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

app = FastAPI(title="🃏 Price Bot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB persistente
DB_PATH = os.getenv("DB_PATH", "/tmp/watchlist.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, soglia REAL, last_price REAL, created_at TEXT)''')
conn.commit()

class CardRequest(BaseModel):
    nome_carta: str
    soglia_prezzo: float = 10.0
    lingua: int = 5

class WatchItem(BaseModel):
    user_id: str
    card_url: str
    soglia: float

def parse_prezzo(prezzo_str):
    if prezzo_str == "N/D": return None
    prezzo = prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip()
    try: return float(prezzo)
    except: return None

@app.get("/")
async def root():
    return {"status": "Price Bot API live!", "endpoint": "/docs"}

@app.post("/cerca-carta")
async def cerca_carta(request: CardRequest):
    search_url = f"https://www.cardmarket.com/it/Pokemon/Products/Singles?searchString={request.nome_carta.replace(' ', '+')}"
    scraper = cloudscraper.create_scraper()
    html = scraper.get(search_url).text
    soup = BeautifulSoup(html, "html.parser")
    primo_risultato = soup.select_one('a[href*="/Products/Singles/"]')
    if not primo_risultato:
        raise HTTPException(status_code=404, detail="Carta non trovata")
    url_base = "https://www.cardmarket.com" + primo_risultato['href']
    url_finale = f"{url_base}?language={request.lingua}&minCondition=2"
    return {"url_generato": url_finale, "nome_carta": request.nome_carta}

@app.post("/watch")
async def add_watch(item: WatchItem):
    cur = conn.cursor()
    cur.execute("INSERT INTO watchlist (user_id, url, soglia, created_at) VALUES (?, ?, ?, ?)",
                (item.user_id, item.card_url, item.soglia, datetime.now().isoformat()))
    conn.commit()
    return {"status": "aggiunta ✅", "id": cur.lastrowid}

@app.get("/watchlist/{user_id}")
async def get_watchlist(user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT id, nome_carta, url, soglia, last_price FROM watchlist WHERE user_id=?", (user_id,))
    return [{"id": row[0], "nome": row[1] or row[2].split('/')[-1], "url": row[2], "soglia": row[3], "last_price": row[4]} for row in cur.fetchall()]

@app.get("/prezzo/{watch_id}")
async def check_prezzo(watch_id: int):
    cur = conn.cursor()
    cur.execute("SELECT url, soglia FROM watchlist WHERE id=?", (watch_id,))
    row = cur.fetchone()
    if not row: raise HTTPException(status_code=404, detail="Non trovata")
    url, soglia = row
    scraper = cloudscraper.create_scraper()
    html = scraper.get(url).text
    soup = BeautifulSoup(html, "html.parser")
    prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
    prezzo_str = prezzo_tag.get_text(strip=True) if prezzo_tag else "N/D"
    prezzo = parse_prezzo(prezzo_str)
    sotto_soglia = prezzo and prezzo <= soglia if prezzo else False
    cur.execute("UPDATE watchlist SET last_price=? WHERE id=?", (prezzo, watch_id))
    conn.commit()
    return {"prezzo": prezzo_str, "sotto_soglia": sotto_soglia, "soglia": soglia}

def job_check_prices():
    print(f"🔍 [{datetime.now()}] Controllo {len(get_watchlist('marco'))} carte...")
    # Logica futura notifiche

# Scheduler background
def run_scheduler():
    schedule.every(15).minutes.do(job_check_prices)
    while True:
        schedule.run_pending()
        time.sleep(60)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
