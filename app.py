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

# IMPORTANTE: Importiamo la libreria standard requests come std_requests per Telegram, 
# e curl_cffi per lo scraping, così non si pestano i piedi a vicenda!
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

# 1. DB PERSISTENTE: ./ invece di /tmp
DB_PATH = os.getenv("DB_PATH", "./watchlist_v4.db")
print(f"📁 DB_PATH impostato su: {DB_PATH}")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT)''')
conn.commit()
print("✅ Database inizializzato")

# --- I TUOI DATI TELEGRAM ---
BOT_TOKEN = "8470410976:AAEJDujquJMbNVHy48Js6dJw6O6qmf3QJds"
CHAT_ID = "393014146"

# Lock per evitare esecuzioni sovrapposte del job schedulato
job_lock = threading.Lock()

# --- NUOVO: Dizionario per tracciare chi ha l'app aperta ---
active_users = {}

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
        std_requests.post(url, json=payload, timeout=10)
        print("📨 Telegram inviato")
    except Exception as e:
        print(f"❌ Errore Telegram: {e}")

# --- NUOVO: Endpoint per ricevere il "battito" dall'app ---
@app.get("/ping/{user_id}")
async def ping_user(user_id: str):
    active_users[user_id] = time.time()
    return {"status": "ok", "user": user_id}

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

            # 2. CHIAMATA DIRETTA SENZA SESSIONE: questo evita il riuso della connessione (session reuse) 
            # che fa capire a Cardmarket che sei uno scraper ripetitivo.
            response = cffi_requests.get(url_busted, impersonate="chrome120", headers=headers, timeout=12)
            
            print(f"🌐 Scraping tentativo {attempt + 1}/{max_retries}: status {response.status_code}")
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
            if not prezzo_tag:
                tabelle = soup.select('dd.col-6.col-xl-7')
                for tag in tabelle:
                    if '€' in tag.text:
                        prezzo_tag = tag
                        break

            if prezzo_tag:
                # TRIONFO! Trovato il prezzo, usciamo subito dal ciclo.
                prezzo = parse_prezzo(prezzo_tag.get_text(strip=True))
                print(f"✅ PREZZO TROVATO al tentativo {attempt + 1}: {prezzo}€")
                return prezzo
            
            print(f"⚠️ Tentativo {attempt + 1} di {max_retries} fallito: prezzo non trovato")
            
        except Exception as e:
            print(f"❌ Errore al tentativo {attempt + 1}: {e}")
        
        # Pausa prima di riprovare
        if attempt < max_retries - 1:
            attesa = random.uniform(2.5, 4.5)
            print(f"⏳ Ritento tra {attesa:.1f} sec...")
            time.sleep(attesa)
            
    # Se esce dal ciclo, tutti i tentativi sono falliti
    print("❌ Tutti i tentativi falliti")
    return None

@app.post("/watch")
async def add_watch(item: WatchItem):
    clean_url = item.card_url.split('?')[0] 
    final_url = f"{clean_url}?language=5&minCondition=2"
    
    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="URL non valido")
    
    # --- NUOVO: Se aggiunge una carta, registriamo che è attivo ---
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
    cur.execute("INSERT INTO watchlist (user_id, url, last_price, created_at) VALUES (?, ?, ?, ?)",
                (item.user_id, final_url, current_price, datetime.now().isoformat()))
    conn.commit()
    
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
    # 3. LOCK: Se il job dura più di un minuto, salta il giro invece di accavallarsi e farsi bannare
    if not job_lock.acquire(blocking=False):
        print("⏭️ Job saltato: esecuzione precedente ancora in corso")
        return

    try:
        print("🔍 Controllo prezzi in corso...")
        cur = conn.cursor()
        # --- NUOVO: Aggiunto user_id nella SELECT ---
        cur.execute("SELECT id, user_id, url, last_price FROM watchlist")
        rows = cur.fetchall()
        
        if not rows:
            print("📭 Nessuna carta in watchlist")
            # Tolto il test job vuoto per non spammare Telegram se non c'è nulla
            return

        for row in rows:
            watch_id, user_id, url, old_price = row
            
            # --- NUOVO: Controlla se l'utente è attivo ---
            last_seen = active_users.get(user_id, 0)
            if time.time() - last_seen > 120: # 120 secondi = 2 minuti
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
                    # NOTIFICA DI TEST
                    msg = f"🧪 TEST JOB OK (App aperta)\n🃏 {nome}\n💶 Prezzo invariato: {new_price}€\n🔗 {url}"
                    send_telegram_message(msg)
                    print(f"ℹ️ Prezzo invariato per {nome}, notifica test inviata")
            else:
                # NOTIFICA DI TEST FALLITO
                msg = f"⚠️ TEST JOB FALLITO\n🃏 {nome}\n❌ Prezzo non trovato\n🔗 {url}"
                send_telegram_message(msg)
                print(f"⚠️ Bot bloccato su {url}. Mantengo il vecchio prezzo in memoria.")

    finally:
        job_lock.release()

def run_scheduler():
    schedule.every(1).minutes.do(job_check_prices)
    print("⏰ Scheduler avviato: controllo ogni 1 minuto (solo per utenti con app aperta)")
    while True:
        schedule.run_pending()
        time.sleep(10)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
