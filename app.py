from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Creo un nuovo DB (v2) per non avere conflitti con le vecchie colonne "soglia"
DB_PATH = os.getenv("DB_PATH", "/tmp/watchlist_v2.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT, push_token TEXT)''')
conn.commit()

class WatchItem(BaseModel):
    user_id: str
    card_url: str
    push_token: str = ""

def parse_prezzo(prezzo_str):
    if prezzo_str == "N/D": return None
    try: return float(prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip())
    except: return None

def scrape_price(url):
    scraper = cloudscraper.create_scraper()
    try:
        html = scraper.get(url).text
        soup = BeautifulSoup(html, "html.parser")
        prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
        return parse_prezzo(prezzo_tag.get_text(strip=True)) if prezzo_tag else None
    except:
        return None

def send_push_message(token, message, title):
    try:
        requests.post(
            "https://exp.host/--/api/v2/push/send",
            json={"to": token, "title": title, "body": message, "sound": "default"}
        )
    except Exception as e:
        print("Errore push:", e)

@app.post("/watch")
async def add_watch(item: WatchItem):
    clean_url = item.card_url.split('?')[0] 
    final_url = f"{clean_url}?language=5&minCondition=2"
    
    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="L'URL deve essere di Cardmarket")
    
    # Prende il prezzo IN TEMPO REALE appena aggiungi l'URL!
    current_price = scrape_price(final_url)
    nome = final_url.split('/')[-1].split('?')[0].replace('-', ' ')
    
    cur = conn.cursor()
    cur.execute("INSERT INTO watchlist (user_id, url, last_price, created_at, push_token) VALUES (?, ?, ?, ?, ?)",
                (item.user_id, final_url, current_price, datetime.now().isoformat(), item.push_token))
    conn.commit()
    
    # Manda SUBITO la notifica col prezzo più basso attuale
    if item.push_token and current_price is not None:
        send_push_message(item.push_token, f"Prezzo iniziale rilevato: {current_price}€", f"✅ {nome} Aggiunta!")
        
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
    print(f"🔍 Controllo prezzi in corso...")
    cur = conn.cursor()
    cur.execute("SELECT id, url, last_price, push_token FROM watchlist")
    
    for row in cur.fetchall():
        watch_id, url, old_price, push_token = row
        new_price = scrape_price(url)
        
        if new_price is not None:
            nome = url.split('/')[-1].split('?')[0].replace('-', ' ')
            if old_price is None or new_price != old_price:
                msg = f"Il prezzo è CAMBIATO! Nuovo prezzo: {new_price}€ (era {old_price}€)"
                title = f"🚨 {nome} Aggiornato!"
            else:
                msg = f"Il prezzo è INVARIATO: {new_price}€"
                title = f"ℹ️ {nome} Stabile"
                
            print(msg)
            if push_token:
                send_push_message(push_token, msg, title)
                
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
