from fastapi import FastAPI, HTTPException, BackgroundTasks
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
cur = conn.cursor()

# Creazione tabelle aggiornate
cur.execute('''CREATE TABLE IF NOT EXISTS users
               (id TEXT PRIMARY KEY, bot_token TEXT, chat_id TEXT, check_interval INTEGER DEFAULT 5, created_at TEXT)''')

cur.execute('''CREATE TABLE IF NOT EXISTS watchlist 
               (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT)''')
conn.commit()
print("✅ Database inizializzato")

job_lock = threading.Lock()
active_users = {}

# Pydantic Models
class UserSettings(BaseModel):
    user_id: str
    bot_token: str
    chat_id: str
    check_interval: int  # in minuti: 5, 10, 15, 30, 60

class WatchItem(BaseModel):
    user_id: str
    card_url: str

class MassImportItem(BaseModel):
    user_id: str
    urls: list[str]

# --- FUNZIONI DI UTILITA' ---
def parse_prezzo(prezzo_str):
    if not prezzo_str or prezzo_str == "N/D":
        return None
    try:
        pulito = prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip()
        return float(pulito)
    except:
        return None

def send_telegram_message(user_id, testo):
    cur = conn.cursor()
    cur.execute("SELECT bot_token, chat_id FROM users WHERE id=?", (user_id,))
    user = cur.fetchone()
    if not user or not user[0] or not user[1]:
        print(f"⚠️ Dati Telegram mancanti per {user_id}")
        return

    bot_token, chat_id = user[0], user[1]
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": testo}
    try:
        std_requests.post(url, json=payload, timeout=10)
        print(f"📨 Telegram inviato a {user_id}")
    except Exception as e:
        print(f"❌ Errore Telegram per {user_id}: {e}")

# --- SCRAPING CORE ---
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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        }
    ]

    for attempt in range(max_retries):
        try:
            identity = identities[0] if attempt == 0 else identities[min(attempt, len(identities) - 1)]

            micro_wait = random.uniform(1.2, 3.2)
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
                    return prezzo

        except Exception as e:
            print(f"❌ Errore al tentativo {attempt + 1}: {e}")

        time.sleep(random.uniform(4.5, 8.5))

    return None

# --- ENDPOINTS ---
@app.get("/ping/{user_id}")
async def ping_user(user_id: str):
    active_users[user_id] = time.time()
    return {"status": "ok"}

@app.post("/users/settings")
async def save_settings(settings: UserSettings):
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO users (id, bot_token, chat_id, check_interval, created_at) VALUES (?, ?, ?, ?, ?)",
                (settings.user_id, settings.bot_token, settings.chat_id, settings.check_interval, datetime.now().isoformat()))
    conn.commit()

    # Invio messaggio di benvenuto per testare i dati
    send_telegram_message(settings.user_id, "🤖 Benvenuto in Price Bot Pokemon! Impostazioni salvate correttamente.")
    return {"status": "saved"}

@app.get("/users/{user_id}/settings")
async def get_settings(user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT bot_token, chat_id, check_interval FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if row:
        return {"bot_token": row[0], "chat_id": row[1], "check_interval": row[2]}
    return {"bot_token": "", "chat_id": "", "check_interval": 5}

# --- AGGIUNTA SINGOLA CON DELAY ---
@app.post("/watch")
async def add_watch(item: WatchItem):
    clean_url = item.card_url.split('?')[0]
    final_url = f"{clean_url}?language=5&minCondition=2"

    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="URL non valido")

    active_users[item.user_id] = time.time()
    current_price = scrape_price(final_url)
    nome = final_url.split('/')[-1].split('?')[0].replace('-', ' ')

    if current_price is None:
        raise HTTPException(status_code=400, detail="Impossibile estrarre il prezzo. Carta non salvata.")

    cur = conn.cursor()
    cur.execute("INSERT INTO watchlist (user_id, url, last_price, created_at) VALUES (?, ?, ?, ?)",
                (item.user_id, final_url, current_price, datetime.now().isoformat()))
    conn.commit()

    send_telegram_message(item.user_id, f"✅ {nome} aggiunta!\n💰 Prezzo iniziale: {current_price}€")

    # Delay backend per evitare blocchi su aggiunte rapide
    time.sleep(random.uniform(2.5, 5.0))
    return {"status": "aggiunta", "id": cur.lastrowid, "prezzo": current_price}

# --- IMPORT MASSIVO CON CODA BACKGROUND ---
def process_mass_import(user_id: str, urls: list[str]):
    print(f"🚀 Inizio import massivo di {len(urls)} carte per {user_id}")
    success_count = 0
    for url in urls:
        clean_url = url.split('?')[0]
        final_url = f"{clean_url}?language=5&minCondition=2"
        if "cardmarket.com" not in final_url:
            continue

        nome = final_url.split('/')[-1].split('?')[0].replace('-', ' ')
        price = scrape_price(final_url)

        if price is not None:
            cur = conn.cursor()
            # Evita duplicati esatti
            cur.execute("SELECT id FROM watchlist WHERE user_id=? AND url=?", (user_id, final_url))
            if not cur.fetchone():
                cur.execute("INSERT INTO watchlist (user_id, url, last_price, created_at) VALUES (?, ?, ?, ?)",
                            (user_id, final_url, price, datetime.now().isoformat()))
                conn.commit()
                success_count += 1

        # Pausa lunga tra le carte per non farsi bannare
        time.sleep(random.uniform(8.0, 15.0))

    send_telegram_message(user_id, f"📦 Import completato! Aggiunte {success_count}/{len(urls)} carte al tracciamento.")

@app.post("/watch/mass")
async def add_mass_watch(item: MassImportItem, background_tasks: BackgroundTasks):
    active_users[item.user_id] = time.time()
    background_tasks.add_task(process_mass_import, item.user_id, item.urls)
    return {"status": "processing", "message": f"Importazione di {len(item.urls)} carte avviata in background."}

@app.get("/watchlist/{user_id}")
async def get_watchlist(user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT id, url, last_price FROM watchlist WHERE user_id=?", (user_id,))
    return [{"id": row[0], "nome": row[1].split('/')[-1].split('?')[0].replace('-', ' '), "url": row[1], "last_price": row[2]} for row in cur.fetchall()]

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

# --- JOB SCHEDULATO CON INTERVALLO UTENTE ---
def job_check_prices():
    if not job_lock.acquire(blocking=False):
        return

    try:
        cur = conn.cursor()
        # Prende tutti gli utenti attivi e le loro impostazioni
        cur.execute("SELECT id, check_interval FROM users")
        users = cur.fetchall()

        current_minute = datetime.now().minute

        for user_row in users:
            user_id = user_row[0]
            interval = user_row[1] or 5

            # Se il minuto attuale non è multiplo dell'intervallo dell'utente, salta questo utente
            if current_minute % interval != 0:
                continue

            last_seen = active_users.get(user_id, 0)
            if time.time() - last_seen > 120:
                continue # Utente offline, saltiamo per risparmiare risorse

            cur.execute("SELECT id, url, last_price FROM watchlist WHERE user_id=?", (user_id,))
            cards = cur.fetchall()

            for card in cards:
                watch_id, url, old_price = card
                nome = url.split('/')[-1].split('?')[0].replace('-', ' ')

                new_price = scrape_price(url)
                if new_price is not None:
                    if old_price is None or new_price != old_price:
                        msg = f"🚨 AGGIORNAMENTO PREZZO!\n🃏 {nome}\n💶 Nuovo prezzo: {new_price}€ (era {old_price}€)\n🔗 {url}"
                        send_telegram_message(user_id, msg)

                        cur_update = conn.cursor()
                        cur_update.execute("UPDATE watchlist SET last_price=? WHERE id=?", (new_price, watch_id))
                        conn.commit()

                # Pausa tra una carta e l'altra (cruciale)
                time.sleep(random.uniform(12.0, 22.0))

    finally:
        job_lock.release()

def run_scheduler():
    # Il job gira ogni minuto, ma processa solo gli utenti il cui "check_interval" combacia col minuto attuale
    schedule.every(1).minutes.do(job_check_prices)
    while True:
        schedule.run_pending()
        time.sleep(10)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
