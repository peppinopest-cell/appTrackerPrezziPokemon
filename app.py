from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
from fastapi.responses import Response
import sqlite3
from datetime import datetime
import schedule
import time
import random
import threading
import os
import re
import uuid
import hashlib
import secrets
import hmac

import requests as std_requests
from curl_cffi import requests as cffi_requests

app = FastAPI(title="🔴 Poké Price Bot API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

DB_PATH = os.getenv("DB_PATH", "./watchlist_v4.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

# Creazione tabelle aggiornate
cur.execute('''CREATE TABLE IF NOT EXISTS users
               (id TEXT PRIMARY KEY, bot_token TEXT, chat_id TEXT, check_interval INTEGER DEFAULT 5, created_at TEXT)''')

cur.execute('''CREATE TABLE IF NOT EXISTS watchlist 
               (id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT)''')

# Migrazione: Aggiungo colonne immagine, condizione, lingua
try:
    cur.execute("ALTER TABLE watchlist ADD COLUMN image_url TEXT")
except: pass
try:
    cur.execute("ALTER TABLE watchlist ADD COLUMN condition TEXT")
except: pass
try:
    cur.execute("ALTER TABLE watchlist ADD COLUMN language TEXT")
except: pass
try:
    cur.execute("ALTER TABLE users ADD COLUMN passwordhash TEXT")
except:
    pass

try:
    cur.execute("ALTER TABLE users ADD COLUMN updatedat TEXT")
except:
    pass

try:
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_chatid ON users(chatid)")
except:
    pass

conn.commit()

conn.commit()
print("✅ Database inizializzato")

job_lock = threading.Lock()
active_users = {}

# Pydantic Models
class UserSettings(BaseModel):
    user_id: str
    bot_token: str
    chat_id: str
    check_interval: int

class WatchItem(BaseModel):
    user_id: str
    card_url: str

class MassImportItem(BaseModel):
    user_id: str
    urls: list[str]

class RegisterUserModel(BaseModel):
    bottoken: str
    chatid: str
    password: str
    checkinterval: int = 5

class LoginUserModel(BaseModel):
    chatid: str
    password: str
    
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000
    ).hex()
    return f"{salt}${hashed}"

def verify_password(password: str, stored: str) -> bool:
    try:
        salt, old_hash = stored.split("$", 1)
        new_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000
        ).hex()
        return hmac.compare_digest(new_hash, old_hash)
    except:
        return False

def validate_password(password: str):
    if len(password) < 8:
        return "La password deve avere almeno 8 caratteri."
    if not re.search(r"[A-Z]", password):
        return "La password deve contenere almeno una lettera maiuscola."
    if not re.search(r"[a-z]", password):
        return "La password deve contenere almeno una lettera minuscola."
    if not re.search(r"[0-9]", password):
        return "La password deve contenere almeno un numero."
    if not re.search(r"[^A-Za-z0-9]", password):
        return "La password deve contenere almeno un carattere speciale."
    return None
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
        return
    
    bot_token, chat_id = user[0], user[1]
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": testo}
    try:
        std_requests.post(url, json=payload, timeout=10)
    except:
        pass

# --- SCRAPING CORE ---
def scrape_card_data(url, max_retries=3):
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
            time.sleep(random.uniform(1.2, 3.2))

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
                time.sleep(random.uniform(4.0, 7.0))
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            html_text = response.text
            
            price = None
            condition = "N/A"
            language = "🌐"
            image_url = ""

                       # --- 1. ESTRAZIONE IMMAGINE (Nuovo metodo Anti-Blocco) ---
            image_url = ""
            
            # Tentativo 1: Il tag meta ufficiale og:image per i social network (il più affidabile)
            img_meta = soup.find('meta', property='og:image')
            if img_meta and img_meta.get('content'):
                image_url = img_meta['content']
                
            # Tentativo 2: Cerca qualsiasi immagine dentro il contenitore principale della carta
            if not image_url:
                img_tag = soup.select_one('.image-container img, .product-image img, .card-image img')
                if img_tag:
                    # Alcuni siti usano data-src per il caricamento pigro, altri src
                    image_url = img_tag.get('src') or img_tag.get('data-src') or ""
                    
            # Tentativo 3: Regex drastica per trovare qualunque cosa finisca con .jpg o .png e abbia "img/" e "Products"
            if not image_url:
                match = re.search(r'https?://[^"]+/img/[^"]+/Products/[^"]+\.(?:jpg|png)', html_text)
                if match:
                    image_url = match.group(0)

            # Normalizzazione (Se manca https:)
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                image_url = "https://www.cardmarket.com" + image_url

            # 2. ESTRAZIONE TABELLA (PREZZO, LINGUA E CONDIZIONE)
            first_row = soup.select_one("div.row.article-row")
            if first_row:
                # A. Prezzo
                price_tag = first_row.select_one(".price-container .color-primary, .color-primary.small, span.fw-bold, .font-weight-bold.color-primary")
                if price_tag:
                    price = parse_prezzo(price_tag.get_text(strip=True))

                # B. Condizione
                cond_tag = first_row.select_one("a.article-condition span.badge")
                if cond_tag:
                    condition = cond_tag.get_text(strip=True)

                # C. Lingua (Estratta dagli attributi icon o onmouseover)
                lang_tag = first_row.select_one("span.icon[aria-label], span.icon[data-original-title], span.icon[onmouseover]")
                if lang_tag:
                    lang_text = lang_tag.get("aria-label") or lang_tag.get("data-original-title") or ""
                    
                    if not lang_text and lang_tag.get("onmouseover"):
                        match = re.search(r"showMsgBox\(this,`([^`]+)`\)", lang_tag.get("onmouseover"))
                        if match:
                            lang_text = match.group(1)

                    lang_map = {
                        "Inglese": "🇬🇧",
                        "Italiano": "🇮🇹",
                        "Francese": "🇫🇷",
                        "Tedesco": "🇩🇪",
                        "Spagnolo": "🇪🇸",
                        "Portoghese": "🇵🇹",
                        "Giapponese": "🇯🇵",
                        "Coreano": "🇰🇷",
                        "Cinese": "🇨🇳"
                    }
                    for k, v in lang_map.items():
                        if k.lower() in lang_text.lower():
                            language = v
                            break

            # 3. FALLBACK PREZZO (Se la riga tabella non esiste ma siamo sulla pagina carta)
            if price is None:
                prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
                if not prezzo_tag:
                    tabelle = soup.select("dd.col-6.col-xl-7")
                    for tag in tabelle:
                        if "€" in tag.get_text(" ", strip=True):
                            prezzo_tag = tag
                            break
                if prezzo_tag:
                    price = parse_prezzo(prezzo_tag.get_text(strip=True))

            if price is not None:
                return {"price": price, "image": image_url, "condition": condition, "language": language}

        except Exception as e:
            pass

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
    send_telegram_message(settings.user_id, "🔴 Poké Price Bot: Impostazioni salvate correttamente! Inizierò a tracciare le tue carte.")
    return {"status": "saved"}

@app.get("/users/{user_id}/settings")
async def get_settings(user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT bot_token, chat_id, check_interval FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if row:
        return {"bot_token": row[0], "chat_id": row[1], "check_interval": row[2]}
    return {"bot_token": "", "chat_id": "", "check_interval": 5}

# --- AGGIUNTA SINGOLA ---
@app.post("/watch")
async def add_watch(item: WatchItem):
    final_url = item.card_url.strip()

    if "cardmarket.com" not in final_url:
        raise HTTPException(status_code=400, detail="URL non valido. Assicurati che sia un link di Cardmarket.")

    active_users[item.user_id] = time.time()
    data = scrape_card_data(final_url)
    nome = final_url.split('/')[-1].split('?')[0].replace('-', ' ')

    if not data or data["price"] is None:
        raise HTTPException(status_code=400, detail="Impossibile estrarre il prezzo. Il sito potrebbe aver bloccato la richiesta. Riprova più tardi.")

    cur = conn.cursor()
    cur.execute("INSERT INTO watchlist (user_id, url, last_price, image_url, condition, language, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.user_id, final_url, data["price"], data["image"], data.get("condition", "N/A"), data.get("language", "🌐"), datetime.now().isoformat()))
    conn.commit()

    send_telegram_message(item.user_id, f"✅ {nome} aggiunta!\n🗣️ {data.get('language', '🌐')} | 🏷️ {data.get('condition', 'N/A')}\n💰 Prezzo iniziale: {data['price']}€")
    time.sleep(random.uniform(2.5, 5.0))
    
    return {"status": "aggiunta", "id": cur.lastrowid, "prezzo": data["price"], "image": data["image"], "condition": data.get("condition"), "language": data.get("language")}
@app.post("/auth/register")
async def register_user(data: RegisterUserModel):
    bot_token = data.bottoken.strip()
    chat_id = data.chatid.strip()
    password = data.password.strip()

    if not bot_token or not chat_id or not password:
        raise HTTPException(status_code=400, detail="Bot Token, Chat ID e password sono obbligatori.")

    pwd_error = validate_password(password)
    if pwd_error:
        raise HTTPException(status_code=400, detail=pwd_error)

    cur = conn.cursor()
    # ATTENZIONE: La colonna è chat_id con l'underscore
    cur.execute("SELECT id FROM users WHERE chat_id=?", (chat_id,))
    existing = cur.fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Esiste già un account associato a questo Chat ID.")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()
    passwordhash = hash_password(password)

    # ATTENZIONE: Nomi colonne corretti (bot_token, chat_id, check_interval, created_at)
    cur.execute("""
        INSERT INTO users (id, bot_token, chat_id, check_interval, created_at, passwordhash, updatedat)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        bot_token,
        chat_id,
        data.checkinterval,
        now,
        passwordhash,
        now
    ))
    conn.commit()

    send_telegram_message(user_id, "✅ Account creato correttamente! Il tuo profilo è stato registrato.")

    return {
        "status": "registered",
        "userid": user_id,
        "chatid": chat_id,
        "checkinterval": data.checkinterval
    }

@app.post("/auth/login")
async def login_user(data: LoginUserModel):
    chat_id = data.chatid.strip()
    password = data.password.strip()

    if not chat_id or not password:
        raise HTTPException(status_code=400, detail="Chat ID e password sono obbligatori.")

    cur = conn.cursor()
    # ATTENZIONE: Nomi colonne corretti (bot_token, chat_id, check_interval)
    cur.execute("""
        SELECT id, bot_token, chat_id, check_interval, passwordhash
        FROM users
        WHERE chat_id=?
    """, (chat_id,))
    row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Account non trovato.")

    user_id, bot_token, saved_chatid, check_interval, passwordhash = row

    if not passwordhash or not verify_password(password, passwordhash):
        raise HTTPException(status_code=401, detail="Password non corretta.")

    return {
        "status": "logged",
        "userid": user_id,
        "bottoken": bot_token or "",
        "chatid": saved_chatid or "",
        "checkinterval": check_interval or 5
    }

@app.post("/auth/login")
async def login_user(data: LoginUserModel):
    chatid = data.chatid.strip()
    password = data.password.strip()

    if not chatid or not password:
        raise HTTPException(status_code=400, detail="Chat ID e password sono obbligatori.")

    cur = conn.cursor()
    cur.execute("""
        SELECT id, bottoken, chatid, checkinterval, passwordhash
        FROM users
        WHERE chatid=?
    """, (chatid,))
    row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Account non trovato.")

    user_id, bottoken, saved_chatid, checkinterval, passwordhash = row

    if not passwordhash or not verify_password(password, passwordhash):
        raise HTTPException(status_code=401, detail="Password non corretta.")

    return {
        "status": "logged",
        "userid": user_id,
        "bottoken": bottoken or "",
        "chatid": saved_chatid or "",
        "checkinterval": checkinterval or 5
    }

@app.post("/users/settings")
async def save_settings(settings: UserSettings):
    cur = conn.cursor()
    # ATTENZIONE: Nomi colonne corretti
    cur.execute("""
        UPDATE users
        SET bot_token=?, chat_id=?, check_interval=?, updatedat=?
        WHERE id=?
    """, (
        settings.bot_token,
        settings.chat_id,
        settings.check_interval,
        datetime.now().isoformat(),
        settings.user_id
    ))
    conn.commit()
    send_telegram_message(settings.user_id, "✅ Impostazioni salvate correttamente!")
    return {"status": "saved"}
# --- IMPORT MASSIVO CON CODA BACKGROUND ---
def process_mass_import(user_id: str, urls: list[str]):
    success_count = 0
    for url in urls:
        final_url = url.strip()
        if "cardmarket.com" not in final_url:
            continue
            
        data = scrape_card_data(final_url)
        if data and data["price"] is not None:
            cur = conn.cursor()
            cur.execute("SELECT id FROM watchlist WHERE user_id=? AND url=?", (user_id, final_url))
            if not cur.fetchone():
                cur.execute("INSERT INTO watchlist (user_id, url, last_price, image_url, condition, language, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (user_id, final_url, data["price"], data["image"], data.get("condition", "N/A"), data.get("language", "🌐"), datetime.now().isoformat()))
                conn.commit()
                success_count += 1
        
        time.sleep(random.uniform(10.0, 18.0))
        
    msg = f"📦 Import completato!\nAggiunte {success_count}/{len(urls)} carte al tracciamento."
    if success_count < len(urls):
        msg += "\n⚠️ Alcune carte non sono state caricate (possibile blocco di Cardmarket). Riprova."
    send_telegram_message(user_id, msg)

@app.post("/watch/mass")
async def add_mass_watch(item: MassImportItem, background_tasks: BackgroundTasks):
    active_users[item.user_id] = time.time()
    background_tasks.add_task(process_mass_import, item.user_id, item.urls)
    return {"status": "processing", "message": f"Importazione di {len(item.urls)} carte avviata. Riceverai un messaggio su Telegram al termine!"}

@app.get("/watchlist/{user_id}")
async def get_watchlist(user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT id, url, last_price, image_url, condition, language FROM watchlist WHERE user_id=? ORDER BY id DESC", (user_id,))
    return [
        {
            "id": row[0], 
            "nome": row[1].split('/')[-1].split('?')[0].replace('-', ' '), 
            "url": row[1], 
            "last_price": row[2], 
            "image_url": row[3] or "",
            "condition": row[4] or "N/A",
            "language": row[5] or "🌐"
        } for row in cur.fetchall()
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

@app.get("/proxy-image")
async def proxy_image(url: str):
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="URL non valido")
    
    # Facciamo finta di essere un browser normale su Cardmarket
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.cardmarket.com/",
        "Origin": "https://www.cardmarket.com"
    }
    
    try:
        # Scarichiamo l'immagine dal server di Cardmarket
        img_resp = std_requests.get(url, headers=headers, timeout=10)
        
        # Se ha successo, la restituiamo direttamente al frontend come se fossimo noi il server dell'immagine!
        if img_resp.status_code == 200:
            return Response(content=img_resp.content, media_type=img_resp.headers.get("Content-Type", "image/jpeg"))
        else:
            raise HTTPException(status_code=img_resp.status_code, detail="Impossibile scaricare l'immagine")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- JOB SCHEDULATO CON INTERVALLO UTENTE ---
def job_check_prices():
    if not job_lock.acquire(blocking=False):
        return

    try:
        cur = conn.cursor()
        cur.execute("SELECT id, check_interval FROM users")
        users = cur.fetchall()
        
        current_minute = datetime.now().minute
        
        for user_row in users:
            user_id = user_row[0]
            interval = user_row[1] or 5
            
            if current_minute % interval != 0:
                continue
                
            cur.execute("SELECT id, url, last_price FROM watchlist WHERE user_id=?", (user_id,))
            cards = cur.fetchall()
            
            for card in cards:
                watch_id, url, old_price = card
                nome = url.split('/')[-1].split('?')[0].replace('-', ' ')
                
                data = scrape_card_data(url)
                if data and data["price"] is not None:
                    new_price = data["price"]
                    if old_price is None or new_price != old_price:
                        msg = f"🚨 AGGIORNAMENTO PREZZO!\n🃏 {nome}\n🗣️ {data.get('language', '🌐')} | 🏷️ {data.get('condition', 'N/A')}\n💶 Nuovo prezzo: {new_price}€ (era {old_price}€)\n🔗 {url}"
                        send_telegram_message(user_id, msg)
                        
                        cur_update = conn.cursor()
                        cur_update.execute("UPDATE watchlist SET last_price=?, image_url=?, condition=?, language=? WHERE id=?", 
                                           (new_price, data["image"], data.get("condition", "N/A"), data.get("language", "🌐"), watch_id))
                        conn.commit()

                time.sleep(random.uniform(12.0, 22.0))

    finally:
        job_lock.release()

def run_scheduler():
    schedule.every(1).minutes.do(job_check_prices)
    while True:
        schedule.run_pending()
        time.sleep(10)

threading.Thread(target=run_scheduler, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
