import os
import re
import sys
import json
import math
import requests
from supabase import create_client, Client

# --- 初始化 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("缺少 SUPABASE_URL 或 SUPABASE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

# --- 工具函式 ---

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    使用半正矢公式 (Haversine) 計算兩點間距離 (單位: 公里)
    這讓我們不需要依賴資料庫複雜的 spatial_ref_sys，Python 自己算最穩！
    """
    if lat2 is None or lon2 is None: return 99999 # 防呆
    
    R = 6371 # 地球半徑 (km)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def determine_category(title):
    if not title: return "其它"
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "茶", "餅", "甜點", "燒肉", "火鍋", "料理", "Breakfast", "Lunch", "Dinner"]
    travel_keywords = ["景點", "公園", "山", "海", "寺", "宮", "廟", "博物館", "美術館", "步道", "農場", "樂園", "展望", "夜景", "View"]
    for kw in food_keywords:
        if kw in title: return "美食"
    for kw in travel_keywords:
        if kw in title: return "景點"
    return "其它"

def resolve_url(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.head(url, allow_redirects=True, headers=headers, timeout=5)
        return response.url
    except:
        return url

def extract_map_url(text):
    if not text: return None
    match = re.search(r'(https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|www\.google\.com/maps)/[a-zA-Z0-9\./\?=&]+)', text)
    return match.group(1) if match else None

def parse_google_maps_url(url):
    if not url: return None, None
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    match = re.search(r'q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    return None, None

# --- 核心功能 A: 存檔 ---

def handle_save_task(raw_message, user_id):
    print(f"📥 [存檔模式] 收到訊息: {raw_message}")
    target_url = extract_map_url(raw_message)
    final_url = target_url
    lat, lng = None, None
    
    # 這裡未來可以加爬蟲抓網頁標題
    temp_title = raw
