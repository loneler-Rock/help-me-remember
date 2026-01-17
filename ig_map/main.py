import os
import re
import requests
from supabase import create_client, Client

# --- 初始化 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def resolve_url(url):
    """
    將短網址 (goo.gl, maps.app.goo.gl) 還原成真實的長網址
    並過濾掉 googleusercontent 這種縮圖網址
    """
    try:
        # 模擬瀏覽器行為，避免被擋
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.head(url, allow_redirects=True, headers=headers, timeout=10)
        final_url = response.url
        print(f"🔍 [解析] 原始: {url} -> 還原: {final_url}")
        return final_url
    except Exception as e:
        print(f"⚠️ 網址還原失敗: {e}")
        return url

def extract_map_url(text):
    """
    從雜亂的文字中，精準抓出 Google Maps 的連結
    優先抓取 maps.app.goo.gl 或 google.com/maps
    """
    # 這是最強的過濾器：只抓符合地圖特徵的網址
    # 1. 抓 goo.gl 或 maps.app.goo.gl
    short_pattern = r'(https?://(?:maps\.app\.goo\.gl|goo\.gl/maps)/[a-zA-Z0-9]+)'
    # 2. 抓 google.com/maps 長網址
    long_pattern = r'(https?://(?:www\.)?google\.com/maps/[^\s]+)'
    
    match_short = re.search(short_pattern, text)
    if match_short:
        return match_short.group(1)
        
    match_long = re.search(long_pattern, text)
    if match_long:
        return match_long.group(1)
        
    # 如果都沒抓到，但文字裡有 http，試著抓出來看看 (最後手段)
    fallback_pattern = r'(https?://[^\s]+)'
    match_fallback = re.search(fallback_pattern, text)
    if match_fallback:
        found_url = match_fallback.group(1)
        # 如果抓到的是 googleusercontent (縮圖)，我們直接放棄這個，因為它不是地圖
        if "googleusercontent.com" in found_url:
            print("⚠️ 忽略縮圖網址: " + found_url)
            return None
        return found_url
        
    return None

def parse_google_maps_url(url):
    """
    解析網址中的經緯度
    """
    # 處理 @lat,lng,z 格式
    regex_at = r'@(-?\d+\.\d+),(-?\d+\.\d+)'
    match = re.search(regex_at, url)
    if match:
        return float(match.group(1)), float(match.group(2))
        
    # 處理 ?q=lat,lng 格式
    regex_q = r'q=(-?\d+\.\d+),(-?\d+\.\d+)'
    match = re.search(regex_q, url)
    if match:
        return float(match.group(1)), float(match.group(2))
    
    # 處理 search/lat,lng 格式
    regex_search = r'search/(-?\d+\.\d+),\s*(-?\d+\.\d+)'
    match = re.search(regex_search, url)
    if match:
        return float(match.group(1)), float(match.group(2))

    return None, None

def save_to_supabase(user_id, name, address, lat, lng, raw_url):
    """
    寫入資料庫
    """
    data = {
        "user_id": user_id,
        "title": name,
        "address": address,
        "latitude": lat,
        "longitude": lng,
        "created_at": "now()"
    }
    # 嘗試寫入，如果失敗印出錯誤
    try:
        supabase.table("locations").insert(data).execute()
        print(f"✅ 成功儲存: {name}")
    except Exception as e:
        print(f"❌ Supabase 寫入錯誤: {e}")

# --- 主要執行邏輯 ---
def handle_map_task(data):
    print("🚀 [Python] 收到地圖任務")
    raw_message = data.get("raw_message", "")
    user_id = data.get("user_id", "unknown")
    
    print(f"📩 原始訊息: {raw_message}")

    # 1. 從文字中提取網址
    target_url = extract_map_url(raw_message)
    
    lat, lng = None, None
    final_url = target_url

    if target_url:
        # 2. 還原短網址 (取得真實連結)
        final_url = resolve_url(target_url)
        
        # 3. 嘗試解析座標
        lat, lng = parse_google_maps_url(final_url)
    
    # 4. 根據結果寫入資料庫
    if lat and lng:
        # 成功解析出座標
        # 這裡簡單用「新地點」當標題，實際專案通常會再爬取網頁標題(BeautifulSoup)
        # 但為了不讓程式太複雜報錯，我們先存基本資料
        save_to_supabase(user_id, "新地點 (已解析)", final_url, lat, lng, final_url)
    else:
        # ❌ 解析失敗，但我們照樣存！
        print("⚠️ 無法解析座標，寫入待處理清單")
        # 標題設為 [待處理]，地址欄位放入原始文字或網址，座標設為 0
        fallback_content = final_url if final_url else raw_message
        save_to_supabase(user_id, "[待處理] 解析失敗", fallback_content, 0.0, 0.0, fallback_content)
