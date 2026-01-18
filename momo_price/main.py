import os
import sys
import re
import time
import json
import base64
import requests
from urllib.parse import urlparse, parse_qs, unquote
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
    if not data: return ""
    try:
        return base64.b64decode(data).decode('utf-8')
    except:
        return data

def extract_inner_url(url):
    if not url: return None
    if "goodsUrl=" in url:
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if 'goodsUrl' in params:
                return unquote(params['goodsUrl'][0])
        except: pass
    return url

def normalize_momo_url(url):
    if not url: return None
    match = re.search(r'goodsDetail/([A-Za-z0-9]+)', url)
    if match:
        product_id = match.group(1)
        if product_id.startswith("TP"): return url
        if product_id.isdigit():
            return f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={product_id}"
    return url

def resolve_short_url(url):
    if not url: return None
    if "momoshop.com.tw/goods/GoodsDetail" in url and "reurl.jsp" not in url:
        return url
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        final_url = response.url
        inner_url = extract_inner_url(final_url)
        return normalize_momo_url(inner_url)
    except Exception as e:
        return url

def extract_url_from_text(decoded_text):
    if not decoded_text: return None
    match = re.search(r'(https?://[^\s]+)', decoded_text)
    if match: return match.group(1)
    return decoded_text

def clean_price_text(text):
    if not text: return None
    clean = re.sub(r'[^\d]', '', str(text))
    if not clean: return None
    return int(clean)

def extract_price_from_user_text(text):
    if not text: return None
    # 文字保底機制：改為抓取第一個合理的價格
    matches = re.finditer(r'(?:【|\$|)(\d+(?:,\d+)*)(?:元|)', text)
    for m in matches:
        p = clean_price_text(m.group(1))
        # 這裡門檻設為 100，避免抓到 "1入" 或 "2024" 年份(雖然年份通常很大，但如果切分錯誤)
        if p and p > 100 and p < 1000000:
             # 在文字描述中，通常第一個出現的金額就是重點
             return p
    return None

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

# --- 3. 解析邏輯 (V10.19 核心升級: 位置優先法) ---

def parse_momo(soup):
    title = "Momo商品"
    
    # 標題
    og_title = soup.find("meta", property="og:title")
    title = og_title["content"] if og_title else (soup.title.text.split("- momo")[0].strip() if soup.title else title)

    # === 策略 A: 嚴格 CSS 順序掃描 (Level 1) ===
    # 這是依照 Momo 網頁結構，「主價格」最常出現的 CSS 順序
    # 我們抓到第一個符合條件的就立刻 return，不往後找
    
    priority_selectors = [
        "ul.price li.special span.price",   # [活動頁] 最標準的特價紅字
        ".priceArea .price",                # [標準頁] 新版價格區
        ".goodsPrice .price",               # [TP頁] 常見結構
        "li.special span.price",            # [通用] 特價
        ".d-price .price",                  # [TP頁] 另一種結構
        ".product_price .price",            # [舊版]
        "b.price",                          # [通用] 強調價格
        ".seoPrice"                         # [隱藏] 搜尋引擎用價格
    ]

    for sel in priority_selectors:
        # 使用 select (會依照 HTML 出現順序回傳)
        tags = soup.select(sel)
        for tag in tags:
            # 排除被劃掉的 (del)
            if tag.find_parent("del") or "strike" in tag.get("class", []):
                continue
                
            p = clean_price_text(tag.text)
            
            # 過濾器: 排除 < 100 (雜訊/運費) 和 > 20萬 (通常是錯誤或ID)
            # 3680 會通過，202 會通過，但因為我們是「位置優先」，
            # 3680 在 HTML 裡通常比 202 (回饋金) 更早出現，所以會先被抓到並 return
            if p and p > 100 and p < 200000:
                # print(f"🎯 命中 CSS ({sel}): {p}")
                return p, title

    # === 策略 B: JSON-LD (Level 2) ===
    # 如果 CSS 全滅 (例如改版)，才看 JSON
    json_data = extract_json_ld(soup, "momo")
    if json_data:
        if 'name' in json_data and title == "Momo商品": title = json_data['name']
        
        candidates = []
        if 'offers' in json_data:
            offers = json_data['offers']
            if isinstance(offers, dict) and 'price' in offers:
                candidates.append(clean_price_text(offers['price']))
            elif isinstance(offers, list):
                for offer in offers:
                    if 'price' in offer:
                        candidates.append(clean_price_text(offer['price']))
        
        # JSON-LD 裡通常只有一個價格，如果是多個，選最小的(促銷)
        # 這裡也要過濾 < 100
        valid_candidates = [c for c in candidates if c and c > 100]
        if valid_candidates:
             return min(valid_candidates), title

    # === 策略 C: 暴力搜尋 (Level 3 - 最後手段) ===
    # 掃描 HTML 所有含 "price" class 的元素
    # 依然採用「第一個出現」原則
    price_tags = soup.select("[class*='price']")
    for tag in price_tags:
        # 排除刪除線
        if tag.find_parent("del"): continue
        
        p = clean_price_text(tag.text)
        if p and p > 100 and p < 200000:
            # print(f"🎯 命中暴力搜尋: {p}")
            return p, title

    return None, title

def parse_pchome(soup):
    price, title = None, "PChome商品"
    
    # PChome 優先策略
    selectors = ["#PriceTotal", ".o-prodPrice__price", ".price-info__price"]
    for sel in selectors:
        tag = soup.select_one(sel)
        if tag:
            p = clean_price_text(tag.text)
            if p and p > 10: return p, title

    # JSON-LD
    json_data = extract_json_ld(soup, "pchome")
    if json_data:
        if 'name' in json_data: title = json_data['name']
        if 'offers' in json_data:
            offers = json_data['offers']
            raw_p = None
            if isinstance(offers, dict) and 'price' in offers: raw_p = offers['price']
            elif isinstance(offers, list) and offers and 'price' in offers[0]: raw_p = offers[0]['price']
            
            p = clean_price_text(raw_p)
            if p and p > 10: return p, title

    return price, title

# --- 4. 核心功能 ---

def get_product_info(url_or_base64):
    decoded_text = decode_base64_safe(url_or_base64)
    raw_url = extract_url_from_text(decoded_text)
    real_url = resolve_short_url(raw_url)
    
    if not real_url:
        return None, None, None

    print(f"🔍 爬取: {real_url[:60]}...")
    
    platform = "unknown"
    if "momoshop.com.tw" in real_url: platform = "momo"
    elif "pchome.com.tw" in real_url: platform = "pchome"

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled") 
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

    price, title = None, None
    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.get(real_url)
        time.sleep(3) 
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        if platform == "momo": price, title = parse_momo(soup)
        elif platform == "pchome": price, title = parse_pchome(soup)
        else: price, title = parse_momo(soup)
    except Exception as e:
        print(f"❌ 爬蟲錯誤: {e}")
    finally:
        driver.quit()

    if (not price) and decoded_text and (len(decoded_text) < 1000):
        fallback_price = extract_price_from_user_text(decoded_text)
        if fallback_price:
            price = fallback_price
            print(f"✅ 文字保底價格: {price}")
            if not title or title == "Momo商品":
                title = decoded_text.split('\n')[0][:50] 

    return price, title, real_url

# --- 5. 資料庫操作 ---

def save_price_record(user_id, raw_input, price, title, url):
    if not supabase: return
    print(f"💾 存檔: {title} | ${price}")
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
        
        if existing.data:
            pid = existing.data[0]['id']
            # V10.19: 確保每次都更新
            supabase.table("products").update(product_data).eq("id", pid).execute()
        else:
            res = supabase.table("products").insert(product_data).execute()
            pid = res.data[0]['id'] if res.data else None

        if pid:
            supabase.table("price_history").insert({"product_id": pid, "price": price, "recorded_at": "now()"}).execute()
            print("✅ 成功")
    except Exception as e:
        print(f"❌ 寫入失敗: {e}")

def check_all_products():
    if not supabase: return
    print("🚀 啟動全庫掃描模式 (Cron Job)...")
    
    try:
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
    except Exception as e:
        print(f"❌ 讀取資料庫失敗: {e}")
        return

    if not products:
        print("📦 目前沒有商品需要檢查")
        return

    print(f"📦 共有 {len(products)} 個商品待檢查")
    
    for prod in products:
        pid = prod['id']
        p_url = prod['original_url']
        p_name = prod['product_name']
        old_price = prod['current_price']
        
        print(f"---------------------------------------------------")
        print(f"🔎 檢查: {p_name[:15]}... (原價: {old_price})")
        
        new_price, new_title, clean_url = get_product_info(p_url)
        
        if new_price:
            print(f"💰 最新價格: {new_price}")
            
            if new_price != old_price and new_price > 50:
                supabase.table("products").update({
                    "current_price": new_price,
                    "updated_at": "now()"
                }).eq("id", pid).execute()

                supabase.table("price_history").insert({
                    "product_id": pid, 
                    "price": new_price, 
                    "recorded_at": "now()"
                }).execute()
                
                if new_price < old_price:
                    diff = old_price - new_price
                    print(f"🎉 降價了！ 便宜了 ${diff} ({old_price} -> {new_price})")
        else:
            print("⚠️ 無法抓取價格，跳過")
        
        time.sleep(5)

if __name__ == "__main__":
    if len(sys.argv) > 2:
        raw_msg = sys.argv[1]
        uid = sys.argv[2]
        print("🚀 V10.19 位置優先法版啟動...")
        price, title, clean_url = get_product_info(raw_msg)
        if price:
            save_price_record(uid, raw_msg, price, title, clean_url)
        else:
            print("❌ 失敗: 無法抓取")
    else:
        check_all_products()
