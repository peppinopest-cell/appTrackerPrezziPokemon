import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

os.environ["RUN_WORKER"] = "0"
import app as backend
from fastapi.testclient import TestClient
from pricing import parse_offers, filtered_url, money_cents, product_url, CardmarketReader, scrape_card_data
from catalog import card_options

URL = "https://www.cardmarket.com/en/Pokemon/Products?idProduct=483559"


def offer(price="12,50 €", lang="Italian", grade="NM", extra=""):
    return f'''<div class="row article-row"><div class="product-attributes">
      <a class="article-condition"><span class="badge">{grade}</span></a>
      <span class="icon" aria-label="{lang}"></span>{extra}</div>
      <div class="price-container"><span class="color-primary">{price}</span></div></div>'''


class ParserTests(unittest.TestCase):
    def test_exact_language_condition_and_no_generic_fallback(self):
        html = offer("1,00 €", "English") + offer("2,00 €", grade="MT") + offer("9,00 €") + offer("7,50 €")
        result = parse_offers(html, "it", "NM", "normal")
        self.assertEqual(result["price_cents"], 750)
        self.assertEqual(result["matched_offers"], 2)
        self.assertEqual(parse_offers('<dd class="col-6 col-xl-7">0,01 €</dd>', "it", "NM", "normal")["status"], "unverified")

    def test_reverse_signed_first_edition_and_unknown_language(self):
        reverse = '<span class="icon" title="Reverse Holo"></span>'
        html = offer("2,00 €", extra=reverse) + offer("1,00 €", extra='<span class="icon" title="Signed"></span>') + offer("5,00 €")
        self.assertEqual(parse_offers(html, "it", "NM", "reverse")["price_cents"], 200)
        self.assertEqual(parse_offers(html, "it", "NM", "normal")["price_cents"], 500)
        self.assertEqual(parse_offers(offer(lang="Unknown"), "it", "NM", "normal")["status"], "no_match")
        self.assertEqual(parse_offers(offer(extra='<span class="icon" title="First Edition"></span>'), "it", "NM", "normal")["status"], "no_match")

    def test_challenge_and_money(self):
        self.assertEqual(parse_offers('<title>Just a moment...</title>', "it", "NM", "normal")["status"], "blocked")
        self.assertEqual(money_cents("1.234,56 €"), 123456)
        self.assertEqual(money_cents("0,00 €"), 0)
        self.assertIsNone(money_cents("12,50 € shipping 3,00 €"))

    def test_urls_strict_and_deduplicated(self):
        with self.assertRaises(ValueError):
            product_url("https://cardmarket.com.evil.example/en/Pokemon/Products?idProduct=1")
        with self.assertRaises(ValueError):
            product_url("https://www.cardmarket.com/en/Pokemon/Products?idProduct=oops")
        self.assertEqual(filtered_url(URL+"&nocache=22&language=1", "it", "NM", "reverse"), filtered_url(URL, "it", "NM", "reverse"))
        self.assertIn("isReverseHolo=Y", filtered_url(URL, "it", "NM", "reverse"))

    def test_block_does_not_retry(self):
        reader = CardmarketReader()
        with patch.object(reader.session, "get", return_value=Mock(status_code=403, headers={})) as get:
            self.assertEqual(reader.fetch(URL, "it", "NM", "normal")["status"], "blocked")
            self.assertEqual(get.call_count, 1)

    def test_scrape_card_data_delegates_to_the_shared_reader(self):
        reader = Mock()
        reader.fetch.return_value = {"status": "ok", "price_cents": 450}
        self.assertEqual(scrape_card_data(URL, "it", "NM", "reverse", client=reader)["price_cents"], 450)
        reader.fetch.assert_called_once_with(URL, "it", "NM", "reverse")

    def test_ambiguous_mapping_is_not_guessed(self):
        card = {"variants_detailed": [{"type": "normal", "thirdParty": {"cardmarket": 42}}, {"type": "holo", "thirdParty": {"cardmarket": 42}}]}
        self.assertEqual(card_options(card), [])
        self.assertEqual(card_options({"pricing": {"cardmarket": {"low": 1.23}}}), [])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(backend, "DB_PATH", str(Path(self.tmp.name)/"test.db"))
        self.path_patch.start()
        self.client = TestClient(backend.app)
        self.client.__enter__()
        r = self.client.post('/auth/register', json={"chatid": "test-user", "password": "Password123!", "bottoken": "test-token"})
        self.assertEqual(r.status_code, 200)
        self.headers = {"Authorization": "Bearer "+r.json()["token"]}
        self.user_id = r.json()["userid"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.path_patch.stop()
        self.tmp.cleanup()

    def add(self, **kwargs):
        payload = dict(url=URL, language="it", condition="NM", variant="normal", tracked=True, quantity=2, name="Furret", set_name="Darkness Ablaze", number="136")
        payload.update(kwargs)
        r = self.client.post('/api/entries', headers=self.headers, json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def allow_next(self):
        with backend.db() as c:
            c.execute("UPDATE worker_state SET next_request=0,blocked_until=0,lease_until=0")
            c.execute("UPDATE quotes SET requested=1,next_attempt=0")

    def test_success_change_notification_and_collection_total(self):
        self.add()
        with patch.object(backend.reader, "fetch", return_value={"status": "ok", "price_cents": 1250, "message": "verified"}) as fetch:
            self.assertTrue(backend.worker_step())
            data = self.client.get('/api/entries', headers=self.headers).json()
            self.assertEqual(data['total_value'], 25)
            with backend.db() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)
            self.client.post('/api/collection/refresh', headers=self.headers)
            backend.worker_step()
            self.assertEqual(fetch.call_count, 1, "cache and rate gate must not be bypassed")
        self.allow_next()
        with patch.object(backend.reader, "fetch", return_value={"status": "ok", "price_cents": 1000, "message": "verified"}):
            backend.worker_step()
        with backend.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)
            self.assertIn("12.50 € → 10.00 €", c.execute("SELECT message FROM outbox").fetchone()[0])
        self.assertEqual(self.client.get('/api/entries', headers=self.headers).json()['total_value'], 20)

    def test_block_global_persistent_cooldown_preserves_last_price(self):
        self.add()
        with patch.object(backend.reader, "fetch", return_value={"status": "ok", "price_cents": 900, "message": "verified"}):
            backend.worker_step()
        self.allow_next()
        self.add(language="en")
        with patch.object(backend.reader, "fetch", return_value={"status": "blocked", "retry_after": "7200", "message": "blocked"}) as fetch:
            backend.worker_step()
            self.assertFalse(backend.worker_step())
            self.assertEqual(fetch.call_count, 1)
        with backend.db() as c:
            self.assertGreater(c.execute("SELECT blocked_until FROM worker_state").fetchone()[0], time.time()+7190)
            self.assertEqual(c.execute("SELECT price_cents FROM quotes WHERE language='it'").fetchone()[0], 900)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)
        backend.init_db()
        self.assertFalse(backend.worker_step(), "restart must not clear cooldown")

    def test_no_match_never_sets_zero_or_sends_price_change(self):
        self.add()
        with patch.object(backend.reader, "fetch", return_value={"status": "ok", "price_cents": 900, "message": "verified"}):
            backend.worker_step()
        self.allow_next()
        with patch.object(backend.reader, "fetch", return_value={"status": "no_match", "message": "no matching offer"}):
            backend.worker_step()
        data = self.client.get('/api/entries', headers=self.headers).json()
        self.assertEqual(data['entries'][0]['quote']['price'], 9)
        self.assertEqual(data['stale_prices'], 1)
        with backend.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)

    def test_shared_cache_across_users_and_no_notifications_for_owned_only(self):
        self.add(tracked=False)
        token2 = self.client.post('/auth/register', json={"chatid": "second", "password": "Password123!"}).json()['token']
        r = self.client.post('/api/entries', headers={"Authorization": "Bearer "+token2}, json={"url": URL, "quantity": 1, "tracked": False})
        self.assertEqual(r.status_code, 200)
        with backend.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 1)
        for price in (500, 600):
            self.allow_next()
            with patch.object(backend.reader, "fetch", return_value={"status": "ok", "price_cents": price, "message": "verified"}):
                backend.worker_step()
        with backend.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)

    def test_unknown_price_is_not_a_zero_value(self):
        self.add(tracked=False)
        data = self.client.get('/api/entries', headers=self.headers).json()
        self.assertIsNone(data['total_value'])
        self.assertIsNone(data['sets'][0]['value'])
        self.assertEqual(data['missing_prices'], 1)

    def test_catalog_uses_translated_metadata_and_verified_variant_id(self):
        english = {"id": "swsh3-136", "name": "Furret", "localId": "136", "set": {"id": "swsh3", "name": "Darkness Ablaze"},
                   "variants_detailed": [{"type": "reverse", "variantId": "reverse-136", "thirdParty": {"cardmarket": 483559}}]}
        italian = {"id": "swsh3-136", "name": "Furret", "localId": "136", "set": {"id": "swsh3", "name": "Fiamme Oscure"}}
        with patch.object(backend, 'catalog_data', side_effect=lambda lang, *args: english if lang == 'en' else italian):
            options = backend.one_card('it', 'swsh3-136', {})['options']
            self.assertEqual(options[0]['product_id'], 483559)
            data = backend.Selection(catalog_id='swsh3-136', catalog_lang='it', option_id='reverse-136', variant='normal')
            key, url, variant, metadata = backend.resolve_selection(data)
            self.assertEqual(variant, 'reverse', 'the server, not the client, chooses the mapped variant')
            self.assertIn('isReverseHolo=Y', url)
            self.assertEqual(metadata['set_name'], 'Fiamme Oscure')
            self.assertEqual(backend.mapped_options(italian, 'ja'), [])

    def test_ownership_logout_and_invalid_filters(self):
        entry_id = self.add()
        token2 = self.client.post('/auth/register', json={"chatid": "second", "password": "Password123!"}).json()['token']
        self.client.delete(f'/api/entries/{entry_id}', headers={"Authorization": "Bearer "+token2})
        self.assertEqual(len(self.client.get('/api/entries', headers=self.headers).json()['entries']), 1)
        self.assertEqual(self.client.get('/api/entries').status_code, 401)
        self.assertEqual(self.client.post('/api/quotes', headers=self.headers, json={"url": URL, "language": "xx"}).status_code, 422)
        self.client.post('/auth/logout', headers=self.headers)
        self.assertEqual(self.client.get('/api/entries', headers=self.headers).status_code, 401)

    def test_telegram_failure_retries_without_losing_event(self):
        entry_id = self.add()
        with backend.db() as c:
            c.execute("INSERT INTO outbox(user_id,entry_id,message) VALUES (?,?,?)", (self.user_id, entry_id, "Test"))
        response = Mock(ok=False)
        response.json.return_value = {"ok": False, "parameters": {"retry_after": 500}}
        with patch.object(backend.requests, 'post', return_value=response):
            backend.send_outbox()
        with backend.db() as c:
            row = c.execute("SELECT * FROM outbox").fetchone()
            self.assertIsNone(row['sent_at'])
            self.assertEqual(row['attempts'], 1)
            self.assertGreater(row['next_attempt'], time.time()+490)


class MigrationTests(unittest.TestCase):
    def test_original_database_is_preserved_and_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/'old.db')
            with sqlite3.connect(path) as c:
                c.executescript('''CREATE TABLE users(id TEXT PRIMARY KEY,bot_token TEXT,chat_id TEXT,check_interval INTEGER,created_at TEXT,passwordhash TEXT);
                    CREATE TABLE watchlist(id INTEGER PRIMARY KEY,user_id TEXT,url TEXT,last_price REAL,image_url TEXT,condition TEXT,language TEXT);
                    CREATE TABLE collection_cards(id INTEGER PRIMARY KEY,user_id TEXT,url TEXT,quantity INTEGER,card_name TEXT,set_name TEXT,card_number TEXT,last_price REAL);
                    INSERT INTO users VALUES ('old','token','123',5,'2026','hash');''')
                c.execute("INSERT INTO watchlist VALUES (1,'old',?,123,'','NM','🇮🇹')", (URL,))
                c.execute("INSERT INTO collection_cards VALUES (1,'old',?,3,'Furret','Darkness Ablaze','136',123)", (URL,))
            c.close()
            with patch.object(backend, 'DB_PATH', path):
                backend.init_db()
                backend.init_db()
                data = backend.entries_payload('old')
                self.assertEqual(len(data['entries']), 2)
                self.assertEqual(data['quantity'], 3)
                self.assertEqual(data['missing_prices'], 1)
                self.assertTrue(all(e['needs_review'] for e in data['entries']))
                with backend.db() as c:
                    self.assertEqual(c.execute("SELECT last_price FROM watchlist").fetchone()[0], 123)


if __name__ == '__main__':
    unittest.main()
