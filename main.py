import sqlite3
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

DB_NAME = "tracker.db"

# ==================== TELEGRAM SOZLAMALARI ====================
TELEGRAM_BOT_TOKEN = "8986494486:AAHJCm_fUlQalFQLjArrnXWZ-kewpDGGavE"
TELEGRAM_CHAT_ID = "8130935215"

def send_alert(message: str):
    print(f"🔔 [XABAR]: {message}")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(tg_url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }, timeout=8)
        except Exception as e:
            print(f"Telegram xatosi: {e}")

# ==================== SKRAPER MOTOR ====================
def scrape_product(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.content, "html.parser")
    title_elem = soup.find("h1") or soup.find("meta", property="og:title")
    title = title_elem.text.strip() if title_elem else "Noma'lum tovar"

    price = 0.0
    price_elem = soup.find("p", class_="price_color") or soup.find("span", class_="price")
    if price_elem:
        import re
        match = re.search(r"[\d\.]+", price_elem.text.replace(",", "."))
        if match:
            price = float(match.group())

    avail_elem = soup.find("p", class_="instock availability")
    is_available = True
    if avail_elem and "In stock" not in avail_elem.text:
        is_available = False

    return {"title": title, "price": price, "availability": is_available}

# ==================== BAZA INIT ====================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            current_price REAL NOT NULL,
            original_price REAL NOT NULL,
            is_available BOOLEAN NOT NULL,
            url TEXT UNIQUE NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==================== CRON SCHEDULER ====================
def check_all_prices():
    print("🔄 [Scheduler] Narxlar fon tekshiruvi boshlandi...")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, title, current_price, original_price, url FROM products")
    items = c.fetchall()

    for item_id, title, old_price, orig_price, url in items:
        try:
            data = scrape_product(url)
            if not data:
                continue
            new_price = data['price']
            is_avail = data['availability']

            if new_price < old_price:
                msg = (
                    f"🔥 <b>DIQQAT, NARX TUSHDI!</b>\n\n"
                    f"📦 <b>{title}</b>\n"
                    f"📉 Eski narx: <s>${old_price}</s>\n"
                    f"🏷 Yangi narx: <b>${new_price}</b>\n"
                    f"🔗 <a href='{url}'>Xarid qilish</a>"
                )
                send_alert(msg)

            c.execute("""
                UPDATE products 
                SET current_price = ?, is_available = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_price, is_avail, item_id))
        except Exception as e:
            print(f"Xatolik: {e}")

    conn.commit()
    conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_all_prices, 'interval', minutes=10)
    scheduler.start()
    send_alert("🚀 <b>PriceTracker Serveri muvaffaqiyatli ishga tushdi!</b>")
    yield
    scheduler.shutdown()

app = FastAPI(title="PriceTracker PRO API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ProductRequest(BaseModel):
    url: str

# ==================== ENDPOINTLAR ====================

@app.get("/")
def home():
    return {"status": "online", "message": "PriceTracker API is running"}

@app.get("/products")
def get_products():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, title, current_price, original_price, is_available, url FROM products ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return {"status": "success", "data": [
        {"id": r[0], "title": r[1], "current_price": r[2], "original_price": r[3], "is_available": bool(r[4]), "url": r[5]}
        for r in rows
    ]}

@app.post("/products")
def add_product(payload: ProductRequest):
    data = scrape_product(payload.url)
    if not data or not data.get("title"):
        raise HTTPException(status_code=400, detail="Tovarni skanerlab bo'lmadi")

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO products (title, current_price, original_price, is_available, url)
            VALUES (?, ?, ?, ?, ?)
        """, (data["title"], data["price"], data["price"], data["availability"], payload.url))
        conn.commit()
        prod_id = c.lastrowid
        send_alert(f"✅ <b>Yangi tovar kuzatuvga olindi:</b>\n📦 {data['title']}\n💵 Narxi: ${data['price']}")
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Bu tovar allaqachon qo'shilgan")
    conn.close()
    return {"status": "success", "id": prod_id, "data": data}

@app.delete("/products/{product_id}")
def delete_product(product_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    cursor = c.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "O'chirildi"}
