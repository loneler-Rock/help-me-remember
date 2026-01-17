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

# --- 2. 工具函式 ---

def clean_price_text(text):
    """清除 $ , 元 等雜訊，只留數字"""
    if not text: return None
    # 轉成字串並移除所有非數字字符
    clean = re.sub(r'[^\d]', '', str(text))
    return int(clean) if clean else None

def extract_json_ld(soup, platform):
    """
    【高階技巧】從 SEO 結構化資料中提取價格
    這是最穩定的方法，因為網站很少改動給 Google 看的資料
    """
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        try:
            data = json.loads(script.string)
            
            # PChome 的結構通常是一個列表，或者單一物件
            if isinstance(data, list):
                for item in data:
                    if item.get('@type') == 'Product':
                        return item
            elif isinstance(data, dict):
                if data.get('@type') == 'Product':
                    return data
        except:
            continue
    return None

# --- 3. 平台解析邏輯 ---

def parse_momo(soup):
    """Momo 解析邏輯 (混合模式)"""
    price = None
    title = "Momo商品"

    # 1. 嘗試 JSON-LD (Momo 有時候有)
    json_data = extract_json_ld(soup, "momo")
    if json_data:
        if 'offers' in json_data and 'price' in json_data['offers']:
            price = clean_price_text(json_data['offers']['price'])
        if 'name' in json_data:
            title = json_data['name']

    # 2. 如果 JSON-LD 沒抓到，使用傳統 CSS Selector
    if not price:
        price_tag = soup.find('span', {'class': 'price'})
        if not price_tag: price_tag = soup.find('span', {'class': 'seoPrice'})
        if not price_tag:
            price_element = soup.select_one("ul.price li.special span.price b")
            if price_element: price_tag = price_element
        
        if price_tag:
            price = clean_price_text(price_tag.text)

    # 3. 標題後補
    if title == "Momo商品":
        og_title = soup.find("meta", property="og:title")
        if og_title: title = og_title["content"]
        else:
            page_title = soup.find("title")
            if page_title: title = page_title.text.split("- momo")[0].strip()

    return price, title

def parse_pchome(soup):
    """PChome 解析邏輯 (JSON-LD 優先)"""
    price = None
    title = "PChome商品"

    # --- 策略 A: JSON-LD (最強大) ---
    # PChome 幾乎一定有這個，且包含了精確價格
    json_data = extract_json_ld(soup, "pchome")
    if json_data:
        # print(f"DEBUG: 找到 JSON-LD 資料") # 除錯用
        if 'offers' in json_data:
            offers = json_data['offers']
            # PChome 的 offers 有時是 list 有時是 dict
            if isinstance(offers, dict) and 'price' in offers:
                price = clean_price_text(offers['price'])
            elif isinstance(offers, list) and len(offers) > 0 and 'price' in offers[0]:
                price = clean_price_text(offers[0]['price'])
        
        if 'name' in json_data:
            title = json_data['name']
            
        if price: return price, title

    # --- 策略 B: Meta Tags (次要穩定) ---
    if not price:
        meta_price = soup.find("meta", property="product:price:amount")
        if not meta_price: meta_price = soup.find("meta", property="og:price:amount")
        
        if meta_price:
            price = clean_price_text(meta_price["content"])

    # --- 策略 C: 暴力視覺搜尋 (最後手段) ---
    if not price:
        # PChome 的價格區塊經常變動，這裡列出幾種常見的
        selectors = [
            "#PriceTotal", 
            ".o-prodPrice__price", 
            ".price-info__price",
            "span[id^='PriceTotal']"
        ]
        for sel in selectors:
            tag = soup.select_one(sel)
            if tag:
                price = clean_price_text(tag.text)
                if price: break

    # 補抓標題
    if title == "PChome商品":
        name_tag = soup.find(id="NickName")
        if name_tag: title = name_tag.text.strip()
        else:
            page_title = soup.find("title")
            if page_title: title = page_title.text.split("- PChome")[0].strip()

    return price, title

def get_product_info(url):
    print(f"🔍 正在解析: {url}...")
    
    platform = "unknown"
    if "momoshop.com.tw" in url:
        platform = "momo"
        print("💡 識別為: Momo 購物網")
    elif "pchome.com.tw" in url:
        platform = "pchome"
        print("💡 識別為: PChome 24h")

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # 隨機 User Agent 避免被擋
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get(url)
        time.sleep(5) # 等待 PChome 的 JS 跑完
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        if platform == "momo":
            return parse_momo(soup)
        elif platform == "pchome":
            return parse_pchome(soup)
        else:
            return parse_momo(soup)

    except Exception as e:
        print(f"❌ 爬蟲發生錯誤: {e}")
        return None, None
    finally:
        driver.quit()

# --- 4. 資料庫儲存 ---

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
        
        print("🚀 啟動 V10.6 結構化數據版...")
        
        current_price, product_title = get_product_info(target_url)
        
        if current_price:
            print(f"💰 成功抓取價格: {current_price}")
            save_price_record(user_id, target_url, current_price, product_title)
        else:
            print(f"❌ 解析失敗: PChome 結構變更，請檢查 JSON-LD 格式。")
    else:
        print("❌ 參數不足")
