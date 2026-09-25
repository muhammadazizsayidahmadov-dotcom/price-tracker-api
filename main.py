import os
import re
import json
import sqlite3
import requests
from bs4 import BeautifulSoup
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title="PriceTracker PRO API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8986494486:AAHJCm_fUlQalFQLjArrnXWZ-kewpDGGavE")
DB_NAME = "tracker.db"

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

def scrape_product_details(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1"
    }

    extracted_title = "Global Product"
    extracted_price = 29.99

    try:
        domain = url.split("//")[-1].split("/")[0].replace("www.", "")
        extracted_title = f"Product from {domain}"
    except Exception:
        pass

    try:
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=12)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")

            # 1. Schema.org JSON-LD orqali aniq narx va nomni olish (Global do'konlar standarti)
            scripts = soup.find_all("script", type="application/ld+json")
            for script in scripts:
                try:
                    data = json.loads(script.string or "{}")
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    
                    if data.get("@type") == "Product" or "offers" in data:
                        if "name" in data:
                            extracted_title = str(data["name"])[:70]
                        offers = data.get("offers", {})
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        price_val = offers.get("price") or offers.get("lowPrice")
                        if price_val:
                            extracted_price = float(str(price_val).replace(",", ""))
                            return extracted_title, extracted_price
                except Exception:
                    continue

            # 2. Tovar sarlavhasi (Title)
            title_elem = (
                soup.find("span", id="productTitle") or  # Amazon
                soup.find("h1", class_=re.compile(r"title|product-title", re.I)) or
                soup.find("h1") or
                soup.find("meta", property="og:title")
            )
            if title_elem:
                raw_title = title_elem.get("content") if title_elem.name == "meta" else title_elem.get_text(strip=True)
                if raw_title:
                    extracted_title = raw_title[:70]

            # 3. Tovar narxini qidirish
            price_elem = (
                soup.find("span", class_="a-price-whole") or  # Amazon
                soup.find("span", id=re.compile(r"priceblock|ourprice|saleprice", re.I)) or
                soup.find("span", class_=re.compile(r"price|current-price|sale-price", re.I)) or
                soup.find("meta", property="product:price:amount") or
                soup.find("meta", property="og:price:amount")
            )

            if price_elem:
                val_str = price_elem.get("content", "") if price_elem.name == "meta" else price_elem.get_text(strip=True)
                digits = re.findall(r"\d+(?:\.\d+)?", val_str.replace(",", ""))
                if digits:
                    val = float(digits[0])
                    if 0.5 <= val <= 25000.0:
                        extracted_price = val

    except Exception as e:
        print(f"Scraper notice: {e}")

    return extracted_title, extracted_price

# Har 1 soatda avtomatik narx pasayishini tekshirish
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
