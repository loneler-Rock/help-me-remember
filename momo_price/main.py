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
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get(url)
        time.sleep(3) 
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # --- A. 價格獵殺邏輯 ---
        price = None
        price_tag = soup.find('span', {'class': 'price'})
        if not price_tag:
            price_tag = soup.find('span', {'class': 'seoPrice'})
        if not price_tag:
            price_element = soup.select_one("ul.price li.special span.price b")
            if price_element:
                price_tag = price_element

        if price_tag:
            raw_price = price_tag.text.strip()
            clean_price = re.sub(r'[^\d]', '', raw_price)
            if clean_price:
                price = int(clean_price)
        
        # --- B. 標題獵殺邏輯 (修正版) ---
        title = "未命名商品"
        
        # 策略 1: 優先抓取 Open Graph 標籤 (這是給 FB 分享用的，通常最乾淨)
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"]
        else:
            # 策略 2: 抓取網頁標題 title tag
            page_title = soup.find("title")
            if page_title:
                # Momo 的 title 通常是 "商品名 - momo購物網"，我們把後面的字切掉
                title = page_title.text.split("- momo")[0].strip()
            else:
                # 策略 3: 電腦版專用 ID
                name_tag = soup.find(id="goodsName") 
                if name_tag:
                    title = name_tag.text.strip()

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
        product_data = {
            "user_id": user_id,
            "original_url": url,
            "current_price": price,
            "product_name": title,
            "is_active": True,
            "updated_at": "now()"
        }
        
        existing = supabase.table("products").select("id").eq("original_url", url).eq("user_id", user_id).execute()
        
        product_id = None
        if existing.data:
            product_id = existing.data[0]['id']
            supabase.table("products").update(product_data).eq("id", product_id).execute()
        else:
            result = supabase.table("products").insert(product_data).execute()
            if result.data:
                product_id = result.data[0]['id']

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

if __name__ == "__main__":
    if len(sys.argv) > 2:
        target_url = sys.argv[1]
        user_id = sys.argv[2]
        
        print("🚀 啟動 V10.4 修正版...")
        
        current_price, product_title = get_momo_price(target_url)
        
        if current_price:
            print(f"💰 成功抓取價格: {current_price}")
            save_price_record(user_id, target_url, current_price, product_title)
        else:
            print(f"❌ 解析失敗: 無法抓取價格。")
    else:
        print("❌ 參數不足")
