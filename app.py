from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import schedule
import time
import random
import threading
import os

import requests as std_requests
from curl_cffi import requests as cffi_requests

app = FastAPI(title="🃏 Price Bot API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

DB_PATH = os.getenv("DB_PATH", "./watchlist_v4.db")
print(f"📁 DB_PATH impostato su: {DB_PATH}")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT)''')
conn.commit()
print("✅ Database inizializzato")

BOT_TOKEN = "8470410976:AAEJDujquJMbNVHy48Js6dJw6O6qmf3QJds"
CHAT_ID = "393014146"

job_lock = threading.Lock()
active_users = {}

class WatchItem(BaseModel):
    user_id: str
    card_url: str

def parse_prezzo(prezzo_str):
    if not prezzo_str or prezzo_str == "N/D":
        return None
    try:
        pulito = prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip()
        return float(pulito)
    except:
        return None

def send_telegram_message(testo):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": testo}
    try:
        std_requests.post(url, json=payload, timeout=10)
        print("📨 Telegram inviato")
    except Exception as e:
        print(f"❌ Errore Telegram: {e}")

@app.get("/ping/{user_id}")
async def ping_user(user_id: str):
    active_users[user_id] = time.time()
    return {"status": "ok", "user": user_id}

def scrape_price(url, max_retries=3):
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
                "Sec-Ch-Ua": "\"Google Chrome\";v=\"120\", \"Not:A-Brand\";v=\"8\", \"Chromium\";v=\"120\"",
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": "\"Windows\"",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        }
    ]

    for attempt in range(max_retries):
        try:
            if attempt == 0:
                identity = identities[0]
            else:
                identity = identities[min(attempt, len(identities) - 1)]

            micro_wait = random.uniform(1.2, 3.2)
            print(f"⏳ Micro-pausa pre-request: {micro_wait:.1f}s")
            time.sleep(micro_wait)

            cache_buster = random.randint(1000000, 9999999)
            separator = "&" if "?" in url else "?"
            url_busted = f"{url}{separator}nocache={cache_buster}"

            response = cffi_requests.get(
                url_busted,
                impersonate=identity["impersonate"],
                headers=identity["headers"],
                timeout=18
            )

            print(f"🌐 [{identity['name']}] tentativo {attempt + 1}/{max_retries}: status {response.status_code}")

            if response.status_code == 403:
                print(f"❌ 403 con identità {identity['name']}")
                time.sleep(random.uniform(4.0, 7.0))
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")

            if not prezzo_tag:
                tabelle = soup.select("dd.col-6.col-xl-7")
                for tag in tabelle:
                    txt = tag.get_text(" ", strip=True)
                    if "€" in txt:
                        prezzo_tag = tag
                        break

            if prezzo_tag:
                prezzo = parse_prezzo(prezzo_tag.get_text(strip=True))
                if prezzo is not None:
                    print(f"✅ PREZZO TROVATO con {identity['name']}: {prezzo}€")
                    return prezzo

            print(f"⚠️ Prezzo non trovato con {identity['name']} al tentativo {attempt + 1}")

        except Exception as e:
            print(f"❌ Errore al tentativo {attempt + 1}: {e}")

        retry_wait = random.uniform(4.5, 8.5)
        print(f"⏳ Attesa retry: {retry_wait:.1f}s")
        time.sleep(retry_wait)

    return None

@app.post("/watch")
async def add_watch(item: WatchItem):
    clean_url = item.card_url.split('?')[0]
    final_url = f"{clean_url}?language=5&minCondition=2"

    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="URL non valido")

    active_users[item.user_id] = time.time()

    print(f"📥 Nuova carta da aggiungere: {final_url}")
    current_price = scrape_price(final_url)

    nome = final_url.split('/')[-1].split('?')[0].replace('-', ' ')

    if current_price is None:
        print(f"❌ Impossibile estrarre il prezzo per {nome}. Nessun salvataggio nel DB.")
        raise HTTPException(
            status_code=400,
            detail="Blocco di sicurezza da parte di Cardmarket. Nessun prezzo trovato, la carta non è stata salvata."
        )

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO watchlist (user_id, url, last_price, created_at) VALUES (?, ?, ?, ?)",
        (item.user_id, final_url, current_price, datetime.now().isoformat())
    )
    conn.commit()

    send_telegram_message(f"✅ {nome} aggiunta!\n💰 Prezzo iniziale: {current_price}€")
    return {"status": "aggiunta", "id": cur.lastrowid, "prezzo": current_price}

@app.get("/watchlist/{user_id}")
async def get_watchlist(user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT id, url, last_price FROM watchlist WHERE user_id=?", (user_id,))
    return [
        {
            "id": row[0],
            "nome": row[1].split('/')[-1].split('?')[0].replace('-', ' '),
            "url": row[1],
            "last_price": row[2]
        }
        for row in cur.fetchall()
    ]

@app.delete("/watch/{watch_id}")
async def delete_watch(watch_id: int):
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlist WHERE id=?", (watch_id,))
    conn.commit()
    return {"status": "eliminata"}

@app.delete("/watchlist/{user_id}/clear")
async def clear_watchlist(user_id: str):
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlist WHERE user_id=?", (user_id,))
    conn.commit()
    return {"status": "svuotata"}

def job_check_prices():
    if not job_lock.acquire(blocking=False):
        print("⏭️ Job saltato: esecuzione precedente ancora in corso")
        return

    try:
        print("🔍 Controllo prezzi in corso...")
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, url, last_price FROM watchlist")
        rows = cur.fetchall()

        if not rows:
            print("📭 Nessuna carta in watchlist")
            return

        for row in rows:
            watch_id, user_id, url, old_price = row

            last_seen = active_users.get(user_id, 0)
            if time.time() - last_seen > 120:
                print(f"😴 Utente offline ({user_id}). Salto aggiornamento per id={watch_id}")
                continue

            nome = url.split('/')[-1].split('?')[0].replace('-', ' ')
            print(f"🃏 Controllo carta id={watch_id} - {nome}")

            new_price = scrape_price(url)

            if new_price is not None:
                if old_price is None or new_price != old_price:
                    msg = f"🚨 AGGIORNAMENTO PREZZO!\n🃏 {nome}\n💶 Nuovo prezzo: {new_price}€ (era {old_price}€)\n🔗 {url}"
                    send_telegram_message(msg)

                    cur_update = conn.cursor()
                    cur_update.execute("UPDATE watchlist SET last_price=? WHERE id=?", (new_price, watch_id))
                    conn.commit()
                    print(f"✅ Prezzo aggiornato nel DB per {nome}")
                else:
                    print(f"ℹ️ Prezzo invariato per {nome}")
            else:
                msg = f"⚠️ TEST JOB FALLITO\n🃏 {nome}\n❌ Prezzo non trovato\n🔗 {url}"
                send_telegram_message(msg)
                print(f"⚠️ Bot bloccato o prezzo non trovato su {url}")

            attesa_umana = random.uniform(12.0, 22.0)
            print(f"⏳ Pausa tra carte: {attesa_umana:.1f}s")
            time.sleep(attesa_umana)

    finally:
        job_lock.release()

def run_scheduler():
    schedule.every(5).minutes.do(job_check_prices)
    print("⏰ Scheduler avviato: controllo ogni 5 minuti (solo per utenti con app aperta)")
    while True:
        schedule.run_pending()
        time.sleep(10)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
