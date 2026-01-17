import os
import re
import sys
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

def determine_category(title):
    """
    根據地點名稱簡單判斷分類
    """
    if not title:
        return "其它"
    
    # 關鍵字清單 (您可以隨時回來這裡擴充)
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "茶", "餅", "甜點", "燒肉", "火鍋", "料理"]
    travel_keywords = ["景點", "公園", "山", "海", "寺", "宮", "廟", "博物館", "美術館", "步道", "農場", "樂園", "展望", "夜景"]
    
    for kw in food_keywords:
        if kw in title:
            return "美食"
            
    for kw in travel_keywords:
        if kw in title:
            return "景點"
            
    return "其它"

def resolve_url(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.head(url, allow_redirects=True, headers=headers, timeout=10)
        return response.url
    except:
        return url

def extract_map_url(text):
    if not text: return None
    # 抓短網址
    short_pattern = r'(https?://(?:maps\.app\.goo\.gl|goo\.gl/maps)/[a-zA-Z0-9]+)'
    match = re.search(short_pattern, text)
    if match: return match.group(1)
    
    # 抓長網址
    long_pattern = r'(https?://(?:www\.)?google\.com/maps/[^\s]+)'
    match = re.search(long_pattern, text)
    if match: return match.group(1)
    
    return None

def parse_google_maps_url(url):
    if not url: return None, None
    
    # 嘗試抓取座標 @lat,lng
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    
    # 嘗試抓取 q=lat,lng
    match = re.search(r'q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    
    return None, None

def save_to_supabase(user_id, name, address, lat, lng, raw_url):
    # ★ 自動判斷分類
    category = determine_category(name)
    
    data = {
        "user_id": user_id,
        "title": name,
        "url": raw_url,
        "address": address,
        "latitude": lat,
        "longitude": lng,
        "category": category, # ★ 寫入分類
        "created_at": "now()"
    }
    
    try:
        # ★ 改存到新的 map_spots 表格
        supabase.table("map_spots").insert(data).execute()
        print(f"✅ 成功儲存: {name} [{category}]")
    except Exception as e:
        print(f"❌ 寫入失敗: {e}")

def handle_map_task(raw_message, user_id):
    print(f"🚀 處理訊息: {raw_message}")
    
    target_url = extract_map_url(raw_message)
    final_url = target_url
    lat, lng = None, None
    
    # 預設標題 (真實專案通常會爬取網頁 Title，這裡先用簡單邏輯)
    # 如果有分類需求，未來這裡可以加強爬蟲去抓 Google Map 的店名
    temp_title = raw_message[:20] if raw_message else "未命名地點"

    if target_url:
        final_url = resolve_url(target_url)
        lat, lng = parse_google_maps_url(final_url)
    
    if lat and lng:
        save_to_supabase(user_id, f"新地點-{lat:.2f}", final_url, lat, lng, final_url)
    else:
        print("⚠️ 無法解析座標，寫入待處理")
        save_to_supabase(user_id, "待處理地點", final_url if final_url else raw_message, 0.0, 0.0, final_url)

if __name__ == "__main__":
    if len(sys.argv) > 2:
        handle_map_task(sys.argv[1], sys.argv[2])
    else:
        print("❌ 參數不足")
