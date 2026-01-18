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
    # 從文字提取時，我們仍然可以用「最大值策略」
    # 因為使用者分享的文字比較乾淨，不太會有銀行促銷資訊
    candidates = []
    patterns = [r'【(\d+(?:,\d+)*)元', r'\$(\d+(?:,\d+)*)', r'(\d+(?:,\d+)*)元']
    for p in patterns:
        matches = re.finditer(p, text)
        for m in matches:
            val = clean_price_text(m.group(1))
            if val and val > 10: 
                candidates.append(val)
    
    if candidates:
        return max(candidates)
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

# --- 3. 解析邏輯 (V10.17 核心升級: 信任分級制) ---

def parse_momo(soup):
    title = "Momo商品"
    
    # === Level 1: JSON-LD (最高信任度) ===
    # 只要這裡有抓到，我們就相信它，直接回傳，不看後面！
    json_data = extract_json_ld(soup, "momo")
    if json_data:
        if 'name' in json_data: title = json_data['name']
        
        if 'offers' in json_data:
            # 有些結構是 offers: { price: ... }
            if isinstance(json_data['offers'], dict) and 'price' in json_data['offers']:
                p = clean_price_text(json_data['offers']['price'])
                if p and p > 10: 
                    # print(f"🎯 命中 JSON-LD 價格: {p}")
                    return p, title
            
            # 有些是 offers: [ { price: ... } ]
            elif isinstance(json_data['offers'], list):
                for offer in json_data['offers']:
                    if 'price' in offer:
                        p = clean_price_text(offer['price'])
                        if p and p > 10:
                            # print(f"🎯 命中 JSON-LD 價格 (List): {p}")
                            return p, title

    # 標題 fallback
    if title == "Momo商品":
        og_title = soup.find("meta", property="og:title")
        title = og_title["content"] if og_title else (soup.title.text.split("- momo")[0].strip() if soup.title else title)

    # === Level 2: 標準視覺標籤 (中等信任度) ===
    # 按照順序找，找到第一個「合理」的就回傳
    # 通常頁面最上面的價格就是主商品價格
    selectors = [
        "span.price",            # 最常見
        "span.seoPrice",         # 常見
        ".product_price b",      # 舊版頁面
        ".special .price",       # 特價區
        ".goodsPrice .price",    # 活動頁
        ".d-price .price",       # 活動頁
        "dd.price b"             # 列表頁
    ]
    
    for sel in selectors:
        tags = soup.select(sel)
        for tag in tags:
            p = clean_price_text(tag.text)
            # 這裡我們設定 > 100，避免抓到 67 元那種怪怪的數字
            if p and p > 100:
                # print(f"🎯 命中 CSS 標籤 ({sel}): {p}")
                return p, title

    # === Level 3: 暴力搜尋 (最低信任度) ===
    # 只有前面都失敗了，才允許用正則去掃 HTML
    # 這裡我們也要很小心，只抓 class="price" 附近的數字
    html_str = str(soup)
    matches = re.findall(r'price[^>]*>.*?(\d{1,3}(?:,\d{3})*)', html_str)
    
    # 這裡如果有多個，我們不選最大的 (怕選到 15000)
    # 我們選第一個出現的 (因為價格通常在上面)
    for m in matches:
        p = clean_price_text(m)
        if p and p > 100:
            # print(f"🎯 命中暴力搜尋: {p}")
            return p, title

    return None, title

def parse_pchome(soup):
    price, title = None, "PChome商品"
    # PChome 也採用優先回傳機制
    
    # 1. JSON-LD
    json_data = extract_json_ld(soup, "pchome")
    if json_data:
        if 'offers' in json_data:
            offers = json_data['offers']
            raw_p = None
            if isinstance(offers, dict) and 'price' in offers: raw_p = offers['price']
            elif isinstance(offers, list) and offers and 'price' in offers[0]: raw_p = offers[0]['price']
            
            p = clean_price_text(raw_p)
            if p and p > 10: return p, (json_data['name'] if 'name' in json_data else title)

    # 2. Meta
    meta = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
    if meta:
        p = clean_price_text(meta["content"])
        if p and p > 10: return p, title

    # 3. Visual
    for sel in ["#PriceTotal", ".o-prodPrice__price", ".price-info__price", "span[id^='PriceTotal']"]:
        tag = soup.select_one(sel)
        if tag: 
            p = clean_price_text(tag.text)
            if p and p > 10: return p, title

    if title == "PChome商品":
        name_tag = soup.find(id="NickName")
        title = name_tag.text.strip() if name_tag else (soup.title.text.split("- PChome")[0].strip() if soup.title else title)
    
    return price, title

# --- 4. 核心功能: 抓取單一商品 ---

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

    # 保底 (僅在單一新增模式下使用)
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
            
            # 邏輯修正：只要價格變動就更新 (漲價也要記，才知道後續降價)
            if new_price != old_price:
                # 安全閥：如果價格變動太劇烈 (例如變成 15000)，再檢查一次
                # 但因為 V10.17 已經改為「JSON-LD優先」，這裡應該很準了，不需要太保守
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
        print("🚀 V10.17 信任分級制版啟動...")
        price, title, clean_url = get_product_info(raw_msg)
        if price:
            save_price_record(uid, raw_msg, price, title, clean_url)
        else:
            print("❌ 失敗: 無法抓取")
    else:
        check_all_products()
