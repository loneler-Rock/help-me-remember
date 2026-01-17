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

# --- 2. 解析邏輯區 (Momo & PChome 分流) ---

def clean_price_text(text):
    """工具函式: 清除 $ , 元 等雜訊，只留數字"""
    if not text: return None
    # 移除千分位逗號與非數字字元
    clean = re.sub(r'[^\d]', '', text)
    return int(clean) if clean else None

def parse_momo(soup):
    """Momo 專用解析器"""
    price = None
    title = "Momo商品"

    # A. 抓價格
    price_tag = soup.find('span', {'class': 'price'})
    if not price_tag: price_tag = soup.find('span', {'class': 'seoPrice'})
    if not price_tag:
        # 嘗試抓取特價區塊
        price_element = soup.select_one("ul.price li.special span.price b")
        if price_element: price_tag = price_element

    if price_tag:
        price = clean_price_text(price_tag.text)

    # B. 抓標題
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]
    else:
        page_title = soup.find("title")
        if page_title:
            title = page_title.text.split("- momo")[0].strip()

    return price, title

def parse_pchome(soup):
    """PChome 專用解析器"""
    price = None
    title = "PChome商品"

    # A. 抓價格 (PChome 的價格 ID 比較固定)
    # 策略 1: 標準 ID (PriceTotal)
    price_tag = soup.find(id="PriceTotal")
    
    # 策略 2: 新版介面 Class (有時候會在 o-prodPrice__price)
    if not price_tag:
        price_tag = soup.find("span", class_="o-prodPrice__price")
    
    # 策略 3: Meta Tag
    if not price_tag:
        meta_price = soup.find("meta", property="product:price:amount")
        if meta_price:
            return clean_price_text(meta_price["content"]), "PChome商品"

    if price_tag:
        price = clean_price_text(price_tag.text)

    # B. 抓標題
    # PChome 商品名稱通常在 id="NickName"
    name_tag = soup.find(id="NickName")
    if name_tag:
        title = name_tag.text.strip()
    else:
        # 備用: 網頁標題
        page_title = soup.find("title")
        if page_title:
            title = page_title.text.split("- PChome")[0].strip()

    return price, title

def get_product_info(url):
    print(f"🔍 正在解析: {url}...")
    
    # 辨識平台
    platform = "unknown"
    if "momoshop.com.tw" in url:
        platform = "momo"
        print("💡 識別為: Momo 購物網")
    elif "pchome.com.tw" in url:
        platform = "pchome"
        print("💡 識別為: PChome 24h")
    else:
        print("⚠️ 未知平台，將嘗試通用解析...")

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # 偽裝成一般瀏覽器
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get(url)
        # PChome 有時候載入比較慢，給它一點時間
        time.sleep(5) 
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        if platform == "momo":
            return parse_momo(soup)
        elif platform == "pchome":
            return parse_pchome(soup)
        else:
            # 預設嘗試 Momo (或是可以擴充其他平台)
            return parse_momo(soup)

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
        # 準備寫入資料
        product_data = {
            "user_id": user_id,
            "original_url": url,
            "current_price": price,
            "product_name": title,
            "is_active": True,
            "updated_at": "now()"
        }
        
        # 檢查是否存在
        existing = supabase.table("products").select("id").eq("original_url", url).eq("user_id", user_id).execute()
        
        product_id = None
        if existing.data:
            # 更新現有商品
            product_id = existing.data[0]['id']
            supabase.table("products").update(product_data).eq("id", product_id).execute()
        else:
            # 新增商品
            result = supabase.table("products").insert(product_data).execute()
            if result.data:
                product_id = result.data[0]['id']

        # 寫入歷史價格
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
    if len(sys.argv) > 2:
        target_url = sys.argv[1]
        user_id = sys.argv[2]
        
        print("🚀 啟動 V10.5 全能版...")
        
        current_price, product_title = get_product_info(target_url)
        
        if current_price:
            print(f"💰 成功抓取價格: {current_price}")
            save_price_record(user_id, target_url, current_price, product_title)
        else:
            print(f"❌ 解析失敗: 無法抓取價格，請確認網址或網站結構是否變更。")
    else:
        print("❌ 參數不足: 請提供 URL 和 User_ID")
