import os
import re
import sqlite3
import requests
from bs4 import BeautifulSoup
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title="PriceTracker PRO API")

# Mobil ilovadan keladigan so'rovlarga to'liq ruxsat berish
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8986494486:AAHJCm_fUlQalFQLjArrnXWZ-kewpDGGavE")
DB_NAME = "tracker.db"

# Ma'lumotlar bazasini initsializatsiya qilish
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            current_price REAL NOT NULL,
            in_stock INTEGER NOT NULL,
            chat_id TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Pydantic modellari
class ItemCreate(BaseModel):
    url: str
    chat_id: str

class ItemResponse(BaseModel):
    id: int
    url: str
    title: str
    current_price: float
    in_stock: bool
    chat_id: str

# Telegramga xabarnoma yuborish funksiyasi
def send_telegram_alert(chat_id: str, message: str):
    if not chat_id:
        return
    cleaned_chat_id = str(chat_id).strip()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": cleaned_chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# Saytdan real narx va nomni oluvchi universal Scraper
def scrape_product_details(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    extracted_title = "Online Store Product"
    extracted_price = 29.99

    try:
        domain = url.split("//")[-1].split("/")[0].replace("www.", "")
        extracted_title = f"Product from {domain}"
    except Exception:
        pass

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")

            # 1. Mahsulot nomini topish
            h1 = soup.find("h1")
            og_title = soup.find("meta", property="og:title")
            if h1 and h1.get_text(strip=True):
                extracted_title = h1.get_text(strip=True)[:60]
            elif og_title and og_title.get("content"):
                extracted_title = og_title.get("content")[:60]

            # 2. Narxni qidirish (Sinflar va HTML elementlari bo'yicha)
            price_tag = (
                soup.find(class_=re.compile(r"price_color|current-price|product-price|price-current|price", re.I)) or
                soup.find("p", class_="price_color") or
                soup.find("span", class_=re.compile(r"price", re.I))
            )

            if price_tag:
                raw_text = price_tag.get_text(strip=True)
                match = re.search(r"(\d+[\.,]\d{1,2})", raw_text)
                if match:
                    extracted_price = float(match.group(1).replace(",", "."))
            else:
                # Butun HTML matnidan valyutali narxni qidirish
                match = re.search(r"[\$£€]\s*(\d+[\.,]\d{1,2})", response.text)
                if match:
                    extracted_price = float(match.group(1).replace(",", "."))

    except Exception as e:
        print(f"Scraper notice: {e}")

    return extracted_title, extracted_price

# Har soatda narxlarni avtomatik tekshiruvchi fon jarayoni
def auto_check_prices():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, url, title, current_price, chat_id FROM items")
    rows = cursor.fetchall()

    for item_id, item_url, old_title, old_price, chat_id in rows:
        _, new_price = scrape_product_details(item_url)
        if new_price and new_price < old_price:
            cursor.execute("UPDATE items SET current_price = ? WHERE id = ?", (new_price, item_id))
            conn.commit()

            alert_msg = (
                f"🔥 *PRICE DROP ALERT!*\n\n"
                f"📦 *Item:* `{old_title}`\n"
                f"📉 *Old Price:* ${old_price:.2f}\n"
                f"🎉 *New Price:* ${new_price:.2f}\n"
                f"🔗 [Buy Now]({item_url})"
            )
            send_telegram_alert(chat_id, alert_msg)
    conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(auto_check_prices, 'interval', hours=1)
scheduler.start()

@app.get("/")
def read_root():
    return {"status": "ok", "service": "PriceTracker PRO API"}

# Foydalanuvchi tovarlarini olish
@app.get("/items", response_model=List[ItemResponse])
def get_items(chat_id: Optional[str] = Query(None)):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if chat_id:
        cleaned_chat_id = str(chat_id).strip()
        cursor.execute("SELECT id, url, title, current_price, in_stock, chat_id FROM items WHERE chat_id = ?", (cleaned_chat_id,))
    else:
        cursor.execute("SELECT id, url, title, current_price, in_stock, chat_id FROM items")
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "url": r[1],
            "title": r[2],
            "current_price": float(r[3]),
            "in_stock": bool(r[4]),
            "chat_id": r[5]
        }
        for r in rows
    ]

# Tovar qo'shish va Telegramga yuborish
@app.post("/items", response_model=ItemResponse)
def add_item(item: ItemCreate):
    cleaned_chat_id = str(item.chat_id).strip()
    clean_url = item.url.strip()

    title, price = scrape_product_details(clean_url)
    in_stock_val = 1

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO items (url, title, current_price, in_stock, chat_id) VALUES (?, ?, ?, ?, ?)",
        (clean_url, title, price, in_stock_val, cleaned_chat_id)
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    alert_text = (
        f"🔔 *New Product Tracked!*\n\n"
        f"📦 *Item:* `{title}`\n"
        f"💰 *Current Price:* ${price:.2f}\n"
        f"✅ *Stock Status:* In Stock\n"
        f"🔗 [Open Product Page]({clean_url})"
    )
    send_telegram_alert(cleaned_chat_id, alert_text)

    return {
        "id": new_id,
        "url": clean_url,
        "title": title,
        "current_price": price,
        "in_stock": True,
        "chat_id": cleaned_chat_id
    }

# Tovarni o'chirish va Telegramga bildirishnoma jo'natish
@app.delete("/items/{item_id}")
def delete_item(item_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, chat_id FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()

    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")

    item_title = item[1]
    chat_id = item[2]

    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

    alert_text = f"🗑 *Item Untracked*\n\n`{item_title}` has been successfully removed from your tracking list."
    send_telegram_alert(chat_id, alert_text)

    return {"status": "deleted", "id": item_id} 
from fastapi import Request

@app.post("/webhook/lemonsqueezy")
async def lemonsqueezy_webhook(request: Request):
    try:
        payload = await request.json()
        event_name = payload.get("meta", {}).get("event_name")
        data = payload.get("data", {})
        attributes = data.get("attributes", {})
        
        user_email = attributes.get("user_email")
        total_formatted = attributes.get("total_formatted", "$4.99")

        if event_name in ["order_created", "subscription_created"]:
            print(f"Yangi obuna/xarid: {user_email} - {total_formatted}")
            
            # Agar bot orqali bildirishnoma yuborilishi kerak bo'lsa
            custom_data = payload.get("meta", {}).get("custom_data", {})
            chat_id = custom_data.get("chat_id")
            if chat_id:
                pro_alert = (
                    f"🎉 *TABRIKLAYMIZ!*\n\n"
                    f"Siz muvaffaqiyatli *PriceTracker PRO* obunachisi bo‘ldingiz!\n"
                    f"💰 To‘lov: `{total_formatted}`\n"
                    f"⚡ Endi cheksiz tovarlarni kuzatishingiz mumkin!"
                )
                send_telegram_alert(chat_id, pro_alert)

        return {"status": "success"}
    except Exception as e:
        print(f"Webhook xatosi: {e}")
        return {"status": "error", "message": str(e)}from fastapi import Request

@app.post("/webhook/lemonsqueezy")
async def lemonsqueezy_webhook(request: Request):
    try:
        payload = await request.json()
        event_name = payload.get("meta", {}).get("event_name")
        data = payload.get("data", {})
        attributes = data.get("attributes", {})
        
        user_email = attributes.get("user_email")
        total_formatted = attributes.get("total_formatted", "$4.99")

        if event_name in ["order_created", "subscription_created"]:
            print(f"Yangi obuna/xarid: {user_email} - {total_formatted}")
            
            # Agar bot orqali bildirishnoma yuborilishi kerak bo'lsa
            custom_data = payload.get("meta", {}).get("custom_data", {})
            chat_id = custom_data.get("chat_id")
            if chat_id:
                pro_alert = (
                    f"🎉 *TABRIKLAYMIZ!*\n\n"
                    f"Siz muvaffaqiyatli *PriceTracker PRO* obunachisi bo‘ldingiz!\n"
                    f"💰 To‘lov: `{total_formatted}`\n"
                    f"⚡ Endi cheksiz tovarlarni kuzatishingiz mumkin!"
                )
                send_telegram_alert(chat_id, pro_alert)

        return {"status": "success"}
    except Exception as e:
        print(f"Webhook xatosi: {e}")
        return {"status": "error", "message": str(e)}
