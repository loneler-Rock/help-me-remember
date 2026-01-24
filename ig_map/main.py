import os
import re
import sys
import json
import math
import requests
from supabase import create_client, Client
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

# --- LINE 回覆工具 ---
def reply_line(token, messages):
    if not token:
        print("⚠️ [DEBUG] 沒有 Reply Token，略過回覆")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}"
    }
    body = {
        "replyToken": token,
        "messages": messages
    }
    
    try:
        r = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=body)
        print(f"📤 LINE 回覆狀態: {r.status_code} {r.text}")
    except Exception as e:
        print(f"❌ LINE 回覆失敗: {e}")

# --- 2. 工具函式 ---

def calculate_distance(lat1, lon1, lat2, lon2):
    if lat2 is None or lon2 is None: return 99999
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def get_url_and_content(url):
    """
    V1.3 升級：同時回傳「最終網址」和「網頁 HTML 內容」
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        # 取得完整回應
        response = requests.get(url, allow_redirects=True, headers=headers, timeout=15)
        return response.url, response.text
    except Exception as e:
        print(f"⚠️ [DEBUG] 網頁請求失敗: {e}")
        return url, ""

def extract_map_url(text):
    if not text: return None
    # 廣域捕獲
    match = re.search(r'(https?://[^\s]*(?:google|goo\.gl)[^\s]*)', text)
    return match.group(1) if match else None

def parse_coordinates(url, html_content=""):
    """
    V1.3 核心：雙重解析機制 (先看網址，再看 HTML)
    """
    if not url: return None, None
    url = unquote(url)

    # --- 策略 A: 從網址解析 (優先) ---
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    
    match = re.search(r'q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    
    match_lat = re.search(r'!3d(-?\d+\.\d+)', url)
    match_lng = re.search(r'!4d(-?\d+\.\d+)', url)
    if match_lat and match_lng: return float(match_lat.group(1)), float(match_lng.group(2))

    # --- 策略 B: 從 HTML 內容解析 (針對 googleusercontent) ---
    if html_content:
        print("🕵️ [DEBUG] 網址解析失敗，啟動 HTML 深度搜查...")
        
        # 搜尋 meta tag 中的 og:image (通常包含 staticmap 連結)
        # 範例: .../staticmap?center=24.123,121.123&zoom=...
        if "center=" in html_content:
            match = re.search(r'center=(-?\d+\.\d+)%2C(-?\d+\.\d+)', html_content) # URL encoded comma
            if not match:
                match = re.search(r'center=(-?\d+\.\d+),(-?\d+\.\d+)', html_content) # Normal comma
            
            if match:
                print("🕵️ [DEBUG] 從 HTML meta (center) 找到座標！")
                return float(match.group(1)), float(match.group(2))

        # 搜尋 markers
        if "markers=" in html_content:
            match = re.search(r'markers=(-?\d+\.\d+)%2C(-?\d+\.\d+)', html_content)
            if not match:
                match = re.search(r'markers=(-?\d+\.\d+),(-?\d+\.\d+)', html_content)
            
            if match:
                print("🕵️ [DEBUG] 從 HTML meta (markers) 找到座標！")
                return float(match.group(1)), float(match.group(2))
                
    return None, None

def determine_category(title):
    if not title: return "其它"
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉", "小吃"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "Hotel", "民宿", "景點", "文創", "步道"]
    for kw in food_keywords:
        if kw in title: return "美食"
    for kw in travel_keywords:
        if kw in title: return "景點"
    return "其它"

# --- 3. 核心功能 A: 存檔模式 ---

def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 開始處理...")
    print(f"🕵️ [DEBUG] 收到原始字串 -> [{raw_message}]")

    if not raw_message or not raw_message.strip():
        return

    # 1. 抓取網址
    target_url = extract_map_url(raw_message)
    if not target_url and "google" in raw_message and "http" in raw_message:
         target_url = raw_message.strip()

    print(f"🕵️ [DEBUG] 判定處理網址 -> [{target_url}]")

    temp_title = raw_message[:30].replace("\n", " ") if raw_message else "未命名地點"
    message_to_user = ""

    if target_url:
        # 2. 取得網址 與 HTML 內容 (V1.3 關鍵)
        final_url, html_content = get_url_and_content(target_url)
        print(f"🕵️ [DEBUG] 還原後的長網址 -> [{final_url}]")
        # print(f"🕵️ [DEBUG] HTML 前100字 -> {html_content[:100]}") # Debug 用
        
        # 嘗試從網址或 HTML 抓標題
        if "/place/" in final_url:
            try:
                parts = unquote(final_url).split("/place/")[1].split("/")[0]
                temp_title = parts.replace("+", " ")
            except:
                pass
        
        # 3. 雙重解析座標
        lat, lng = parse_coordinates(final_url, html_content)
        print(f"🕵️ [DEBUG] 最終座標結果 -> Lat: {lat}, Lng: {lng}")
        
        category = determine_category(temp_title)

        if lat and lng:
            data = {
                "user_id": user_id,
                "location_name": temp_title,
                "google_map_url": final_url,
                "address": final_url,
                "latitude": lat,
                "longitude": lng,
                "category": category,
                "geom": f"POINT({lng} {lat})",
                "created_at": "now()"
            }
            try:
                supabase.table("map_spots").insert(data).execute()
                print(f"✅ 成功寫入資料庫: {temp_title}")
                message_to_user = f"✅ 已收藏地點！\n類別: {category}\n標題: {temp_title}"
            except Exception as e:
                print(f"❌ 資料庫寫入失敗: {e}")
                message_to_user = "❌ 系統錯誤，儲存失敗。"
        else:
            print("⚠️ [DEBUG] 網址與HTML都找不到座標，存入待處理")
            backup_save(user_id, temp_title, raw_message, target_url)
            message_to_user = "⚠️ 連結已接收，但無法解析座標 (已存入待處理清單)。"
    else:
        print("⚠️ [DEBUG] 無法識別為地圖連結，存為純文字")
        backup_save(user_id, temp_title, raw_message, "")
        message_to_user = "📝 已存為純文字筆記。"

    if message_to_user:
        reply_line(reply_token, [{"type": "text", "text": message_to_user}])

def backup_save(user_id, title, content, url):
    data = {
        "user_id": user_id,
        "location_name": "[待處理] " + title,
        "google_map_url": url,
        "address": content,
        "latitude": 0,
        "longitude": 0,
        "category": "其它",
        "created_at": "now()"
    }
    try:
        supabase.table("map_spots").insert(data).execute()
        print("✅ 已寫入備份/待處理清單")
    except Exception as e:
        print(f"❌ 備份寫入失敗: {e}")

# --- 4. 核心功能 B: 雷達模式 (不變) ---

def handle_radar_task(user_lat, user_lng, user_id, reply_token):
    print(f"📡 [雷達模式] 搜尋附近: {user_lat}, {user_lng}")

    try:
        response = supabase.table("map_spots").select("*").neq("latitude", 0).execute()
        spots = response.data

        for spot in spots:
            dist = calculate_distance(user_lat, user_lng, spot['latitude'], spot['longitude'])
            spot['distance_km'] = dist

        nearby_spots = sorted(spots, key=lambda x: x['distance_km'])[:5]

        if not nearby_spots:
            reply_line(reply_token, [{"type": "text", "text": "📭 附近 5km 內沒有你的收藏。"}])
            return

        bubbles = []
        for spot in nearby_spots:
            dist_text = f"{spot['distance_km']:.1f} km"
            nav_url = f"https://www.google.com/maps/search/?api=1&query={spot['latitude']},{spot['longitude']}"
            cat_val = spot.get('category') or "其它"
            title_val = spot.get('location_name') or "未命名"
            cat_color = "#E63946" if cat_val == "美食" else ("#457B9D" if cat_val == "景點" else "#1D8446")

            bubble = {
                "type": "bubble", "size": "micro",
                "body": {
                    "type": "box", "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": cat_val, "weight": "bold", "color": cat_color, "size": "xxs"},
                        {"type": "text", "text": title_val, "weight": "bold", "size": "sm", "wrap": True, "margin": "xs"},
                        {"type": "text", "text": dist_text, "size": "xs", "color": "#aaaaaa", "margin": "xs"}
                    ]
                },
                "footer": {
                    "type": "box", "layout": "vertical",
                    "contents": [{"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": "導航", "uri": nav_url}}]
                }
            }
            bubbles.append(bubble)
        flex_message = {"type": "flex", "altText": "附近地點", "contents": {"type": "carousel", "contents": bubbles}}
        reply_line(reply_token, [flex_message])
    except Exception as e:
        print(f"❌ 雷達搜尋失敗: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 系統忙碌中 (Radar Error)"}])

if __name__ == "__main__":
    if len(sys.argv) > 3:
        arg1 = sys.argv[1]
        arg2 = sys.argv[2]
        arg3 = sys.argv[3]
        if re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', arg1):
            try:
                lat_str, lng_str = arg1.split(',')
                handle_radar_task(float(lat_str), float(lng_str), arg2, arg3)
            except:
                handle_save_task(arg1, arg2, arg3)
        else:
            handle_save_task(arg1, arg2, arg3)
    else:
        print("❌ 參數不足")
