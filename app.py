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
import gc

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

DB_PATH = os.getenv("DB_PATH", "/tmp/watchlist_v4.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute(
    '''
    CREATE TABLE IF NOT EXISTS watchlist
    (
        id INTEGER PRIMARY KEY,
        user_id TEXT,
        url TEXT,
        last_price REAL,
        created_at TEXT
    )
    '''
)
conn.commit()

BOT_TOKEN = "8470410976:AAEJDujquJMbNVHy48Js6dJw6O6qmf3QJds"
CHAT_ID = "393014146"


class WatchItem(BaseModel):
    user_id: str
    card_url: str


def clear_runtime_cache():
    print("🧹 Svuoto cache/runtime...")
    gc.collect()


@app.on_event("startup")
def startup_clear_cache():
    clear_runtime_cache()
    print("🚀 Avvio backend: cache/runtime svuotata.")


def parse_prezzo(prezzo_str):
    if not prezzo_str or prezzo_str == "N/D":
        return None
    try:
        pulito = prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip()
        return float(pulito)
    except Exception:
        return None


def send_telegram_message(testo):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": testo}
    try:
        std_requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Errore Telegram: {e}")


def scrape_price(url):
    clear_runtime_cache()

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    try:
        cache_buster = random.randint(1000000, 9999999)
        separator = "&" if "?" in url else "?"
        url_busted = f"{url}{separator}nocache={cache_buster}"

        with cffi_requests.Session(impersonate="chrome131") as session:
            response = session.get(url_busted, headers=headers, timeout=12)

        print(f"Status: {response.status_code}")

        soup = BeautifulSoup(response.text, "html.parser")

        prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
        if not prezzo_tag:
            tabelle = soup.select("dd.col-6.col-xl-7")
            for tag in tabelle:
                if "€" in tag.text:
                    prezzo_tag = tag
                    break

        if prezzo_tag:
            prezzo = parse_prezzo(prezzo_tag.get_text(strip=True))
            print(f"✅ PREZZO TROVATO: {prezzo}€")
            clear_runtime_cache()
            return prezzo

        print("❌ Nessun prezzo trovato nell'HTML")
        clear_runtime_cache()
        return None

    except Exception as e:
        print(f"❌ Errore scrape: {e}")
        clear_runtime_cache()
        return None


@app.post("/watch")
async def add_watch(item: WatchItem):
    clear_runtime_cache()

    clean_url = item.card_url.split("?")[0]
    final_url = f"{clean_url}?language=5&minCondition=2"

    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="URL non valido")

    current_price = scrape_price(final_url)

    nome = final_url.split("/")[-1].split("?")[0].replace("-", " ")

    if current_price is None:
        print(f"❌ Impossibile estrarre il prezzo per {nome}. Nessun salvataggio nel DB.")
        clear_runtime_cache()
        raise HTTPException(
            status_code=400,
            detail="Prezzo non trovato al primo tentativo. Carta non salvata."
        )

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO watchlist (user_id, url, last_price, created_at) VALUES (?, ?, ?, ?)",
        (item.user_id, final_url, current_price, datetime.now().isoformat())
    )
    conn.commit()

    send_telegram_message(f"✅ {nome} aggiunta!\n💰 Prezzo iniziale: {current_price}€")

    clear_runtime_cache()
    return {"status": "aggiunta", "id": cur.lastrowid, "prezzo": current_price}


@app.get("/watchlist/{user_id}")
async def get_watchlist(user_id: str):
    clear_runtime_cache()
    cur = conn.cursor()
    cur.execute("SELECT id, url, last_price FROM watchlist WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    clear_runtime_cache()
    return [
        {
            "id": row[0],
            "nome": row[1].split("/")[-1].split("?")[0].replace("-", " "),
            "url": row[1],
            "last_price": row[2]
        }
        for row in rows
    ]


@app.delete("/watch/{watch_id}")
async def delete_watch(watch_id: int):
    clear_runtime_cache()
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlist WHERE id=?", (watch_id,))
    conn.commit()
    clear_runtime_cache()
    return {"status": "eliminata"}


@app.delete("/watchlist/{user_id}/clear")
async def clear_watchlist(user_id: str):
    clear_runtime_cache()
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlist WHERE user_id=?", (user_id,))
    conn.commit()
    clear_runtime_cache()
    return {"status": "svuotata"}


def job_check_prices():
    print("🔍 Controllo prezzi in corso...")
    clear_runtime_cache()

    cur = conn.cursor()
    cur.execute("SELECT id, url, last_price FROM watchlist")
    rows = cur.fetchall()

    for row in rows:
        watch_id, url, old_price = row

        clear_runtime_cache()
        new_price = scrape_price(url)

        if new_price is None:
            print(f"❌ Prezzo non trovato per {url}. Nessun update.")
            clear_runtime_cache()
            continue

        nome = url.split("/")[-1].split("?")[0].replace("-", " ")

        if old_price is None or new_price != old_price:
            msg = f"🚨 AGGIORNAMENTO PREZZO!\n🃏 {nome}\n💶 Nuovo prezzo: {new_price}€ (era {old_price}€)\n🔗 {url}"
            send_telegram_message(msg)

            cur_update = conn.cursor()
            cur_update.execute("UPDATE watchlist SET last_price=? WHERE id=?", (new_price, watch_id))
            conn.commit()

        clear_runtime_cache()


def run_scheduler():
    schedule.every(1).minutes.do(job_check_prices)
    while True:
        schedule.run_pending()
        time.sleep(10)


threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    clear_runtime_cache()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
