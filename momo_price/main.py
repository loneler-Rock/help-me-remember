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
    # 從文字抓取時，也要避開「期」
    # 這裡先維持簡單邏輯，抓取第一個 > 100 的數字
    matches = re.finditer(r'(?:【|\$|)(\d+(?:,\d+)*)(?:元|)', text)
    for m in matches:
        p = clean_price_text(m.group(1))
        if p and p > 100 and p < 1000000:
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

# --- 3. 解析邏輯 (V10.21: 語意過濾版) ---

def is_installment(tag):
    """
    【V10.21 核心功能】
    檢查這個價格標籤的「周圍」有沒有出現「期」這個字。
    如果有，代表它是分期付款金額，必須忽略！
    """
    if not tag: return False
    
    # 檢查自己有沒有含「期」
    if "期" in tag.text: return True
    
    # 檢查父層 (Parent) 有沒有含「期」 (例如: <div>每期 <span>$244</span></div>)
    parent = tag.parent
    if parent and "期" in parent.text:
        return True
        
    # 檢查爺爺層 (Grandparent) (有些結構比較深)
    grandparent = parent.parent if parent else None
    if grandparent and "期" in grandparent.text:
        # 要小心，不要因為爺爺層有期就殺錯人，但分期金額通常跟「期」靠
