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
        if product_id.startswith("TP"): return url # TP 保持原樣
        if product_id.isdigit():
            return f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={product_id}"
    return url

def resolve_short_url(url):
    if not url: return None
    if "momoshop.com.tw/goods/GoodsDetail" in url and "reurl.jsp" not in url:
        return url
    print(f"🔄 正在還原短網址: {url} ...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        final_url = response.url
        inner_url = extract_inner_url(final_url)
        return normalize_momo_url(inner_url)
    except Exception as e:
        print(f"⚠️ 還原網址失敗: {e}")
        return url

def extract_url_from_text(decoded_text):
    if not decoded_text: return None
    match = re.search(r'(https?://[^\s]+)', decoded_text)
    if match: return match.group(1)
    return decoded_text

def clean_price_text(text):
    """清洗價格文字，並過濾不合理的低價"""
    if not text: return None
    # 只留數字
    clean = re.sub(r'[^\d]', '', str(text))
    
    if not clean: return None
    
    price = int(clean)
    
    # ★ V10.14 核心修正：低價過濾器
    # 如果價格小於 10 元，極有可能是「1入」、「1件」或「0元運費」，直接忽略
    if price < 10:
        # print(f"⚠️ 忽略不合理低價: {price}")
        return None
        
    return price

def extract_price_from_user_text(text):
    """文字保底機制 (含低價過濾)"""
    if not text: return None
    print("🛡️ 啟動保底機制: 嘗試從文字提取價格...")
    
    # 策略 A: 找 【xxxx元】
    matches = re.finditer(r'【(\d+(?:,\d+)*)元', text)
    for m in matches:
        p = clean_price_text(m.group(1))
        if p: return p
    
    # 策略 B: 找 $xxxx
    matches = re.finditer(r'\$(\d+(?:,\d+)*)', text)
    for m in matches:
        p = clean_price_text(m.group(1))
        if p: return p
        
    # 策略 C: 找 xxxx元 (最寬鬆)
    matches = re.finditer(r'(\d+(?:,\d+)*)元', text)
    for m in matches:
        p = clean_price_text(m.group(1))
        if p: return p
    
    return None

def extract_json_ld(soup, platform):
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                for item
