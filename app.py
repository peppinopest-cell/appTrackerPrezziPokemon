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
import urllib.parse

# IMPORTANTE: Importiamo la libreria standard requests come std_requests per Telegram, 
# e curl_cffi per lo scraping, così non si pestano i piedi a vicenda!
import requests as std_requests
from curl_cffi import requests as cffi_requests

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
        # Usiamo requests standard per chiamare le API di Telegram
        std_requests.post(url, json=payload)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def scrape_price(url, max_retries=5):
    # Header COMPLETI di un Chrome 131 reale che naviga da Italia
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
    
    for attempt in range(max_retries):
        try:
            cache_buster = random.randint(1000000, 9999999)
            separator = "&" if "?" in url else "?"
            url_busted = f"{url}{separator}nocache={cache_buster}"

            with cffi_requests.Session(impersonate="chrome131") as session:  # Chrome 131 è più recente
                response = session.get(url_busted, headers=headers, timeout=12)
            
            # Log per capire cosa succede
            print(f"Status: {response.status_code}, Tentativo {attempt+1}/{max_retries}")
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
            if not prezzo_tag:
                tabelle = soup.select('dd.col-6.col-xl-7')
                for tag in tabelle:
                    if '€' in tag.text:
                        prezzo_tag = tag
                        break

            if prezzo_tag:
                prezzo = parse_prezzo(prezzo_tag.get_text(strip=True))
                print(f"✅ PREZZO TROVATO: {prezzo}€")
                return prezzo
            
            print(f"⚠️ Nessun prezzo trovato nell'HTML (tentativo {attempt+1})")
            
        except Exception as e:
            print(f"❌ Errore al tentativo {attempt+1}: {e}")
        
        if attempt < max_retries - 1:
            attesa = random.uniform(5.0, 10.0)
            print(f"⏳ Pausa {attesa:.1f}s prima del ritentativo...")
            time.sleep(attesa)
    
    print("❌ Tutti i tentativi falliti")
    return None

@app.post("/watch")
async def add_watch(item: WatchItem):
    clean_url = item.card_url.split('?')[0] 
    final_url = f"{clean_url}?language=5&minCondition=2"
    
    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="URL non valido")
    
    # 1. Tentativo di estrazione del prezzo (ora farà i 3 tentativi)
    current_price = scrape_price(final_url)
    
    nome = final_url.split('/')[-1].split('?')[0].replace('-', ' ')
    
    # 2. CONTROLLO DI SICUREZZA: se il prezzo è None, FERMIAMO TUTTO!
    if current_price is None:
        print(f"❌ Impossibile estrarre il prezzo per {nome}. Nessun salvataggio nel DB.")
        raise HTTPException(
            status_code=400, 
            detail="Blocco di sicurezza da parte di Cardmarket. Nessun prezzo trovato, la carta non è stata salvata."
        )
    
    # 3. Il prezzo c'è, procediamo col salvataggio nel DB
    cur = conn.cursor()
    cur.execute("INSERT INTO watchlist (user_id, url, last_price, created_at) VALUES (?, ?, ?, ?)",
                (item.user_id, final_url, current_price, datetime.now().isoformat()))
    conn.commit()
    
    # Mandiamo il messaggio Telegram
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
        
        # Qui il bot in background aggiorna il prezzo SOLO SE nuovo_price NON è None
        if new_price is not None:
            nome = url.split('/')[-1].split('?')[0].replace('-', ' ')
            if old_price is None or new_price != old_price:
                msg = f"🚨 AGGIORNAMENTO PREZZO!\n🃏 {nome}\n💶 Nuovo prezzo: {new_price}€ (era {old_price}€)\n🔗 {url}"
                send_telegram_message(msg)
                
                cur = conn.cursor() # apriamo un nuovo cursore per l'update
                cur.execute("UPDATE watchlist SET last_price=? WHERE id=?", (new_price, watch_id))
                conn.commit()
        else:
            print(f"⚠️ Bot bloccato su {url}. Mantengo il vecchio prezzo in memoria.")

def run_scheduler():
    schedule.every(1).minutes.do(job_check_prices)
    while True:
        schedule.run_pending()
        time.sleep(10)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
