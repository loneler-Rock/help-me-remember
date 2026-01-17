import os
import re
import sys
import requests
from supabase import create_client, Client

# --- 初始化 ---
# 從環境變數讀取 Supabase 設定
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 建立 Supabase 連線端點
try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("缺少 SUPABASE_URL 或 SUPABASE_KEY 環境變數")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1) # 如果連線失敗直接停止，避免後面報錯

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
    """
    if not text:
        return None

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
        
    # 3. 兜底：抓任何 http 開頭，但排除 googleusercontent 縮圖
    fallback_pattern = r'(https?://[^\s]+)'
    match_fallback = re.search(fallback_pattern, text)
    if match_fallback:
        found_url = match_fallback.group(1)
        if "googleusercontent.com" in found_url:
            print("⚠️ 忽略縮圖網址: " + found_url)
            return None
        return found_url
        
    return None

def parse_google_maps_url(url):
    """
    解析網址中的經緯度
    """
    if not url:
        return None, None

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
        # 未來如果加上 category 欄位，可以在這裡新增 "category": "未分類"
    }
    try:
        supabase.table("locations").insert(data).execute()
        print(f"✅ 成功儲存至 Supabase: {name}")
    except Exception as e:
        print(f"❌ Supabase 寫入錯誤: {e}")

def handle_map_task(raw_message, user_id):
    print("🚀 [Python] 系統啟動，收到地圖任務")
    print(f"📩 原始訊息: {raw_message}")
    print(f"👤 User ID: {user_id}")

    # 1. 提取網址
    target_url = extract_map_url(raw_message)
    
    lat, lng = None, None
    final_url = target_url

    if target_url:
        # 2. 還原短網址
        final_url = resolve_url(target_url)
        # 3. 解析座標
        lat, lng = parse_google_maps_url(final_url)
    
    # 4. 存檔判斷
    if lat and lng:
        # 成功解析
        save_to_supabase(user_id, "新地點 (已解析)", final_url, lat, lng, final_url)
    else:
        # 解析失敗或無網址，但仍存檔 (Fallback)
        print("⚠️ 無法解析座標或無網址，寫入待處理清單")
        # 確保內容不為空，若全空則給個預設字
        fallback_content = final_url if final_url else (raw_message if raw_message else "[無內容]")
        save_to_supabase(user_id, "[待處理] 解析失敗", fallback_content, 0.0, 0.0, fallback_content)

# ★★★ 這就是之前漏掉的啟動區塊 ★★★
if __name__ == "__main__":
    # 從命令列參數讀取輸入 (sys.argv[1] 是訊息, sys.argv[2] 是 user_id)
    if len(sys.argv) > 2:
        msg_arg = sys.argv[1]
        uid_arg = sys.argv[2]
        handle_map_task(msg_arg, uid_arg)
    else:
        print("❌ 錯誤: 參數不足，請提供 raw_message 和 user_id")
