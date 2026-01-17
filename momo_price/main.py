import os
import sys
import re
import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
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

# --- 2. 工具函式 (新增: 網址萃取) ---

def extract_url_from_text(text):
    """
    【V10.7 新功能】從手機分享的雜亂文字中，精準抓出網址
    """
    if not text: return None
    
    # 尋找 http 或 https 開頭，直到遇到空白或結尾的字串
    match = re.search(r'(https?://[^\s]+)', text)
    if match:
        return match.group(1)
    return text # 如果沒抓到，就回傳原本的試試看

def clean_price_text(text):
    """清除 $ , 元 等雜訊，只留數字"""
    if not text: return None
    clean = re.sub(r'[^\d]', '', str(text))
    return int(clean) if clean else None

def extract_json_ld(soup, platform):
    """從 SEO 結構化資料中提取價格"""
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                for item in data:
                    if item.get('@type') == 'Product': return item
            elif isinstance(data, dict):
                if data.get('@type') == 'Product': return data
        except:
            continue
    return None

# --- 3. 平台解析邏輯 (保留 V10.6 的強大功能) ---

def parse_momo(soup):
    price = None
    title = "Momo商品"
    
    # 1. JSON-LD
    json_data = extract_json_ld(soup, "momo")
    if json_data:
        if 'offers' in json_data and 'price' in json_data['offers']:
            price = clean_price_text(json_data['offers']['price'])
        if 'name' in json_data: title = json_data['name']

    # 2. 視覺標籤
    if not price:
        price_tag = soup.find('span', {'class': 'price'})
        if not price_tag: price_tag = soup.find('span', {'class': 'seoPrice'})
        if not price_tag:
            price_element = soup.select_one("ul.price li.special span.price b")
            if price_element: price_tag = price_element
        if price_tag: price = clean_price_text(price_tag.text)

    # 3. 標題後補
    if title == "Momo商品":
        og_title = soup.find("meta", property="og:title")
        if og_title: title = og_title["content"]
        else:
            page_title = soup.find("title")
            if page_title: title = page_title.text.split("- momo")[0].strip()

    return price, title

def parse_pchome(soup):
    price = None
    title = "PChome商品"

    # A. JSON-LD
    json_data = extract_json_ld(soup, "pchome")
    if json_data:
        if 'offers' in json_data:
            offers = json_data['offers']
            if isinstance(offers, dict) and 'price' in offers:
                price = clean_price_text(offers['price'])
            elif isinstance(offers, list) and len(offers) > 0 and 'price' in offers[0]:
                price = clean_price_text(offers[0]['price'])
        if 'name' in json_data: title = json_data['name']
        if price: return price, title

    # B. Meta Tags
    if not price:
        meta_price = soup.find("meta", property="product:price:amount")
        if not meta_price: meta_price = soup.find("meta", property="og:price:amount")
        if meta_price: price = clean_price_text(meta_price["content"])

    # C. 視覺搜尋
    if not price:
        selectors = ["#PriceTotal", ".o-prodPrice__price", ".price-info__price", "span[id^='PriceTotal']"]
        for sel in selectors:
            tag = soup.select_one(sel)
            if tag:
                price = clean_price_text(tag.text)
                if price: break

    if title == "PChome商品":
        name_tag = soup.find(id="NickName")
        if name_tag: title = name_tag.text.strip()
        else:
            page_title = soup.find("title")
            if page_title: title = page_title.text.split("- PChome")[0].strip()

    return price, title

def get_product_info(url):
    print(f"🔍 收到原始連結: {url}...")
    
    # ★ V10.7 關鍵修正: 先清洗網址
    clean_url = extract_url_from_text(url)
    if clean_url != url:
        print(f"🧹 清洗後網址: {clean_url}")
    
    platform = "unknown"
    if "momoshop.com.tw" in clean_url:
        platform = "momo"
        print("💡 識別為: Momo 購物網")
    elif "pchome.com.tw" in clean_url:
        platform = "pchome"
        print("💡 識別為: PChome 24h")

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get(clean_url)
        time.sleep(5)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        if platform == "momo": return parse_momo(soup)
        elif platform == "pchome": return parse_pchome(soup)
        else: return parse_momo(soup)

    except Exception as e:
        print(f"❌ 爬蟲發生錯誤: {e}")
        return None, None
    finally:
        driver.quit()

# --- 4. 資料庫儲存 ---

def save_price_record(user_id, url, price, title):
    if not supabase: return
    print(f"💾 正在儲存: {title} | ${price}")
    try:
        # 這裡也要確保儲存的是乾淨的 URL
        clean_url = extract_url_from_text(url)
        
        product_data = {
            "user_id": user_id,
            "original_url": clean_url, # 存乾淨的
            "current_price": price,
            "product_name": title,
            "is_active": True,
            "updated_at": "now()"
        }
        existing = supabase.table("products").select("id").eq("original_url", clean_url).eq("user_id", user_id).execute()
        product_id = None
        if existing.data:
            product_id = existing.data[0]['id']
            supabase.table("products").update(product_data).eq("id", product_id).execute()
        else:
            result = supabase.table("products").insert(product_data).execute()
            if result.data: product_id = result.data[0]['id']

        if product_id:
            supabase.table("price_history").insert({"product_id": product_id, "price": price, "recorded_at": "now()"}).execute()
            print("✅ 價格歷史已記錄")
    except Exception as e:
        print(f"❌ 資料庫寫入失敗: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        # 接收整串髒髒的訊息
        raw_message = sys.argv[1] 
        user_id = sys.argv[2]
        
        print("🚀 啟動 V10.7 手機抗噪版...")
        
        # 程式內部會自己洗乾淨
        current_price, product_title = get_product_info(raw_message)
        
        if current_price:
            print(f"💰 成功抓取價格: {current_price}")
            save_price_record(user_id, raw_message, current_price, product_title)
        else:
            print(f"❌ 解析失敗: 無法抓取價格。")
    else:
        print("❌ 參數不足")
