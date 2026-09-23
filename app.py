"""Price Bot v2. Run a single Uvicorn process with persistent DB_PATH on Render."""
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from catalog import card_options, catalog_lock, fetch_catalog, validate_path
from pricing import CardmarketReader, CONDITIONS, LANGUAGES, filtered_url, product_url
import random
import re
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

log = logging.getLogger("pricebot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
DB_PATH = os.getenv("DB_PATH", "./watchlist_v4.db")
MIN_GAP = max(10, int(os.getenv("PRICE_MIN_GAP_SECONDS", "30")))
CACHE_SECONDS = max(300, int(os.getenv("PRICE_CACHE_SECONDS", "900")))
COLLECTION_INTERVAL = max(3600, int(os.getenv("COLLECTION_INTERVAL_SECONDS", "86400")))
BLOCK_COOLDOWN = max(300, int(os.getenv("BLOCK_COOLDOWN_SECONDS", "1800")))
stop_event = threading.Event()
reader = CardmarketReader()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def stamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, bot_token TEXT, chat_id TEXT,
            check_interval INTEGER DEFAULT 15, created_at TEXT, passwordhash TEXT, updatedat TEXT);
        CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS catalog_cache (path TEXT PRIMARY KEY, body TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS quotes (
            key TEXT PRIMARY KEY, url TEXT NOT NULL, language TEXT NOT NULL, condition TEXT NOT NULL,
            variant TEXT NOT NULL, price_cents INTEGER, checked_at REAL, attempted_at REAL,
            status TEXT NOT NULL DEFAULT 'pending', message TEXT, requested INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, quote_key TEXT, catalog_id TEXT,
            catalog_lang TEXT, option_id TEXT, name TEXT NOT NULL, set_name TEXT NOT NULL DEFAULT '',
            number TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '', url TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'it', condition TEXT NOT NULL DEFAULT 'NM',
            variant TEXT NOT NULL DEFAULT 'normal', quantity INTEGER NOT NULL DEFAULT 0,
            tracked INTEGER NOT NULL DEFAULT 0, needs_review INTEGER NOT NULL DEFAULT 0,
            notified_cents INTEGER, legacy_source TEXT UNIQUE, created_at REAL NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS entries_user_quote ON entries(user_id, quote_key) WHERE quote_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS worker_state (
            id INTEGER PRIMARY KEY CHECK(id=1), blocked_until REAL NOT NULL DEFAULT 0,
            next_request REAL NOT NULL DEFAULT 0, strikes INTEGER NOT NULL DEFAULT 0,
            lease_until REAL NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO worker_state(id) VALUES (1);
        CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL,
            entry_id INTEGER NOT NULL, message TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, sent_at REAL);
        CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
        """)
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for name in ("passwordhash", "updatedat"):
            if name not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {name} TEXT")
        # Leave original tables intact; old prices did not guarantee exact filters.
        if not c.execute("SELECT 1 FROM migrations WHERE name='legacy-v1'").fetchone():
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("watchlist", "collection_cards"):
                if table not in tables:
                    continue
                for raw in c.execute(f"SELECT * FROM {table}").fetchall():
                    r = dict(raw)
                    url = r.get("url") or ""
                    name = r.get("card_name") or url.split("?")[0].split("/")[-1].replace("-", " ")
                    c.execute("""INSERT OR IGNORE INTO entries
                        (user_id,name,set_name,number,image,url,quantity,tracked,needs_review,legacy_source,created_at)
                        VALUES (?,?,?,?,?,?,?,?,1,?,?)""", (r["user_id"], name or "Carta importata", r.get("set_name") or "Importate",
                        r.get("card_number") or "", r.get("image_url") or "", url,
                        max(1, r.get("quantity") or 1) if table == "collection_cards" else 0,
                        int(table == "watchlist"), f"{table}:{r['id']}", time.time()))
            c.execute("INSERT INTO migrations(name) VALUES ('legacy-v1')")


def hash_password(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return f"{salt}${hashed}"


def verify_password(password, stored):
    try:
        salt, old = stored.split("$")
        return hmac.compare_digest(old, hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex())
    except (ValueError, AttributeError):
        return False


def session_for(user_id):
    token = secrets.token_urlsafe(32)
    with db() as c:
        c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        c.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time()+30*86400))
    return {"userid": user_id, "token": token}


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Accedi per continuare.")
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as c:
        user = c.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.digest=? AND s.expires>?", (digest, time.time())).fetchone()
    if not user:
        raise HTTPException(401, "Sessione scaduta. Accedi di nuovo.")
    return dict(user)


def catalog_data(lang, kind, item_id=None):
    path = validate_path(lang, kind, item_id)
    with catalog_lock:
        with db() as c:
            cached = c.execute("SELECT * FROM catalog_cache WHERE path=?", (path,)).fetchone()
        if cached and cached["expires"] > time.time():
            return json.loads(cached["body"])
        try:
            body = fetch_catalog(path)
        except HTTPException as e:
            if cached and e.status_code == 503:
                return json.loads(cached["body"])
            raise
        with db() as c:
            c.execute("INSERT OR REPLACE INTO catalog_cache VALUES (?,?,?)", (path, json.dumps(body), time.time()+86400))
        return body


def mapped_options(card, lang):
    options = card_options(card)
    # TCGdex often only attaches marketplace IDs to English records. Western
    # translations share the same set/card IDs; Japanese sets must never use this fallback.
    if not options and lang in {"it", "fr", "de", "es", "pt"}:
        try:
            english = catalog_data("en", "cards", card["id"])
            if english["set"]["id"] == card["set"]["id"] and str(english["localId"]) == str(card["localId"]):
                options = card_options(english)
        except HTTPException:
            pass
    return options


class Login(BaseModel):
    chatid: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class Register(Login):
    bottoken: str = Field(default="", max_length=200)
    checkinterval: int = Field(default=15, ge=5, le=1440)


class Settings(BaseModel):
    bot_token: str | None = Field(default=None, max_length=200)
    chat_id: str = Field(min_length=1, max_length=80)
    check_interval: int = Field(default=15, ge=5, le=1440)


class Selection(BaseModel):
    catalog_id: str | None = Field(default=None, max_length=100)
    catalog_lang: str = "en"
    option_id: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=2000)
    language: str = "it"
    condition: str = "NM"
    variant: str = "normal"


class EntryInput(Selection):
    quantity: int = Field(default=0, ge=0, le=100000)
    tracked: bool = False
    name: str = Field(default="", max_length=200)
    set_name: str = Field(default="", max_length=200)
    number: str = Field(default="", max_length=30)


def resolve_selection(data):
    metadata = {"name": getattr(data, "name", "") or "Carta Cardmarket", "set_name": getattr(data, "set_name", ""),
                "number": getattr(data, "number", ""), "image": ""}
    base = data.url or ""
    variant = data.variant
    if data.catalog_id:
        card = catalog_data(data.catalog_lang, "cards", data.catalog_id)
        metadata = {"name": card["name"], "set_name": card["set"]["name"], "number": str(card["localId"]),
                    "image": card.get("image", "") + "/high.webp" if card.get("image") else ""}
        if (data.language in {"ja", "ko"} or data.catalog_lang in {"ja", "ko"}) and data.language != data.catalog_lang:
            raise HTTPException(422, "Per carte giapponesi o coreane scegli il catalogo della stessa lingua o collega la stampa manualmente.")
        option = next((o for o in mapped_options(card, data.catalog_lang) if o["id"] == data.option_id), None)
        if not option:
            raise HTTPException(422, "Questa variante non ha un collegamento Cardmarket verificabile nel catalogo.")
        base, variant = option["url"], option["variant"]
    try:
        url = filtered_url(base, data.language, data.condition, variant)
    except ValueError as e:
        raise HTTPException(422, str(e))
    # Includes exact condition and variant even if two finishes share a market URL.
    key = hashlib.sha256(f"{url}|{variant}|exact:{data.condition}".encode()).hexdigest()
    return key, url, variant, metadata


def ensure_quote(c, data, key, url, variant):
    c.execute("INSERT OR IGNORE INTO quotes(key,url,language,condition,variant) VALUES (?,?,?,?,?)",
              (key, url, data.language, data.condition, variant))


def request_quote(c, key):
    c.execute("UPDATE quotes SET requested=1 WHERE key=? AND (checked_at IS NULL OR checked_at<=?)", (key, time.time()-CACHE_SECONDS))


def quote_payload(row, gate=None):
    r = dict(row)
    now = time.time()
    blocked = gate and gate["blocked_until"] > now
    r.update(price=r["price_cents"] / 100 if r["price_cents"] is not None else None,
             updated_at=stamp(r["checked_at"]), attempted_at=stamp(r["attempted_at"]),
             stale=not r["checked_at"] or now-r["checked_at"] >= CACHE_SECONDS or r["status"] != "ok",
             queued=bool(r["requested"]), retry_at=stamp(max(r["next_attempt"], gate["blocked_until"] if gate else 0)))
    if blocked:
        r["status"] = "blocked"
        r["message"] = "Aggiornamenti in pausa: Cardmarket ha limitato l'accesso."
    return r


def entries_payload(user_id):
    with db() as c:
        gate = c.execute("SELECT * FROM worker_state WHERE id=1").fetchone()
        entries = c.execute("SELECT * FROM entries WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        result = []
        total_cents = missing = stale = valued = 0
        sets = {}
        for raw in entries:
            e = dict(raw)
            q = c.execute("SELECT * FROM quotes WHERE key=?", (e["quote_key"],)).fetchone() if e["quote_key"] else None
            e["quote"] = quote_payload(q, gate) if q else None
            value = q["price_cents"] * e["quantity"] if q and q["price_cents"] is not None else None
            e["value"] = value / 100 if value is not None else None
            if e["quantity"]:
                total_cents += value or 0
                missing += int(value is None)
                valued += int(value is not None)
                stale += int(bool(e["quote"] and e["quote"]["stale"] and value is not None))
                name = e["set_name"] or "Carte singole"
                group = sets.setdefault(name, {"name": name, "value_cents": 0, "quantity": 0, "missing": 0, "valued": 0})
                group["value_cents"] += value or 0
                group["quantity"] += e["quantity"]
                group["missing"] += int(value is None)
                group["valued"] += int(value is not None)
            result.append(e)
    return {"entries": result, "total_value": total_cents/100 if valued or not missing else None, "missing_prices": missing,
            "stale_prices": stale, "quantity": sum(e["quantity"] for e in result),
            "sets": [{**s, "value": s["value_cents"]/100 if s["valued"] else None} for s in sets.values()],
            "blocked_until": stamp(gate["blocked_until"]) if gate["blocked_until"] > time.time() else None,
            "cache_seconds": CACHE_SECONDS, "collection_interval_seconds": COLLECTION_INTERVAL}


def backoff_seconds(retry_after, now):
    try:
        return max(0, int(retry_after))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(retry_after).timestamp()-now)
        except (TypeError, ValueError, OverflowError):
            return 0


def schedule_due(c, now):
    # One shared quote for all users and both lists. Polling the UI never schedules jobs.
    c.execute("""UPDATE quotes SET requested=1 WHERE requested=0 AND EXISTS (
        SELECT 1 FROM entries e JOIN users u ON u.id=e.user_id WHERE e.quote_key=quotes.key
        AND e.needs_review=0 AND (e.tracked=1 OR e.quantity>0)
        AND COALESCE(quotes.checked_at,0) + CASE WHEN e.tracked=1
            THEN MAX(?, COALESCE(u.check_interval,15)*60) ELSE ? END <= ?)
        """, (CACHE_SECONDS, COLLECTION_INTERVAL, now))


# --- Scraping legacy recuperato da app.py ---
def parse_prezzo(prezzo_str):
    if not prezzo_str or prezzo_str == "N/D":
        return None

    try:
        pulito = prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip()
        return float(pulito)
    except Exception:
        return None


def scrape_card_data(url, max_retries=3):
    identities = [
        {
            "name": "safari-main",
            "impersonate": "safari15_5",
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Safari/605.1.15"
            }
        },
        {
            "name": "chrome-fallback",
            "impersonate": "chrome120",
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        }
    ]

    last_status = None
    retry_after = None

    for attempt in range(max_retries):
        try:
            identity = identities[0] if attempt == 0 else identities[min(attempt, len(identities) - 1)]

            time.sleep(random.uniform(1.2, 3.2))

            cache_buster = random.randint(1000000, 9999999)
            separator = "&" if "?" in url else "?"
            url_busted = f"{url}{separator}nocache={cache_buster}"

            response = cffi_requests.get(
                url_busted,
                impersonate=identity["impersonate"],
                headers=identity["headers"],
                timeout=18
            )

            last_status = response.status_code

            try:
                retry_after = response.headers.get("Retry-After")
            except Exception:
                retry_after = None

            if response.status_code in (403, 429):
                time.sleep(random.uniform(4.0, 7.0))
                continue

            if response.status_code >= 500:
                time.sleep(random.uniform(4.0, 7.0))
                continue

            html_text = response.text
            soup = BeautifulSoup(html_text, "html.parser")

            lowered = html_text.lower()
            if any(marker in lowered for marker in (
                "captcha",
                "cf-challenge",
                "challenge-platform",
                "attention required",
                "just a moment"
            )):
                return {
                    "status": "blocked",
                    "http_status": response.status_code,
                    "retry_after": retry_after,
                    "price": None
                }

            price = None
            condition = "N/A"
            language = "🌐"
            image_url = ""

            # --- 1. ESTRAZIONE IMMAGINE ---
            img_meta = soup.find("meta", property="og:image")
            if img_meta and img_meta.get("content"):
                image_url = img_meta["content"]

            if not image_url:
                img_tag = soup.select_one(".image-container img, .product-image img, .card-image img")
                if img_tag:
                    image_url = img_tag.get("src") or img_tag.get("data-src") or ""

            if not image_url:
                match = re.search(r'https?://[^"]+/img/[^"]+/Products/[^"]+\.(?:jpg|png)', html_text)
                if match:
                    image_url = match.group(0)

            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                image_url = "https://www.cardmarket.com" + image_url

            # --- 2. ESTRAZIONE TABELLA PREZZO / LINGUA / CONDIZIONE ---
            first_row = soup.select_one("div.row.article-row")

            if first_row:
                price_tag = first_row.select_one(
                    ".price-container .color-primary, "
                    ".color-primary.small, "
                    "span.fw-bold, "
                    ".font-weight-bold.color-primary"
                )

                if price_tag:
                    price = parse_prezzo(price_tag.get_text(strip=True))

                cond_tag = first_row.select_one("a.article-condition span.badge")
                if cond_tag:
                    condition = cond_tag.get_text(strip=True)

                lang_tag = first_row.select_one(
                    "span.icon[aria-label], "
                    "span.icon[data-original-title], "
                    "span.icon[onmouseover]"
                )

                if lang_tag:
                    lang_text = lang_tag.get("aria-label") or lang_tag.get("data-original-title") or ""

                    if not lang_text and lang_tag.get("onmouseover"):
                        match = re.search(r"showMsgBox\(this,`([^`]+)`\)", lang_tag.get("onmouseover"))
                        if match:
                            lang_text = match.group(1)

                    lang_map = {
                        "Inglese": "🇬🇧",
                        "Italiano": "🇮🇹",
                        "Francese": "🇫🇷",
                        "Tedesco": "🇩🇪",
                        "Spagnolo": "🇪🇸",
                        "Portoghese": "🇵🇹",
                        "Giapponese": "🇯🇵",
                        "Coreano": "🇰🇷",
                        "Cinese": "🇨🇳"
                    }

                    for k, v in lang_map.items():
                        if k.lower() in lang_text.lower():
                            language = v
                            break

            # --- 3. FALLBACK PREZZO ---
            if price is None:
                prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")

                if not prezzo_tag:
                    tabelle = soup.select("dd.col-6.col-xl-7")
                    for tag in tabelle:
                        if "€" in tag.get_text(" ", strip=True):
                            prezzo_tag = tag
                            break

                if prezzo_tag:
                    price = parse_prezzo(prezzo_tag.get_text(strip=True))

            if price is not None:
                return {
                    "status": "ok",
                    "price": price,
                    "image": image_url,
                    "condition": condition,
                    "language": language,
                    "http_status": response.status_code
                }

        except Exception:
            pass

        time.sleep(random.uniform(4.5, 8.5))

    if last_status in (403, 429):
        return {
            "status": "blocked",
            "http_status": last_status,
            "retry_after": retry_after,
            "price": None
        }

    return {
        "status": "error",
        "http_status": last_status,
        "price": None
    }


def fetch_price_with_cffi(url, language, condition, variant):
    """
    Wrapper compatibile con il worker di app2.py.

    app2.py si aspetta:
    - status: ok / blocked / error
    - price_cents: int, se status == ok
    - message: str
    - retry_after e http_status opzionali se blocked
    """
    scraped = scrape_card_data(url)

    if scraped.get("status") == "blocked":
        return {
            "status": "blocked",
            "message": "Cardmarket ha limitato o bloccato la richiesta.",
            "http_status": scraped.get("http_status"),
            "retry_after": scraped.get("retry_after")
        }

    if scraped.get("status") != "ok" or scraped.get("price") is None:
        return {
            "status": "error",
            "message": "Recupero non riuscito. Nuovo tentativo programmato.",
            "http_status": scraped.get("http_status")
        }

    price = float(scraped["price"])

    return {
        "status": "ok",
        "price_cents": int(round(price * 100)),
        "message": f"Prezzo trovato: {price:.2f} €",
        "http_status": scraped.get("http_status")
    }


def worker_step():
    now = time.time()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        schedule_due(c, now)
        state = c.execute("SELECT * FROM worker_state WHERE id=1").fetchone()
        if max(state["blocked_until"], state["next_request"], state["lease_until"]) > now:
            return False
        quote = c.execute("SELECT * FROM quotes WHERE requested=1 AND next_attempt<=? ORDER BY COALESCE(attempted_at,0),key LIMIT 1", (now,)).fetchone()
        if not quote:
            return False
        c.execute("UPDATE worker_state SET lease_until=?,next_request=? WHERE id=1", (now+180, now+MIN_GAP))
    q = dict(quote)
    
    try:
        result = fetch_price_with_cffi(q["url"], q["language"], q["condition"], q["variant"])
    except Exception as exc:
        # Do not log request URLs with credentials or raw page contents.
        log.warning("Price fetch failed (%s), quote %s", type(exc).__name__, q["key"][:10])
        result = {"status": "error", "message": "Recupero non riuscito. Nuovo tentativo programmato."}
        
    now = time.time()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        state = c.execute("SELECT * FROM worker_state WHERE id=1").fetchone()
        if result["status"] == "ok":
            cents = result["price_cents"]
            c.execute("UPDATE quotes SET price_cents=?,checked_at=?,attempted_at=?,status='ok',message=?,requested=0,next_attempt=?,failures=0 WHERE key=?",
                      (cents, now, now, result["message"], now+CACHE_SECONDS, q["key"]))
            c.execute("UPDATE worker_state SET strikes=0,blocked_until=0 WHERE id=1")
            for e in c.execute("SELECT * FROM entries WHERE quote_key=? AND tracked=1", (q["key"],)).fetchall():
                if e["notified_cents"] is not None and e["notified_cents"] != cents:
                    msg = (f"Cambio prezzo · {e['name']} #{e['number']}\n{e['set_name']}\n"
                           f"{LANGUAGES[e['language']][1]} · {e['condition']} esatta · {e['variant']}\n"
                           f"{e['notified_cents']/100:.2f} € → {cents/100:.2f} €\n"
                           f"Minimo verificato nella pagina, spedizione esclusa.\n{q['url']}")
                    c.execute("INSERT INTO outbox(user_id,entry_id,message) VALUES (?,?,?)", (e["user_id"], e["id"], msg))
                c.execute("UPDATE entries SET notified_cents=? WHERE id=?", (cents, e["id"]))
        else:
            delay = min(86400, CACHE_SECONDS * 2**min(q["failures"], 6))
            if result["status"] == "blocked":
                delay = max(min(86400, BLOCK_COOLDOWN * 2**min(state["strikes"], 6)), backoff_seconds(result.get("retry_after"), now))
                c.execute("UPDATE worker_state SET blocked_until=?,strikes=strikes+1 WHERE id=1", (now+delay,))
                log.warning("Cardmarket paused for %d seconds (HTTP %s)", delay, result.get("http_status", "challenge"))
            c.execute("UPDATE quotes SET status=?,message=?,attempted_at=?,next_attempt=?,failures=failures+1 WHERE key=?",
                      (result["status"], result["message"], now, now+delay, q["key"]))
            # A detail viewed once must not retry forever after the user leaves it.
            c.execute("""UPDATE quotes SET requested=0 WHERE key=? AND NOT EXISTS
                (SELECT 1 FROM entries WHERE quote_key=quotes.key AND (tracked=1 OR quantity>0))""", (q["key"],))
        c.execute("UPDATE worker_state SET lease_until=0,next_request=? WHERE id=1", (now+MIN_GAP,))
    return True


def send_outbox():
    now = time.time()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("""SELECT o.*,u.bot_token,u.chat_id FROM outbox o JOIN users u ON u.id=o.user_id
            JOIN entries e ON e.id=o.entry_id AND e.tracked=1
            WHERE o.sent_at IS NULL AND o.next_attempt<=? AND COALESCE(u.bot_token,'')!=''
            ORDER BY o.id LIMIT 1""", (now,)).fetchone()
        if not row:
            return
        c.execute("UPDATE outbox SET next_attempt=? WHERE id=?", (now+120, row["id"]))
    success = False
    retry = 0
    try:
        response = requests.post(f"https://api.telegram.org/bot{row['bot_token']}/sendMessage",
                                 json={"chat_id": row["chat_id"], "text": row["message"], "disable_web_page_preview": True}, timeout=(5, 15))
        body = response.json()
        success = response.ok and body.get("ok") is True
        retry = int((body.get("parameters") or {}).get("retry_after", 0))
    except (requests.RequestException, ValueError, TypeError):
        log.warning("Telegram delivery failed for event %s", row["id"])
    with db() as c:
        if success:
            c.execute("UPDATE outbox SET sent_at=? WHERE id=?", (time.time(), row["id"]))
        else:
            delay = max(retry, min(86400, 60*2**min(row["attempts"], 10)))
            c.execute("UPDATE outbox SET attempts=attempts+1,next_attempt=? WHERE id=?", (time.time()+delay, row["id"]))


def worker_loop():
    while not stop_event.is_set():
        try:
            worker_step()
            send_outbox()
        except Exception:
            log.exception("Worker iteration failed")
        stop_event.wait(2)


@asynccontextmanager
async def lifespan(app):
    init_db()
    stop_event.clear()
    thread = None
    if os.getenv("RUN_WORKER", "1") == "1":
        thread = threading.Thread(target=worker_loop, daemon=True, name="price-worker")
        thread.start()
    yield
    stop_event.set()
    if thread:
        thread.join(timeout=2)


app = FastAPI(title="Price Bot · Catalogo e collezione", lifespan=lifespan)
origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8081,http://127.0.0.1:8081").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins], allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])


@app.get("/health")
def health():
    return {"status": "ok", "version": 2}


@app.post("/auth/register")
def register(data: Register):
    if len(data.password.strip()) < 8:
        raise HTTPException(422, "Usa una password di almeno 8 caratteri.")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM users WHERE chat_id=?", (data.chatid.strip(),)).fetchone():
            raise HTTPException(409, "Chat ID già registrato.")
        uid = "user_" + uuid.uuid4().hex[:16]
        c.execute("INSERT INTO users(id,bot_token,chat_id,check_interval,created_at,passwordhash) VALUES (?,?,?,?,?,?)",
                  (uid, data.bottoken.strip(), data.chatid.strip(), data.checkinterval, stamp(time.time()), hash_password(data.password.strip())))
    return session_for(uid)


@app.post("/auth/login")
def login(data: Login):
    with db() as c:
        user = c.execute("SELECT * FROM users WHERE chat_id=?", (data.chatid.strip(),)).fetchone()
    if not user or not verify_password(data.password.strip(), user["passwordhash"]):
        raise HTTPException(401, "Chat ID o password non corretti.")
    return session_for(user["id"])


@app.post("/auth/logout")
def logout(user=Depends(current_user), authorization: str = Header()):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(authorization[7:].encode()).hexdigest(),))
    return {"status": "ok"}


@app.get("/api/settings")
def get_settings(user=Depends(current_user)):
    return {"chat_id": user["chat_id"], "has_bot_token": bool(user["bot_token"]), "check_interval": user["check_interval"], "minimum_interval": CACHE_SECONDS//60}


@app.put("/api/settings")
def put_settings(data: Settings, user=Depends(current_user)):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM users WHERE chat_id=? AND id!=?", (data.chat_id.strip(), user["id"])).fetchone():
            raise HTTPException(409, "Questo Chat ID è già associato a un altro account.")
        c.execute("UPDATE users SET bot_token=?,chat_id=?,check_interval=?,updatedat=? WHERE id=?",
                  (data.bot_token.strip() if data.bot_token is not None else user["bot_token"], data.chat_id.strip(), data.check_interval, stamp(time.time()), user["id"]))
    return {"status": "saved"}


@app.get("/api/catalog/{lang}/sets")
def sets(lang: str, user=Depends(current_user)):
    data = catalog_data(lang, "sets")
    # Pocket is a different product line; no Cardmarket singles for the digital game.
    return [s for s in data if not s["id"].startswith(("A", "B", "P-A", "P-B"))]


@app.get("/api/catalog/{lang}/sets/{set_id}")
def one_set(lang: str, set_id: str, user=Depends(current_user)):
    return catalog_data(lang, "sets", set_id)


@app.get("/api/catalog/{lang}/cards/{card_id}")
def one_card(lang: str, card_id: str, user=Depends(current_user)):
    card = catalog_data(lang, "cards", card_id)
    return {"id": card["id"], "name": card["name"], "localId": card["localId"], "image": card.get("image"),
            "set": card["set"], "options": mapped_options(card, lang)}


@app.post("/api/quotes")
def preview(data: Selection, user=Depends(current_user)):
    key, url, variant, metadata = resolve_selection(data)
    with db() as c:
        ensure_quote(c, data, key, url, variant)
        request_quote(c, key)
        q = c.execute("SELECT * FROM quotes WHERE key=?", (key,)).fetchone()
        gate = c.execute("SELECT * FROM worker_state WHERE id=1").fetchone()
    return {**quote_payload(q, gate), **metadata}


@app.get("/api/quotes/{key}")
def get_quote(key: str, user=Depends(current_user)):
    with db() as c:
        q = c.execute("SELECT * FROM quotes WHERE key=?", (key,)).fetchone()
        gate = c.execute("SELECT * FROM worker_state WHERE id=1").fetchone()
    if not q:
        raise HTTPException(404, "Prezzo non ancora richiesto.")
    return quote_payload(q, gate)


@app.get("/api/entries")
def get_entries(user=Depends(current_user)):
    return entries_payload(user["id"])


def save_entry(data, user, entry_id=None):
    if not data.tracked and not data.quantity:
        raise HTTPException(422, "Attiva il tracciamento o indica almeno una copia posseduta.")
    if data.tracked and not user["bot_token"]:
        raise HTTPException(422, "Configura il bot Telegram nelle impostazioni prima di attivare le notifiche.")
    key, url, variant, meta = resolve_selection(data)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT * FROM entries WHERE user_id=? AND quote_key=?", (user["id"], key)).fetchone()
        old = None
        if entry_id:
            old = c.execute("SELECT * FROM entries WHERE id=? AND user_id=?", (entry_id, user["id"])).fetchone()
            if not old:
                raise HTTPException(404, "Carta non trovata.")
            if existing and existing["id"] != entry_id:
                raise HTTPException(409, "Questa carta con gli stessi filtri è già salvata. Modifica quella esistente.")
        elif existing:
            raise HTTPException(409, "Carta già presente con questi filtri. Aprila da Tracciate o Collezione per modificarla.")
        ensure_quote(c, data, key, url, variant)
        request_quote(c, key)
        q = c.execute("SELECT * FROM quotes WHERE key=?", (key,)).fetchone()
        baseline = old["notified_cents"] if old and old["quote_key"] == key and old["tracked"] and data.tracked else q["price_cents"]
        fields = (key, data.catalog_id, data.catalog_lang, data.option_id, meta["name"], meta["set_name"], meta["number"], meta["image"], url,
                  data.language, data.condition, variant, data.quantity, int(data.tracked), baseline)
        if entry_id:
            c.execute("""UPDATE entries SET quote_key=?,catalog_id=?,catalog_lang=?,option_id=?,name=?,set_name=?,number=?,image=?,url=?,
                language=?,condition=?,variant=?,quantity=?,tracked=?,notified_cents=?,needs_review=0 WHERE id=? AND user_id=?""", (*fields, entry_id, user["id"]))
            if not data.tracked or old["quote_key"] != key:
                c.execute("DELETE FROM outbox WHERE entry_id=? AND sent_at IS NULL", (entry_id,))
        else:
            cur = c.execute("""INSERT INTO entries(quote_key,catalog_id,catalog_lang,option_id,name,set_name,number,image,url,
                language,condition,variant,quantity,tracked,notified_cents,user_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*fields, user["id"], time.time()))
            entry_id = cur.lastrowid
    return {"status": "saved", "id": entry_id}


@app.post("/api/entries")
def add_entry(data: EntryInput, user=Depends(current_user)):
    return save_entry(data, user)


@app.put("/api/entries/{entry_id}")
def update_entry(entry_id: int, data: EntryInput, user=Depends(current_user)):
    return save_entry(data, user, entry_id)


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int, user=Depends(current_user)):
    with db() as c:
        c.execute("DELETE FROM outbox WHERE entry_id=? AND user_id=?", (entry_id, user["id"]))
        c.execute("DELETE FROM entries WHERE id=? AND user_id=?", (entry_id, user["id"]))
    return {"status": "deleted"}


@app.post("/api/collection/refresh")
def refresh_collection(user=Depends(current_user)):
    with db() as c:
        for row in c.execute("SELECT quote_key FROM entries WHERE user_id=? AND quantity>0 AND needs_review=0", (user["id"],)).fetchall():
            request_quote(c, row["quote_key"])
    return {"message": "Aggiornamento richiesto. Cache e pause di Cardmarket vengono rispettate."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
