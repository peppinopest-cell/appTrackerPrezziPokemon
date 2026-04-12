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

# DB PERSISTENTE: ./ = cartella corrente, non /tmp che si cancella!
DB_PATH = os.getenv("DB_PATH", "./watchlist_v4.db")
print(f"📁 DB_PATH impostato su: {DB_PATH}")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''
    CREATE TABLE IF NOT EXISTS watchlist
    (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT)
''')
conn.commit()
print("✅ Database inizializzato")

BOT_TOKEN = "8470410976:AAEJDujquJMbNVHy48Js6dJw6O6qmf3QJds"
CHAT_ID = "393014146"

job_lock = threading.Lock()


class WatchItem(BaseModel):
    user_id: str
    card_url: str


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
        print("📨 Telegram OK")
    except Exception as e:
        print(f"❌ Errore Telegram: {e}")


def scrape_price(url, max_retries=3):
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    for attempt in range(max_retries):
        try:
            cache_buster = random.randint(1000000, 9999999)
            separator = "&" if "?" in url else "?"
            url_busted = f"{url}{separator}nocache={cache_buster}"

            with cffi_requests.Session(impersonate="chrome120") as session:
                response = session.get(url_busted, headers=headers, timeout=12)

            print(f"🌐 Scraping tentativo {attempt + 1}/{max_retries}: status {response.status_code}")

            soup = BeautifulSoup(response.text, "html.parser")

            prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
            if not prezzo_tag:
                tabelle = soup.select("dd.col-6.col-xl-7")
                for tag in tabelle:
                    if "€" in tag.text:
                        prezzo_tag = tag
                        break

            if prezzo_tag:
                prezzo = parse_prezzo(prezzo_tag
