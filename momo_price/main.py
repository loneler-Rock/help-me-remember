import os
import sys
import re
import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from supabase import create_client, Client

# --- 1. 初始化環境變數與資料庫 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        # 本機測試時，如果沒有環境變數可能會報錯，這裡做個防呆
        print("⚠️ 警告: 未偵測到 Supabase 環境變數 (若在本機測試請忽略)")
        supabase = None
    else:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

# --- 2. 核心功能: 抓取 Momo 價格 ---

def get_momo_price(url):
    print(f"🔍 正在解析: {url}...")
    
    # 設定 Chrome 選項 (Headless 模式)
    chrome_options = Options()
    chrome_options.add_argument("--headless") # 不開啟視窗
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # 重要：偽裝成一般瀏覽器，避免被擋
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get(url)
        time.sleep(3) # 等待網頁載入 (Momo 很多動態載入)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # --- 價格獵殺邏輯 (多重嘗試) ---
        price = None
        
        # 嘗試 1: 抓取常見的 class="price"
        price_tag = soup.find('span', {'class': 'price'})
        if not price_tag:
            # 嘗試 2: 抓取 seoPrice (Momo 常用的另一種標籤)
            price_tag = soup.find('span', {'class': 'seoPrice'})
        if not price_tag:
            # 嘗試 3: 透過 b 標籤抓取 (有時候價格在 <b>999</b>)
            price_element = soup.select_one("ul.price li.special span.price b")
            if price_element:
                price_tag = price_element

        # 如果抓到了標籤，開始清洗數據
        if price_tag:
            raw_price = price_tag.text.strip()
            # 使用 Regex 只保留數字 (剔除 $, ,, 元)
            clean_price = re.sub(r'[^\d]', '', raw_price)
            if clean_price:
                price = int(clean_price)
        
        # 抓取商品名稱 (用來顯示 log)
        title = "未命名商品"
        title_tag = soup.find('h3') # Momo 電腦版標題通常在 h3
        if not title_tag:
            title_tag = soup.find('span', {'class': 'GoodsName'}) # 手機版
        if title_tag:
            title = title_tag.text.strip()

        return price, title

    except Exception as e:
        print(f"❌ 爬蟲發生錯誤: {e}")
        return None, None
    finally:
        driver.quit()

# --- 3. 核心功能: 資料庫操作 ---

def save_price_record(user_id, url, price, title):
    if not supabase:
        print("⚠️ 無法連線資料庫，跳過儲存")
        return

    print(f"💾 正在儲存: {title} | ${price}")
    
    try:
        # 1. 更新或新增 products 表
        # upsert: 如果網址存在就更新，不存在就新增
        product_data = {
            "user_id": user_id,
            "original_url": url,
            "current_price": price,
            "product_name": title, # 假設你有這個欄位，沒有也沒關係
            "is_active": True,
            "updated_at": "now()"
        }
        
        # 先查詢是否已存在 (為了拿 product_id)
        existing = supabase.table("products").select("id").eq("original_url", url).eq("user_id", user_id).execute()
        
        product_id = None
        if existing.data:
            # 更新
            product_id = existing.data[0]['id']
            supabase.table("products").update(product_data).eq("id", product_id).execute()
        else:
            # 新增
            result = supabase.table("products").insert(product_data).execute()
            if result.data:
                product_id = result.data[0]['id']

        # 2. 寫入 price_history (歷史價格)
        if product_id:
            history_data = {
                "product_id": product_id,
                "price": price,
                "recorded_at": "now()"
            }
            supabase.table("price_history").insert(history_data).execute()
            print("✅ 價格歷史已記錄")

    except Exception as e:
        print(f"❌ 資料庫寫入失敗: {e}")

# --- 主程式進入點 ---

if __name__ == "__main__":
    # 接收參數: python main.py "網址" "User_ID"
    if len(sys.argv) > 2:
        target_url = sys.argv[1]
        user_id = sys.argv[2]
        
        print("🚀 啟動新增模式...")
        
        current_price, product_title = get_momo_price(target_url)
        
        if current_price:
            print(f"💰 成功抓取價格: {current_price}")
            save_price_record(user_id, target_url, current_price, product_title)
        else:
            print(f"❌ 解析失敗: 無法抓取價格，請確認網址是否正確或 Momo 已改版。")
            # 這裡不報錯 sys.exit(1)，避免整個 Action 被標記為失敗，但可以考慮傳送錯誤通知
    else:
        print("❌ 參數不足: 請提供 URL 和 User_ID")
