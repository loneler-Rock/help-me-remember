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
    candidates = []
    # 抓取所有可能的價格數字
    patterns = [r'【(\d+(?:,\d+)*)元', r'\$(\d+(?:,\d+)*)', r'(\d+(?:,\d+)*)元']
    for p in patterns:
        matches = re.finditer(p, text)
        for m in matches:
            val = clean_price_text(m.group(1))
            if val and val > 10: # 文字保底的門檻可以低一點
                candidates.append(val)
    
    if candidates:
        # 文字中通常最大的那個數字是總價 (避免抓到分期金額)
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

# --- 3. 解析邏輯 (V10.16 核心升級: 候選人競選機制) ---

def parse_momo(soup):
    title = "Momo商品"
    candidates = []

    # 1. JSON-LD (權重最高)
    json_data = extract_json_ld(soup, "momo")
    if json_data:
        if 'offers' in json_data and 'price' in json_data['offers']:
            p = clean_price_text(json_data['offers']['price'])
            if p: candidates.append(p)
        if 'name' in json_data: title = json_data['name']
    
    # 2. 視覺標籤 (收集所有可能的價格)
    selectors = [
        "span.price", "span.seoPrice", ".special .price", 
        ".product_price b", ".goodsPrice .price", 
        ".d-price .price", "dd.price b", ".amount", ".checkoutPrice"
    ]
    for sel in selectors:
        tags = soup.select(sel)
        for tag in tags:
            p = clean_price_text(tag.text)
            if p: candidates.append(p)

    # 3. 暴力搜尋 (從 HTML 文字中找 class="price">1234<)
    html_str = str(soup)
    matches = re.findall(r'price[^>]*>.*?(\d{1,3}(?:,\d{3})*)', html_str)
    for m in matches:
        p = clean_price_text(m)
        if p: candidates.append(p)

    # 標題
    if title == "Momo商品":
        og_title = soup.find("meta", property="og:title")
        title = og_title["content"] if og_title else (soup.title.text.split("- momo")[0].strip() if soup.title else title)

    # ★ V10.16 決策邏輯: 從候選人中選出最像價格的那個
    final_price = None
    if candidates:
        # 過濾雜訊
        # 1. 去除小於 100 元的數字 (Momo 商品很少低於 100，除了加購品)
        #    但如果所有候選人都小於 100，那可能真的是便宜貨，就保留
        valid_candidates = [c for c in candidates if c >= 100]
        
        if valid_candidates:
            # 2. 在合理的數字中，選「最大」的那個
            #    (因為分期金額、紅利折抵通常都比總價小)
            final_price = max(valid_candidates)
        else:
            # 如果都小於 100，就從原本的裡面挑最大的 (過濾掉 < 10 的極端值)
            valid_candidates_low = [c for c in candidates if c > 10]
            if valid_candidates_low:
                final_price = max(valid_candidates_low)

    return final_price, title

def parse_pchome(soup):
    price, title = None, "PChome商品"
    # PChome 結構相對單純，維持原樣，但加上基本過濾
    json_data = extract_json_ld(soup, "pchome")
    if json_data:
        if 'offers' in json_data:
            offers = json_data['offers']
            raw_p = None
            if isinstance(offers, dict) and 'price' in offers: raw_p = offers['price']
            elif isinstance(offers, list) and offers and 'price' in offers[0]: raw_p = offers[0]['price']
            if raw_p:
                p = clean_price_text(raw_p)
                if p: price = p
        if 'name' in json_data: title = json_data['name']

    if not price:
        meta = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
        if meta:
            p = clean_price_text(meta["content"])
            if p: price = p
    
    if not price:
        for sel in ["#PriceTotal", ".o-prodPrice__price", ".price-info__price", "span[id^='PriceTotal']"]:
            tag = soup.select_one(sel)
            if tag: 
                p = clean_price_text(tag.text)
                if p: 
                    price = p
                    break

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

    # 保底 (掃描模式下通常無效，因為沒有 text)
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
            
            # 只要抓到的價格不一樣，且新價格是合理的 (大於100)，就更新
            # 防止偶爾抓錯變成 67 元把正確價格蓋掉
            if new_price != old_price:
                # 雙重確認：如果新價格低得離譜 (例如原價3000，新價格67)，可能又是誤判
                if old_price > 1000 and new_price < 100:
                    print(f"⚠️ 價格異常下跌 ({old_price} -> {new_price})，可能是誤判，跳過更新")
                    continue

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
        print("🚀 V10.16 智慧價格區間鎖定版啟動...")
        price, title, clean_url = get_product_info(raw_msg)
        if price:
            save_price_record(uid, raw_msg, price, title, clean_url)
        else:
            print("❌ 失敗: 無法抓取")
    else:
        check_all_products()
