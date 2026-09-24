import time
import requests
import re
import sqlite3
from datetime import datetime
from bs4 import BeautifulSoup

# 1. Sozlamalar
BOT_TOKEN = "8986494486:AAHJCm_fUlQalFQLjArrnXWZ-kewpDGGavE"
CHAT_ID = "8130935215"
DB_NAME = "tracker.db"

session = requests.Session()

# 2. Ma'lumotlar bazasini ishga tushirish
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Mahsulotlar jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            title TEXT,
            current_price REAL,
            is_available BOOLEAN,
            last_checked TEXT
        )
    """)
    
    # Narxlar tarixi jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            price REAL,
            recorded_at TEXT,
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    """)
    conn.commit()
    conn.close()

# Telegramga xabar yuborish
def send_telegram_alert(message):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        session.post(api_url, json=payload, timeout=10)
    except Exception as e:
        print(f"Xabar yuborishda xatolik: {e}")

# Tovar ma'lumotlarini sahifadan o'qish
def check_product_price(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = session.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.find("h1").get_text(strip=True)
            
            price_raw = soup.find("p", class_="price_color").get_text(strip=True)
            price_clean = re.sub(r"[^\d.]", "", price_raw)
            price = float(price_clean)
            
            stock_text = soup.find("p", class_="instock availability").get_text(strip=True)
            is_available = "In stock" in stock_text

            return {"title": title, "price": price, "available": is_available}
        return None
    except Exception as e:
        print(f"Skraping xatosi ({url}): {e}")
        return None

# Bazaga yangi tovar qo'shish funksiyasi
def add_product(url):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR IGNORE INTO products (url) VALUES (?)", (url,))
        conn.commit()
    finally:
        conn.close()

# Barcha tovarlarni tekshirish sikli
def monitor_all_products():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, url, title, current_price, is_available FROM products")
    products = cursor.fetchall()
    conn.close()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for prod_id, url, old_title, old_price, old_available in products:
        data = check_product_price(url)
        if not data:
            continue

        new_title = data["title"]
        new_price = data["price"]
        new_available = data["available"]

        print(f"[{now}] Tekshirildi: {new_title} | Narx: ${new_price}")

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Birinchi marta bazaga yozilayotgan bo'lsa
        if old_price is None:
            cursor.execute("""
                UPDATE products 
                SET title = ?, current_price = ?, is_available = ?, last_checked = ? 
                WHERE id = ?
            """, (new_title, new_price, new_available, now, prod_id))
            
            cursor.execute("INSERT INTO price_history (product_id, price, recorded_at) VALUES (?, ?, ?)",
                           (prod_id, new_price, now))
            
            send_telegram_alert(f"✅ *Yangi tovar bazaga kiritildi:*\n📦 {new_title}\n💵 Narxi: ${new_price}")

        # Narx o'zgargan bo'lsa
        elif new_price != old_price:
            diff = new_price - old_price
            percent = (diff / old_price) * 100
            emoji = "📉 Narx arzonlashdi!" if diff < 0 else "📈 Narx ko'tarildi!"

            alert_text = (
                f"⚠️ *{emoji}*\n\n"
                f"📦 *Mahsulot:* {new_title}\n"
                f"💵 *Eski narx:* ${old_price}\n"
                f"🏷 *Yangi narx:* ${new_price} ({percent:+.1f}%)\n"
                f"🔗 [Tovar sahifasi]({url})"
            )
            send_telegram_alert(alert_text)

            cursor.execute("""
                UPDATE products 
                SET current_price = ?, is_available = ?, last_checked = ? 
                WHERE id = ?
            """, (new_price, new_available, now, prod_id))

            cursor.execute("INSERT INTO price_history (product_id, price, recorded_at) VALUES (?, ?, ?)",
                           (prod_id, new_price, now))

        # Faqat tekshirilgan vaqtni yangilash
        else:
            cursor.execute("UPDATE products SET last_checked = ? WHERE id = ?", (now, prod_id))

        conn.commit()
        conn.close()

# Asosiy ishga tushirish bloki
if __name__ == "__main__":
    init_db()
    
    # Sinov uchun 2 xil tovar qo'shamiz
    test_urls = [
        "http://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        "http://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html"
    ]
    for u in test_urls:
        add_product(u)

    print("🚀 SQLite bazasi ulandi. Ko'p tovarli monitoring boshlandi...")
    
    while True:
        monitor_all_products()
        print("Kutish rejimi: 30 soniyadan so'ng qayta tekshiriladi...\n")
        time.sleep(30)