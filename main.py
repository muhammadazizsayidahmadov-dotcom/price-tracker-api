import os
import sqlite3
import requests
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Price & Stock Tracker API")

# Mobil ilovadan CORS orqali keladigan so'rovlarga to'liq ruxsat berish
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Telegram sozlamalari
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

# Telegramga xabar yuborish funksiyasi
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
        response = requests.post(url, json=payload, timeout=10)
        print(f"Telegram status: {response.status_code}, response: {response.text}")
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")

@app.get("/")
def read_root():
    return {"status": "ok", "service": "Price Tracker API"}

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

    items = []
    for row in rows:
        items.append({
            "id": row[0],
            "url": row[1],
            "title": row[2],
            "current_price": float(row[3]),
            "in_stock": bool(row[4]),
            "chat_id": row[5]
        })
    return items

# Yangi tovar qo'shish va Telegramga bildirishnoma jo'natish
@app.post("/items", response_model=ItemResponse)
def add_item(item: ItemCreate):
    cleaned_chat_id = str(item.chat_id).strip()
    clean_url = item.url.strip()

    # Sayt nomidan avtomatik taxminiy sarlavha shakllantirish
    extracted_title = "Online Store Product"
    try:
        domain_part = clean_url.split("//")[-1].split("/")[0].replace("www.", "")
        extracted_title = f"Product from {domain_part}"
    except Exception:
        pass

    default_price = 150000.0
    in_stock_val = 1

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO items (url, title, current_price, in_stock, chat_id) VALUES (?, ?, ?, ?, ?)",
        (clean_url, extracted_title, default_price, in_stock_val, cleaned_chat_id)
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Telegram bot orqali inglizcha alert yuborish
    alert_text = (
        f"🔔 *New Product Tracked!*\n\n"
        f"📦 *Item:* `{extracted_title}`\n"
        f"💰 *Current Price:* {default_price:,.0f} UZS\n"
        f"✅ *Stock Status:* In Stock\n"
        f"🔗 [Open Product Page]({clean_url})"
    )
    send_telegram_alert(cleaned_chat_id, alert_text)

    return {
        "id": new_id,
        "url": clean_url,
        "title": extracted_title,
        "current_price": default_price,
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

    # O'chirilganligi haqida botga xabar yuborish
    alert_text = f"🗑 *Item Untracked*\n\n`{item_title}` has been successfully removed from your tracking list."
    send_telegram_alert(chat_id, alert_text)

    return {"status": "deleted", "id": item_id}
