import os
import sys
import re
import time
import json
import base64
import requests
from urllib.parse import urlparse, parse_qs, unquote # ✅ V10.10 新增: 網址解析工具
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from supabase import create_client, Client

# --- 1. 初始化 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ 警告: 未偵測到 Supabase 環境變數")
        supabase = None
    else:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

# --- 2. 工具函式 ---

def decode_base64_safe(data):
    """自動拆包 Base64"""
    if not data: return ""
    try:
        return base64.b64decode(data).decode('utf-8')
    except:
        return data

def extract_inner_url(url):
    """
    【V10.10 核心】從中轉連結 (reurl.jsp) 提取真正的商品連結
    """
    if not url: return None
    
    # 檢查是否有 goodsUrl 參數 (Momo 的中轉特徵)
    if "goodsUrl=" in url:
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if 'goodsUrl' in params:
                real_url = params['goodsUrl'][0]
                # 這裡可能需要解碼 (例如 %3A 轉成 :)
                return unquote(real_url)
        except Exception as e:
            print(f"⚠️ 解析內部連結失敗: {e}")
            
    return url

def resolve_short_url(url):
    """還原短網址 + 提取內部連結"""
    if not url: return None
    
    # 1. 判斷是否為不需要還原的長網址
    if "momoshop.com.tw/goods/GoodsDetail" in url and "reurl.jsp" not in url:
        return url
        
    print(f"🔄 正在還原短網址: {url} ...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
        # 讓 requests 自動跟隨跳轉
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        final_url = response.url
        
        # ★ V10.10: 檢查是否卡在中轉頁，如果是，再挖一層
        return extract_inner_url(final_url)
        
    except Exception as e:
        print(f"⚠️ 還原網址失敗，將使用原網址: {e}")
        return url

def extract_url_from_text(text):
    """從雜亂文字中抓出網址"""
    if not text: return None
    decoded_text = decode_base64_safe(text)
    print(f"📦 解碼後內容: {decoded_text}") 
    match = re.search(r'(https?://[^\s]+)', decoded_text)
    if match: return match.group(1)
    return decoded_text

def clean_price_text(text):
    if not text: return None
    clean = re.sub(r'[^\d]', '', str(text))
    return int(clean) if clean else None

def extract_json_ld(soup, platform):
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                for item in data:
                    if item.get('@type') == 'Product': return item
            elif isinstance(data, dict):
                if data.get('@type') == 'Product': return data
        except: continue
    return None

# --- 3. 解析邏輯 ---

def parse_momo(soup):
    price, title = None, "Momo商品"
    
    # JSON-LD
    json_data = extract_json_ld(soup, "momo")
    if json_data:
        if 'offers' in json_data and 'price' in json_data['offers']:
            price = clean_price_text(json_data['offers']['price'])
        if 'name' in json_data: title = json_data['name']

    # 視覺標籤
    if not price:
        selectors = ["span.price", "span.seoPrice", "ul.price li.special span.price b", ".priceArea .price", ".special .price"]
        for sel in selectors:
            tag = soup.select_one(sel)
            if tag:
                price = clean_price_text(tag.text)
                if price: break

    # 標題
    if title == "Momo商品":
        og_title = soup.find("meta", property="og:title")
        title = og_title["content"] if og_title else (soup.title.text.split("- momo")[0].strip() if soup.title else title)

    return price, title

def parse_pchome(soup):
    price, title = None, "PChome商品"

    # JSON-LD
    json_data = extract_json_ld(soup, "pchome")
    if json_data:
        if 'offers' in json_data:
            offers = json_data['offers']
            if isinstance(offers, dict) and 'price' in offers: price = clean_price_text(offers['price'])
            elif isinstance(offers, list) and offers and 'price' in offers[0]: price = clean_price_text(offers[0]['price'])
        if 'name' in json_data: title = json_data['name']
        if price: return price, title

    # Meta & Visual
    if not price:
        meta = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
        if meta: price = clean_price_text(meta["content"])
    
    if not price:
        for sel in ["#PriceTotal", ".o-prodPrice__price", ".price-info__price", "span[id^='PriceTotal']"]:
            tag = soup.select_one(sel)
            if tag: 
                price = clean_price_text(tag.text)
                if price: break

    if title == "PChome商品":
        name_tag = soup.find(id="NickName")
        title = name_tag.text.strip() if name_tag else (soup.title.text.split("- PChome")[0].strip() if soup.title else title)

    return price, title

def get_product_info(base64_str):
    raw_url = extract_url_from_text(base64_str)
    
    # ★ V10.10: 這裡會執行雙重解析 (短網址 -> 中轉頁 -> 真實頁)
    real_url = resolve_short_url(raw_url)
    print(f"🔍 準備連線: {real_url}")
    
    platform = "unknown"
    if "momoshop.com.tw" in real_url: platform = "momo"; print("💡 識別為: Momo")
    elif "pchome.com.tw" in real_url: platform = "pchome"; print("💡 識別為: PChome")

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.get(real_url)
        time.sleep(5)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        if platform == "momo": return parse_momo(soup)
        elif platform == "pchome": return parse_pchome(soup)
        else: return parse_momo(soup)
    except Exception as e:
        print(f"❌ 爬蟲錯誤: {e}")
        return None, None
    finally:
        driver.quit()

# --- 4. 儲存 ---

def save_price_record(user_id, raw_url_or_text, price, title):
    if not supabase: return
    print(f"💾 儲存中: {title} | ${price}")
    try:
        clean_url = extract_url_from_text(raw_url_or_text)
        real_url = resolve_short_url(clean_url)

        product_data = {
            "user_id": user_id,
            "original_url": real_url,
            "current_price": price,
            "product_name": title,
            "is_active": True,
            "updated_at": "now()"
        }
        existing = supabase.table("products").select("id").eq("original_url", real_url).eq("user_id", user_id).execute()
        
        if existing.data:
            pid = existing.data[0]['id']
            supabase.table("products").update(product_data).eq("id", pid).execute()
        else:
            res = supabase.table("products").insert(product_data).execute()
            pid = res.data[0]['id'] if res.data else None

        if pid:
            supabase.table("price_history").insert({"product_id": pid, "price": price, "recorded_at": "now()"}).execute()
            print("✅ 成功")
    except Exception as e:
        print(f"❌ 寫入失敗: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        raw_msg = sys.argv[1]
        uid = sys.argv[2]
        
        print("🚀 V10.10 二次解壓縮版啟動...")
        
        price, title = get_product_info(raw_msg)
        if price:
            save_price_record(uid, raw_msg, price, title)
        else:
            print("❌ 失敗: 無法抓取")
    else:
        print("❌ 參數不足")
