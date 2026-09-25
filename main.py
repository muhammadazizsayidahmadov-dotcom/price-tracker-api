import os
import sqlite3
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager

TELEGRAM_BOT_TOKEN = "8986494486:AAHJcm_fU1Qa1FQLjArrnXWZ-kewpDGGavE"

def init_db():
    conn = sqlite3.connect("tracker.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            current_price REAL NOT NULL,
            chat_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

class ItemCreate(BaseModel):
    url: str
    chat_id: str

async def send_telegram_alert(chat_id: str, message: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=10.0)
        except Exception as e:
            print(f"Telegram error: {e}")

async def scrape_item(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch page")
            
        soup = BeautifulSoup(response.text, "html.parser")
        
        title_el = soup.find("h1") or soup.find("title")
        title = title_el.get_text(strip=True)[:100] if title_el else "Unknown Product"
        
        price = 0.0
        for selector in [".price", ".product-price", "[data-price]", "span"]:
            el = soup.select_one(selector)
            if el and any(char.isdigit() for char in el.text):
                clean_num = ''.join([c for c in el.text if c.isdigit() or c in ['.', ',']])
                try:
                    price = float(clean_num.replace(',', '.'))
                    break
                except:
                    continue
        if price == 0.0:
            price = 100.0
            
        return title, price

async def check_prices_job():
    conn = sqlite3.connect("tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, url, title, current_price, chat_id FROM items")
    items = cursor.fetchall()
    
    for item in items:
        item_id, url, title, old_price, chat_id = item
        try:
            _, new_price = await scrape_item(url)
            if new_price < old_price:
                msg = (
                    f"🔥 <b>Price Drop Alert!</b>\n\n"
                    f"📦 <b>Item:</b> {title}\n"
                    f"📉 <b>Old Price:</b> {old_price} UZS\n"
                    f"🎉 <b>New Price:</b> {new_price} UZS\n\n"
                    f"👉 <a href='{url}'>View Deal</a>"
                )
                await send_telegram_alert(chat_id, msg)
                cursor.execute("UPDATE items SET current_price = ? WHERE id = ?", (new_price, item_id))
                conn.commit()
        except Exception as e:
            print(f"Error checking item {item_id}: {e}")
            
    conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_prices_job, "interval", minutes=60)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="Price Tracker API", lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "ok", "service": "Price Tracker API"}

@app.get("/items")
def get_items(chat_id: str):
    conn = sqlite3.connect("tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, url, title, current_price, chat_id, created_at FROM items WHERE chat_id = ?", (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": r[0],
            "url": r[1],
            "title": r[2],
            "current_price": r[3],
            "chat_id": r[4],
            "created_at": r[5]
        }
        for r in rows
    ]

@app.post("/items")
async def create_item(payload: ItemCreate):
    conn = sqlite3.connect("tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM items WHERE chat_id = ?", (payload.chat_id,))
    count = cursor.fetchone()[0]
    if count >= 3:
        conn.close()
        raise HTTPException(
            status_code=403, 
            detail="Free tier limit reached (3 items maximum). Upgrade to PRO!"
        )
    
    title, price = await scrape_item(payload.url)
    
    cursor.execute(
        "INSERT INTO items (url, title, current_price, chat_id) VALUES (?, ?, ?, ?)",
        (payload.url, title, price, payload.chat_id)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    
    msg = (
        f"✅ <b>Tracking Started!</b>\n\n"
        f"📦 <b>Item:</b> {title}\n"
        f"💰 <b>Initial Price:</b> {price} UZS\n\n"
        f"<i>We will notify you immediately if the price drops!</i>"
    )
    await send_telegram_alert(payload.chat_id, msg)
    
    return {"id": new_id, "title": title, "current_price": price, "status": "tracking"}

@app.delete("/items/{item_id}")
async def delete_item(item_id: int):
    conn = sqlite3.connect("tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT title, chat_id FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
        
    title, chat_id = item
    
    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    
    if chat_id:
        msg = (
            f"🗑 <b>Removed from Tracking:</b>\n\n"
            f"📌 <b>Item:</b> {title}\n\n"
            f"<i>This item is no longer being monitored.</i>"
        )
        await send_telegram_alert(chat_id, msg)
        
    return {"message": "Item deleted successfully"}
