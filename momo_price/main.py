import os
import sys
import re
import time
import json
import base64  # ✅ 關鍵工具: 拆包器
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
    """
    【V10.8 新功能】自動拆包 Base64
    """
    if not data: return ""
    try:
        # 嘗試解碼 (Make 傳過來的是 Base64 編碼的亂碼)
        decoded = base64.b64decode(data).decode('utf-8')
        return decoded
    except:
        # 如果不是 Base64 (例如手動測試傳純文字)，直接回傳原字串
        return data

def extract_url_from_text(text):
    """從雜亂文字中抓出網址"""
    if not text: return None
    
    # ★ 第一步：先拆包
    decoded_text = decode_base64_safe(text)
    print(f"📦 解碼後內容: {decoded_text}") 
    
    # ★ 第二步：抓網址
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
        if 'name' in json_data:
