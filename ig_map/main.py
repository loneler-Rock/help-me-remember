import os
import sys
import time
import re
import requests
import json
from supabase import create_client, Client
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import unquote

# --- 1. 初始化設定 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("缺少 SUPABASE_URL 或 SUPABASE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

def reply_line(token, messages):
    if not token:
        print("⚠️ [DEBUG] 沒有 Reply Token，略過回覆")
        return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    try:
        requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})
    except Exception as e:
        print(f"❌ LINE 回覆失敗: {e}")

# --- 2. 輔助工具：OSM 雙重偵探 ---

def parse_osm_category(data):
    """解析 OSM 回傳的 JSON，判斷類別"""
    if not data: return None
    
    # 處理 list (search API 回傳) 和 dict (reverse API 回傳)
    if isinstance(data, list):
        if not data: return None
        item = data[0] # 取信心度最高的第一筆
    else:
        item = data

    # 抓取類別標籤
    osm_category = item.get('category', '') or item.get('class', '') # class 是舊版 key
    osm_type = item.get('type', '')
    
    # 有些 search API 的結構在 'addresstype'
    if not osm_category and 'addresstype' in item:
        osm_category = item['addresstype']

    print(f"   ↳ OSM 屬性分析: Class={osm_category}, Type={osm_type}")

    # --- 判斷邏輯 ---
    food_types = ['restaurant', 'cafe', 'fast_food', 'food_court', 'bar', 'pub', 'ice_cream', 'biergarten']
    if osm_category == 'amenity' and osm_type in food_types: return "美食"
    
    sight_types = ['attraction', 'museum', 'viewpoint', 'artwork', 'gallery', 'zoo', 'theme_park', 'park', 'castle']
    if osm_category in ['tourism', 'historic', 'leisure', 'natural']: return "景點"
    if osm_category == 'amenity' and osm_type in ['place_of_worship']: return "景點"

    if osm_category == 'tourism' and osm_type in ['hotel', 'hostel', 'guest_house', 'motel']: return "住宿"
    
    return None

def get_osm_by_coordinate(lat, lng):
    """策略 1: 座標反查 (Reverse Geocoding)"""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/2.4'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        return parse_osm_category(data)
    except:
        return None

def get_osm_by_name(name, lat, lng):
    """策略 2: 名字搜尋 (Search nearby)"""
    try:
        # 設定搜尋範圍 (Bounding Box)，大約正負 0.002 度 (約 200公尺)
        viewbox = f"{lng-0.002},{lat-0.002},{lng+0.002},{lat+0.002}"
        
        print(f"🕵️ [DEBUG] 啟動 OSM 姓名偵探: 搜尋 '{name}' 於座標附近...")
        url = f"https://nominatim.openstreetmap.org/search?q={name}&format=json&viewbox={viewbox}&bounded=1&limit=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/2.4'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        
        if data:
            print("   ✅ OSM 姓名搜尋命中！")
            return parse_osm_category(data)
        return None
    except Exception as e:
        print(f"⚠️ [DEBUG] OSM 姓名搜尋失敗: {e}")
        return None

def determine_category_smart(title, lat, lng):
    """V2.4 雙重驗
