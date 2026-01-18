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
    # 從文字提取時，嘗試找最低的合理價格 (通常文字裡也會有定價和特價)
    candidates = []
    patterns = [r'【(\d+(?:,\d+)*)元', r'\$(\d+(?:,\d+)*)', r'(\d+(?:,\d+)*)元']
    for p in patterns:
        matches = re.finditer(p, text)
        for m in matches:
            val = clean_price_text(m.group(1))
            if val and val > 100: 
                candidates.append(val)
    
    if candidates:
        return min(candidates) # 假設文字裡有 "原價3980 特價3680"，我們取 3680
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

# --- 3. 解析邏輯 (V10.18 核心升級: 促銷價狙擊手) ---

def parse_momo(soup):
    title = "Momo商品"
    
    # 標題
    og_title = soup.find("meta", property="og:title")
    title = og_title["content"] if og_title else (soup.title.text.split("- momo")[0].strip() if soup.title else title)

    # === 策略 A: 視覺 CSS 促銷區塊 (Level 1 - 最優先) ===
    # 我們不再只看 span.price，我們要看它是不是在 "special" (促銷) 區塊裡
    # 這是 Momo 最典型的特價結構: <li class="special"> <span>促銷價</span> <span class="price">3,680</span> </li>
    
    promo_selectors = [
        "ul.price li.special span.price",  # 標準特價區
        ".priceArea .price",               # 新版特價區
        ".product_price .price",           # 另一種結構
        "b.price"                          # 強調的價格
    ]

    for sel in promo_selectors:
        tag = soup.select_one(sel)
        if tag:
            p = clean_price_text(tag.text)
            # 這裡我們稍微放寬下限，但嚴格過濾上限 (太大的可能是紅利點數)
            if p and p > 50 and p < 200000:
                # print(f"🎯 命中促銷區塊 ({sel}): {p}")
                return p, title

    # === 策略 B: JSON-LD (Level 2 - 次要) ===
    # 如果網頁上找不到特價 CSS，才回頭看 JSON-LD
    # 但這裡要小心，JSON-LD 可能是原價
    json_data = extract_json_ld(soup, "momo")
    if json_data:
        if 'name' in json_data and title == "Momo商品": title = json_data['name']
        
        json_price = None
        if 'offers' in json_data:
            offers = json_data['offers']
            if isinstance(offers, dict) and 'price' in offers:
                json_price = clean_price_text(offers['price'])
            elif isinstance(offers, list):
                # 如果有多個 offer (例如有低價和高價)，選最低的！
                prices = []
                for offer in offers:
                    if 'price' in offer:
                        v = clean_price_text(offer['price'])
                        if v: prices.append(v)
                if prices:
                    json_price = min(prices)
        
        if json_price and json_price > 50:
             # print(f"🎯 命中 JSON-LD: {json_price}")
             return json_price, title

    # === 策略 C: 廣泛搜尋 (Level 3 - 保底) ===
    # 如果上面都沒抓到，掃描所有可能是價格的地方，然後取 "最小值" (但要大於100)
    # 原理：如果有 "3980" 和 "3680" 同時出現，我們想要 3680
    candidates = []
    
    # 收集所有 class="price"
    price_tags = soup.select(".price, .seoPrice")
    for tag in price_tags:
        # 排除被劃掉的價格 (原價)
        if "strike" in tag.get("class", []) or tag.find_parent("del"):
            continue
            
        p = clean_price_text(tag.text)
        if p and p > 100:
            candidates.append(p)
            
    # 收集 HTML 裡面的數字
    html_str = str(soup)
    matches = re.findall(r'price[^>]*>.*?(\d{1,3}(?:,\d{3})*)', html_str)
    for m in matches:
        p = clean_price_text(m)
        if p and p > 100:
            candidates.append(p)
            
    if candidates:
        # 過濾掉太大的 (避免 15000 滿額贈)
        # 假設一般商品不會超過 50萬 (除非你真的是賣車)
        valid_candidates = [c for c in candidates if c < 500000]
        
        if valid_candidates:
            # ★ 關鍵改變：取最小值！ (Assume Lowest Price is the Promo Price)
            # 在排除掉 < 100 的雜訊後，最小的通常是促銷價
            best_price = min(valid_candidates)
            # print(f"🎯 命中候選價格最小值: {best_price}")
            return best_price, title

    return None, title

def parse_pchome(soup):
    price, title = None, "PChome商品"
    # PChome 邏輯: 優先找 "目前售價" 區塊
    
    # 1. 視覺區塊 (PChome 的價格 ID 很明確)
    selectors = ["#PriceTotal", ".o-prodPrice__price", ".price-info__price"]
    for sel in selectors:
        tag = soup.select_one(sel)
        if tag:
            p = clean_price_text(tag.text)
            if p and p > 10: return p, title

    # 2. JSON-LD
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

    # 保底
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
            # V10.18: 每次抓取都更新 current_price，確保是最新
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
            
            # V10.18: 更嚴格的更新邏輯
            # 如果抓到的價格比原價低，或不同，我們都更新
            # 但要避免抓到 0 或 極小值
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
        print("🚀 V10.18 視覺促銷優先版啟動...")
        price, title, clean_url = get_product_info(raw_msg)
        if price:
            save_price_record(uid, raw_msg, price, title, clean_url)
        else:
            print("❌ 失敗: 無法抓取")
    else:
        check_all_products()
