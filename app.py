from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import cloudscraper
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import schedule
import time
import threading
import os
import requests

app = FastAPI(title="🃏 Price Bot API")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

DB_PATH = os.getenv("DB_PATH", "/tmp/watchlist.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, soglia REAL, last_price REAL, created_at TEXT, push_token TEXT)''')
conn.commit()

# Abbiamo modificato la richiesta: ora chiede l'URL diretto
class WatchItem(BaseModel):
    user_id: str
    card_url: str
    soglia: float
    push_token: str = ""

def parse_prezzo(prezzo_str):
    if prezzo_str == "N/D": return None
    try: return float(prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip())
    except: return None

def send_push_message(token, message, title):
    try:
        requests.post(
            "https://exp.host/--/api/v2/push/send",
            json={"to": token, "title": title, "body": message, "sound": "default"}
        )
    except: pass

@app.post("/watch")
async def add_watch(item: WatchItem):
    # Rimuoviamo eventuali parametri extra dall'url per pulizia e assicuriamoci che sia in italiano e minCond 2
    clean_url = item.card_url.split('?')[0] 
    final_url = f"{clean_url}?language=5&minCondition=2"
    
    # Controlliamo che l'url sia valido su cardmarket
    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="L'URL deve essere di Cardmarket")
        
    cur = conn.cursor()
    cur.execute("INSERT INTO watchlist (user_id, url, soglia, created_at, push_token) VALUES (?, ?, ?, ?, ?)",
                (item.user_id, final_url, item.soglia, datetime.now().isoformat(), item.push_token))
    conn.commit()
    return {"status": "aggiunta", "id": cur.lastrowid}

@app.get("/watchlist/{user_id}")
async def get_watchlist(user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT id, url, soglia, last_price FROM watchlist WHERE user_id=?", (user_id,))
    # Il nome della carta viene estratto dall'URL
    return [{"id": row[0], "nome": row[1].split('/')[-1].split('?')[0].replace('-', ' '), "url": row[1], "soglia": row[2], "last_price": row[3]} for row in cur.fetchall()]

def job_check_prices():
    print(f"🔍 Controllo prezzi in corso...")
    cur = conn.cursor()
    cur.execute("SELECT id, url, soglia, push_token FROM watchlist")
    scraper = cloudscraper.create_scraper()
    
    for row in cur.fetchall():
        watch_id, url, soglia, push_token = row
        try:
            html = scraper.get(url).text
            soup = BeautifulSoup(html, "html.parser")
            prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
            prezzo = parse_prezzo(prezzo_tag.get_text(strip=True)) if prezzo_tag else None
            
            if prezzo and prezzo <= soglia:
                nome = url.split('/')[-1].split('?')[0].replace('-', ' ')
                print(f"🚨 ALERT! {nome} è sceso a {prezzo}€!")
                if push_token:
                    send_push_message(push_token, f"Il prezzo è sceso a {prezzo}€! (Soglia: {soglia}€)", f"🚨 {nome} Trovato!")
                    
            cur.execute("UPDATE watchlist SET last_price=? WHERE id=?", (prezzo, watch_id))
            conn.commit()
        except Exception as e:
            print(f"Errore check url {url}: {e}")

def run_scheduler():
    schedule.every(15).minutes.do(job_check_prices)
    while True:
        schedule.run_pending()
        time.sleep(60)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
