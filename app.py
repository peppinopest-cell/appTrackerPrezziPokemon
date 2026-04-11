from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import schedule
import time
import threading
import os
import requests 
from curl_cffi import requests 

app = FastAPI(title="🃏 Price Bot API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DB_PATH = os.getenv("DB_PATH", "/tmp/watchlist_v4.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT)''')
conn.commit()

# --- I TUOI DATI TELEGRAM ---
BOT_TOKEN = "8470410976:AAEJDujquJMbNVHy48Js6dJw6O6qmf3QJds"
CHAT_ID = "393014146"

class WatchItem(BaseModel):
    user_id: str
    card_url: str

def parse_prezzo(prezzo_str):
    if not prezzo_str or prezzo_str == "N/D": return None
    try: 
        pulito = prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip()
        return float(pulito)
    except: 
        return None

def send_telegram_message(testo):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": testo}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Errore Telegram: {e}")

import urllib.parse

def scrape_price(url):
    try:
        # impersonate="chrome120" inganna Cloudflare/DataDome falsificando l'handshake TLS
        response = requests.get(url, impersonate="chrome120", timeout=15)
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
        
        if not prezzo_tag:
            tabelle = soup.select('dd.col-6.col-xl-7')
            for tag in tabelle:
                if '€' in tag.text:
                    prezzo_tag = tag
                    break

        if prezzo_tag:
            # Usa la tua funzione parse_prezzo (assicurati che esista nel file)
            return parse_prezzo(prezzo_tag.get_text(strip=True))
        
        print("⚠️ Prezzo non trovato nel DOM.")
        return None
        
    except Exception as e:
        print(f"❌ Errore con curl_cffi: {e}")
        return None

@app.post("/watch")
async def add_watch(item: WatchItem):
    clean_url = item.card_url.split('?')[0] 
    final_url = f"{clean_url}?language=5&minCondition=2"
    
    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="URL non valido")
    
    current_price = scrape_price(final_url)
    nome = final_url.split('/')[-1].split('?')[0].replace('-', ' ')
    
    cur = conn.cursor()
    cur.execute("INSERT INTO watchlist (user_id, url, last_price, created_at) VALUES (?, ?, ?, ?)",
                (item.user_id, final_url, current_price, datetime.now().isoformat()))
    conn.commit()
    
    if current_price is not None:
        send_telegram_message(f"✅ {nome} aggiunta!\n💰 Prezzo iniziale: {current_price}€")
        
    return {"status": "aggiunta", "id": cur.lastrowid, "prezzo": current_price}

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

def job_check_prices():
    print("🔍 Controllo prezzi in corso...")
    cur = conn.cursor()
    cur.execute("SELECT id, url, last_price FROM watchlist")
    
    for row in cur.fetchall():
        watch_id, url, old_price = row
        new_price = scrape_price(url)
        
        if new_price is not None:
            nome = url.split('/')[-1].split('?')[0].replace('-', ' ')
            if old_price is None or new_price != old_price:
                msg = f"🚨 AGGIORNAMENTO PREZZO!\n🃏 {nome}\n💶 Nuovo prezzo: {new_price}€ (era {old_price}€)\n🔗 {url}"
                send_telegram_message(msg)
                cur.execute("UPDATE watchlist SET last_price=? WHERE id=?", (new_price, watch_id))
                conn.commit()

def run_scheduler():
    schedule.every(1).minutes.do(job_check_prices)
    while True:
        schedule.run_pending()
        time.sleep(10)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
