from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
from fastapi.responses import Response
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from curl_cffi import requests as cffi_requests
import sqlite3
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
conn.row_factory = sqlite3.Row
cur = conn.cursor()

Base tables (existing)
cur.execute('''CREATE TABLE IF NOT EXISTS users
(id TEXT PRIMARY KEY, bot_token TEXT, chat_id TEXT, check_interval INTEGER DEFAULT 5, created_at TEXT)''')
cur.execute('''CREATE TABLE IF NOT EXISTS watchlist
(id INTEGER PRIMARY KEY, user_id TEXT, url TEXT, last_price REAL, created_at TEXT)''')

Try adding legacy columns if missing
for stmt in [
"ALTER TABLE watchlist ADD COLUMN image_url TEXT",
"ALTER TABLE watchlist ADD COLUMN condition TEXT",
"ALTER TABLE watchlist ADD COLUMN language TEXT",
"ALTER TABLE users ADD COLUMN passwordhash TEXT",
"ALTER TABLE users ADD COLUMN updatedat TEXT",
"ALTER TABLE watchlist ADD COLUMN updated_at TEXT",
"ALTER TABLE watchlist ADD COLUMN set_name TEXT",
"ALTER TABLE watchlist ADD COLUMN card_name TEXT",
]:
try:
cur.execute(stmt)
except:
pass

New collections tables
cur.execute('''CREATE TABLE IF NOT EXISTS collections
(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL)''')
cur.execute('''CREATE TABLE IF NOT EXISTS collection_items
(id INTEGER PRIMARY KEY AUTOINCREMENT,
collection_id TEXT NOT NULL,
watch_id INTEGER,
url TEXT NOT NULL,
card_name TEXT,
set_name TEXT,
image_url TEXT,
condition TEXT,
language TEXT,
last_price REAL,
last_checked TEXT,
created_at TEXT NOT NULL,
UNIQUE(collection_id, url))''')

try:
cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id)")
except:
pass
conn.commit()
print("✅ Database inizializzato")

job_lock = threading.Lock()
scrape_semaphore = threading.Semaphore(3)
active_users = {}

Pydantic models
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

class CreateCollectionModel(BaseModel):
user_id: str
name: str

class AddCollectionItemsModel(BaseModel):
urls: list[str] = ]
watch_ids: list[int] = ]

Utilities
def hash_password(password: str) -> str:
salt = secrets.token_hex(16)
hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
return f"{salt}${hashed}"

def verify_password(password: str, stored: str) -> bool:
try:
salt, old_hash = stored.split("$", 1)
new_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
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

def parse_prezzo(prezzo_str):
if not prezzo_str or prezzo_str == "N/D":
return None
try:
pulito = prezzo_str.replace("€", "").replace(".", "").replace(",", ".").strip()
return float(pulito)
except:
return None

def slug_to_name(url: str) -> str:
try:
return url.split('/')[-1].split('?').replace('-', ' ')
except:
return "Carta"

def infer_set_name(url: str) -> str:
try:
parts = url.split("/Pokemon/", 1)
if len(parts) < 2:
return "Altro"
after = parts
segs = after.split("/")
if len(segs) >= 2:
return segs.replace('-', ' ')
return "Altro"
except:
return "Altro"

def send_telegram_message(user_id, testo):
c = conn.cursor()
c.execute("SELECT bot_token, chat_id FROM users WHERE id=?", (user_id,))
user = c.fetchone()
if not user or not user or not user:
return
url = f"https://api.telegram.org/bot{user}/sendMessage"
try:
std_requests.post(url, json={"chat_id": user, "text": testo}, timeout=10)
except:
pass

Scraping core (re-usable)
def scrape_card_data(url, max_retries=3):
with scrape_semaphore:
identities = [
{
"impersonate": "safari15_5",
"headers": {
"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,/;q=0.8",
"Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
"Accept-Encoding": "gzip, deflate, br, zstd",
"Upgrade-Insecure-Requests": "1",
"Cache-Control": "max-age=0",
"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Safari/605.1.15"
}
},
{
"impersonate": "chrome120",
"headers": {
"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,/;q=0.8",
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
identity = identities[min(attempt, len(identities) - 1)]
time.sleep(random.uniform(1.2, 3.2))
sep = "&" if "?" in url else "?"
url_busted = f"{url}{sep}nocache={random.randint(1000000, 9999999)}"
response = cffi_requests.get(url_busted, impersonate=identity["impersonate"], headers=identity["headers"], timeout=18)
if response.status_code == 403:
time.sleep(random.uniform(4.0, 7.0))
continue
soup = BeautifulSoup(response.text, "html.parser")
html_text = response.text
price = None
condition = "N/A"
language = "🌐"
image_url = ""
img_meta = soup.find('meta', property='og:image')
if img_meta and img_meta.get('content'):
image_url = img_meta['content']
if not image_url:
img_tag = soup.select_one('.image-container img, .product-image img, .card-image img')
if img_tag:
image_url = img_tag.get('src') or img_tag.get('data-src') or ""
if not image_url:
match = re.search(r'https?://[^"]+/img/[^"]+/Products/[^"]+.(?:jpg|png)', html_text)
if match:
image_url = match.group(0)
if image_url.startswith("//"):
image_url = "https:" + image_url
elif image_url.startswith("/"):
image_url = "https://www.cardmarket.com" + image_url
first_row = soup.select_one("div.row.article-row")
if first_row:
price_tag = first_row.select_one(".price-container .color-primary, .color-primary.small, span.fw-bold, .font-weight-bold.color-primary")
if price_tag:
price = parse_prezzo(price_tag.get_text(strip=True))
cond_tag = first_row.select_one("a.article-condition span.badge")
if cond_tag:
condition = cond_tag.get_text(strip=True)
lang_tag = first_row.select_one("span.icon[aria-label], span.icon[data-original-title], span.icon[onmouseover]")
if lang_tag:
lang_text = lang_tag.get("aria-label") or lang_tag.get("data-original-title") or ""
if not lang_text and lang_tag.get("onmouseover"):
m = re.search(r"showMsgBox\(this,([^]+)`\\)", lang_tag.get("onmouseover"))
if m:
lang_text = m.group(1)
lang_map = {"Inglese":"🇬🇧","Italiano":"🇮🇹","Francese":"🇫🇷","Tedesco":"🇩🇪","Spagnolo":"🇪🇸","Portoghese":"🇵🇹","Giapponese":"🇯🇵","Coreano":"🇰🇷","Cinese":"🇨🇳"}
for k, v in lang_map.items():
if k.lower() in lang_text.lower():
language = v
break
if price is None:
prezzo_tag = soup.select_one("span.color-primary.small.text-end.text-nowrap.fw-bold")
if not prezzo_tag:
for tag in soup.select("dd.col-6.col-xl-7"):
if "€" in tag.get_text(" ", strip=True):
prezzo_tag = tag
break
if prezzo_tag:
price = parse_prezzo(prezzo_tag.get_text(strip=True))
if price is not None:
return {"price": price, "image": image_url, "condition": condition, "language": language}
except:
pass
time.sleep(random.uniform(4.5, 8.5))
return None

def upsert_collection_item_from_url(collection_id: str, url: str):
data = scrape_card_data(url)
now = datetime.now().isoformat()
set_name = infer_set_name(url)
card_name = slug_to_name(url)
c = conn.cursor()
if data and data.get("price") is not None:
c.execute('''
INSERT INTO collection_items(collection_id, url, card_name, set_name, image_url, condition, language, last_price, last_checked, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(collection_id, url) DO UPDATE SET
card_name=excluded.card_name,
set_name=excluded.set_name,
image_url=excluded.image_url,
condition=excluded.condition,
language=excluded.language,
last_price=excluded.last_price,
last_checked=excluded.last_checked
''', (collection_id, url, card_name, set_name, data.get("image", ""), data.get("condition", "N/A"), data.get("language", "🌐"), data["price"], now, now))
else:
c.execute('''
INSERT INTO collection_items(collection_id, url, card_name, set_name, last_checked, created_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(collection_id, url) DO UPDATE SET
card_name=excluded.card_name,
set_name=excluded.set_name,
last_checked=excluded.last_checked
''', (collection_id, url, card_name, set_name, now, now))
conn.commit()

def refresh_collection_prices(collection_id: str):
c = conn.cursor()
c.execute("SELECT id, url FROM collection_items WHERE collection_id=?", (collection_id,))
items = c.fetchall()
for row in items:
item_id, url = row, row
data = scrape_card_data(url)
now = datetime.now().isoformat()
if data and data.get("price") is not None:
c.execute("UPDATE collection_items SET last_price=?, image_url=?, condition=?, language=?, last_checked=? WHERE id=?",
(data["price"], data.get("image", ""), data.get("condition", "N/A"), data.get("language", "🌐"), now, item_id))
c.execute("UPDATE watchlist SET last_price=?, image_url=?, condition=?, language=?, updated_at=? WHERE url=?",
(data["price"], data.get("image", ""), data.get("condition", "N/A"), data.get("language", "🌐"), now, url))
conn.commit()
time.sleep(random.uniform(5.0, 10.0))

Endpoints
@app.get("/ping/{user_id}")
async def ping_user(user_id: str):
active_users[user_id] = time.time()
return {"status": "ok"}

@app.post("/users/settings")
async def save_settings(settings: UserSettings):
c = conn.cursor()
c.execute("UPDATE users SET bot_token=?, chat_id=?, check_interval=?, updatedat=? WHERE id=?",
(settings.bot_token, settings.chat_id, settings.check_interval, datetime.now().isoformat(), settings.user_id))
if c.rowcount == 0:
c.execute("INSERT INTO users (id, bot_token, chat_id, check_interval, created_at, updatedat) VALUES (?, ?, ?, ?, ?, ?)",
(settings.user_id, settings.bot_token, settings.chat_id, settings.check_interval, datetime.now().isoformat(), datetime.now().isoformat()))
conn.commit()
send_telegram_message(settings.user_id, "✅ Impostazioni salvate correttamente!")
return {"status": "saved"}

@app.get("/users/{user_id}/settings")
async def get_settings(user_id: str):
c = conn.cursor()
c.execute("SELECT bot_token, chat_id, check_interval FROM users WHERE id=?", (user_id,))
row = c.fetchone()
if row:
return {"bot_token": row, "chat_id": row, "check_interval": row}
return {"bot_token": "", "chat_id": "", "check_interval": 5}

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
c = conn.cursor()
c.execute("SELECT id FROM users WHERE chat_id=?", (chat_id,))
if c.fetchone():
raise HTTPException(status_code=400, detail="Esiste già un account associato a questo Chat ID.")
user_id = f"user_{uuid.uuid4().hex[:12]}"
now = datetime.now().isoformat()
passwordhash = hash_password(password)
c.execute("INSERT INTO users (id, bot_token, chat_id, check_interval, created_at, passwordhash, updatedat) VALUES (?, ?, ?, ?, ?, ?, ?)",
(user_id, bot_token, chat_id, data.checkinterval, now, passwordhash, now))
conn.commit()
send_telegram_message(user_id, "✅ Account creato correttamente! Il tuo profilo è stato registrato.")
return {"status": "registered", "userid": user_id, "chatid": chat_id, "checkinterval": data.checkinterval}

@app.post("/auth/login")
async def login_user(data: LoginUserModel):
chat_id = data.chatid.strip()
password = data.password.strip()
if not chat_id or not password:
raise HTTPException(status_code=400, detail="Chat ID e password sono obbligatori.")
c = conn.cursor()
c.execute("SELECT id, bot_token, chat_id, check_interval, passwordhash FROM users WHERE chat_id=?", (chat_id,))
row = c.fetchone()
if not row:
raise HTTPException(status_code=404, detail="Account non trovato.")
user_id, bot_token, saved_chatid, check_interval, passwordhash = row
if not passwordhash or not verify_password(password, passwordhash):
raise HTTPException(status_code=401, detail="Password non corretta.")
return {"status": "logged", "userid": user_id, "bottoken": bot_token or "", "chatid": saved_chatid or "", "checkinterval": check_interval or 5}

@app.post("/watch")
async def add_watch(item: WatchItem):
final_url = item.card_url.strip()
if "cardmarket.com" not in final_url:
raise HTTPException(status_code=400, detail="URL non valido. Assicurati che sia un link di Cardmarket.")
active_users[item.user_id] = time.time()
data = scrape_card_data(final_url)
nome = slug_to_name(final_url)
if not data or data["price"] is None:
raise HTTPException(status_code=400, detail="Impossibile estrarre il prezzo. Il sito potrebbe aver bloccato la richiesta. Riprova più tardi.")
c = conn.cursor()
c.execute("INSERT INTO watchlist (user_id, url, last_price, image_url, condition, language, created_at, updated_at, set_name, card_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
(item.user_id, final_url, data["price"], data["image"], data.get("condition", "N/A"), data.get("language", "🌐"), datetime.now().isoformat(), datetime.now().isoformat(), infer_set_name(final_url), nome))
conn.commit()
send_telegram_message(item.user_id, f"✅ {nome} aggiunta!\n🗣️ {data.get('language', '🌐')} | 🏷️ {data.get('condition', 'N/A')}\n💰 Prezzo iniziale: {data['price']}€")
return {"status": "aggiunta", "id": c.lastrowid, "prezzo": data["price"], "image": data["image"], "condition": data.get("condition"), "language": data.get("language")}

def process_mass_import(user_id: str, urls: list[str]):
success_count = 0
for url in urls:
final_url = url.strip()
if "cardmarket.com" not in final_url:
continue
data = scrape_card_data(final_url)
if data and data["price"] is not None:
c = conn.cursor()
c.execute("SELECT id FROM watchlist WHERE user_id=? AND url=?", (user_id, final_url))
if not c.fetchone():
c.execute("INSERT INTO watchlist (user_id, url, last_price, image_url, condition, language, created_at, updated_at, set_name, card_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
(user_id, final_url, data["price"], data["image"], data.get("condition", "N/A"), data.get("language", "🌐"), datetime.now().isoformat(), datetime.now().isoformat(), infer_set_name(final_url), slug_to_name(final_url)))
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
c = conn.cursor()
c.execute("SELECT id, url, last_price, image_url, condition, language, set_name, card_name, updated_at FROM watchlist WHERE user_id=? ORDER BY id DESC", (user_id,))
rows = c.fetchall()
return [{
"id": row,
"nome": row or slug_to_name(row),
"url": row,
"last_price": row,
"image_url": row or "",
"condition": row or "N/A",
"language": row or "🌐",
"set_name": row or infer_set_name(row),
"updated_at": row
} for row in rows]

@app.delete("/watch/{watch_id}")
async def delete_watch(watch_id: int):
c = conn.cursor()
c.execute("DELETE FROM watchlist WHERE id=?", (watch_id,))
conn.commit()
return {"status": "eliminata"}

@app.delete("/watchlist/{user_id}/clear")
async def clear_watchlist(user_id: str):
c = conn.cursor()
c.execute("DELETE FROM watchlist WHERE user_id=?", (user_id,))
conn.commit()
return {"status": "svuotata"}

Collections endpoints
@app.post("/collections")
async def create_collection(payload: CreateCollectionModel):
cid = f"col_{uuid.uuid4().hex[:12]}"
now = datetime.now().isoformat()
c = conn.cursor()
c.execute("INSERT INTO collections (id, user_id, name, created_at) VALUES (?, ?, ?, ?)", (cid, payload.user_id, payload.name.strip(), now))
conn.commit()
return {"id": cid, "name": payload.name.strip(), "created_at": now}

@app.get("/collections/{user_id}")
async def list_collections(user_id: str):
c = conn.cursor()
c.execute('''
SELECT c.id, c.name, c.created_at,
COUNT(ci.id) as cards_count,
COALESCE(ROUND(SUM(COALESCE(ci.last_price, 0)), 2), 0) as total_value,
MAX(ci.last_checked) as last_checked
FROM collections c
LEFT JOIN collection_items ci ON ci.collection_id = c.id
WHERE c.user_id = ?
GROUP BY c.id, c.name, c.created_at
ORDER BY c.created_at DESC
''', (user_id,))
return [dict(r) for r in c.fetchall()]

@app.delete("/collections/item/{item_id}")
async def delete_collection_item(item_id: int):
c = conn.cursor()
c.execute("DELETE FROM collection_items WHERE id=?", (item_id,))
conn.commit()
return {"status": "deleted"}

@app.delete("/collections/{collection_id}")
async def delete_collection(collection_id: str):
c = conn.cursor()
c.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
c.execute("DELETE FROM collections WHERE id=?", (collection_id,))
conn.commit()
return {"status": "deleted"}

@app.post("/collections/{collection_id}/add")
async def add_to_collection(collection_id: str, payload: AddCollectionItemsModel, background_tasks: BackgroundTasks):
c = conn.cursor()
inserted = 0
for watch_id in payload.watch_ids:
c.execute("SELECT url, card_name, set_name, image_url, condition, language, last_price, updated_at FROM watchlist WHERE id=?", (watch_id,))
row = c.fetchone()
if row:
now = datetime.now().isoformat()
c.execute('''
INSERT OR IGNORE INTO collection_items(collection_id, watch_id, url, card_name, set_name, image_url, condition, language, last_price, last_checked, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', (collection_id, watch_id, row, row or slug_to_name(row), row or infer_set_name(row), row or "", row or "N/A", row or "🌐", row, row or now, now))
inserted += 1
for url in payload.urls:
url = url.strip()
if "cardmarket.com" not in url:
continue
c.execute("INSERT OR IGNORE INTO collection_items(collection_id, url, card_name, set_name, created_at) VALUES (?, ?, ?, ?, ?)",
(collection_id, url, slug_to_name(url), infer_set_name(url), datetime.now().isoformat()))
inserted += 1
background_tasks.add_task(upsert_collection_item_from_url, collection_id, url)
conn.commit()
return {"status": "ok", "inserted": inserted}

@app.get("/collections/{collection_id}/summary")
async def collection_summary(collection_id: str):
c = conn.cursor()
c.execute("SELECT name FROM collections WHERE id=?", (collection_id,))
collection = c.fetchone()
if not collection:
raise HTTPException(status_code=404, detail="Collezione non trovata")
c.execute('''
SELECT id, url, card_name, set_name, image_url, condition, language, last_price, last_checked
FROM collection_items
WHERE collection_id=?
ORDER BY set_name ASC, card_name ASC
''', (collection_id,))
items = [dict(r) for r in c.fetchall()]
total = round(sum(float(x["last_price"]) for x in items if x.get("last_price") is not None), 2)
grouped = {}
for item in items:
set_name = item.get("set_name") or "Altro"
grouped.setdefault(set_name, {"set_name": set_name, "total": 0.0, "count": 0, "cards": []})
grouped[set_name]["cards"].append(item)
grouped[set_name]["count"] += 1
if item.get("last_price") is not None:
grouped[set_name]["total"] += float(item["last_price"])
sets = ]
for g in grouped.values():
g["total"] = round(g["total"], 2)
sets.append(g)
sets.sort(key=lambda x: x["set_name"].lower())
return {"collection_id": collection_id, "collection_name": collection, "total": total, "sets": sets, "cards_count": len(items)}

@app.post("/collections/{collection_id}/refresh")
async def refresh_collection(collection_id: str, background_tasks: BackgroundTasks):
background_tasks.add_task(refresh_collection_prices, collection_id)
return {"status": "queued"}

@app.get("/proxy-image")
async def proxy_image(url: str):
if not url.startswith("http"):
raise HTTPException(status_code=400, detail="URL non valido")
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://www.cardmarket.com/", "Origin": "https://www.cardmarket.com"}
try:
img_resp = std_requests.get(url, headers=headers, timeout=10)
if img_resp.status_code == 200:
return Response(content=img_resp.content, media_type=img_resp.headers.get("Content-Type", "image/jpeg"))
raise HTTPException(status_code=img_resp.status_code, detail="Impossibile scaricare l'immagine")
except Exception as e:
raise HTTPException(status_code=500, detail=str(e))

Scheduler + background jobs
def job_check_prices():
if not job_lock.acquire(blocking=False):
return
try:
c = conn.cursor()
current_minute = datetime.now().minute
c.execute("SELECT id, check_interval FROM users")
users_to_check = ]
for user_row in c.fetchall():
user_id = user_row
interval = user_row or 5
if current_minute % interval == 0:
users_to_check.append(user_id)
if users_to_check:
placeholders = ','.join(['?'] * len(users_to_check))
c.execute(f"SELECT id, user_id, url, last_price FROM watchlist WHERE user_id IN ({placeholders})", users_to_check)
all_cards = c.fetchall()
unique_urls = list(set(card for card in all_cards))
scraped_data = {}
def fetch_url(url):
return url, scrape_card_data(url)
with ThreadPoolExecutor(max_workers=3) as executor:
for url, data in executor.map(fetch_url, unique_urls):
scraped_data[url] = data
for card in all_cards:
watch_id, user_id, url, old_price = card
data = scraped_data.get(url)
if data and data.get("price") is not None:
new_price = data["price"]
if old_price is None or new_price != old_price:
nome = slug_to_name(url)
msg = f"🚨 AGGIORNAMENTO PREZZO!\n🃏 {nome}\n🗣️ {data.get('language', '🌐')} | 🏷️ {data.get('condition', 'N/A')}\n💶 Nuovo prezzo: {new_price}€ (era {old_price}€)\n🔗 {url}"
send_telegram_message(user_id, msg)
cu = conn.cursor()
cu.execute("UPDATE watchlist SET last_price=?, image_url=?, condition=?, language=?, updated_at=? WHERE id=?",
(new_price, data.get("image", ""), data.get("condition", "N/A"), data.get("language", "🌐"), datetime.now().isoformat(), watch_id))
conn.commit()
finally:
job_lock.release()

def daily_collections_refresh():
if not job_lock.acquire(blocking=False):
return
try:
c = conn.cursor()
c.execute("SELECT id, user_id, name FROM collections")
collections = c.fetchall()
for col in collections:
refresh_collection_prices(col)
send_telegram_message(col, f"📚 Collezione aggiornata: {col}")
finally:
job_lock.release()

def run_scheduler():
schedule.every(1).minutes.do(job_check_prices)
schedule.every().day.at("03:15").do(daily_collections_refresh)
while True:
schedule.run_pending()
time.sleep(10)

threading.Thread(target=run_scheduler, daemon=True).start()

if _name_ == "_main_":
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
