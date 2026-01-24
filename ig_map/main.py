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
    R = 6371 # 地球半徑 (km)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def resolve_url(url):
    """還原短網址，增加 User-Agent 避免被 Google 擋"""
    try:
        # 模擬瀏覽器，確保伺服器願意吐出真實網址
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        # allow_redirects=True 會自動跟隨跳轉，直到最後的長網址
        response = requests.head(url, allow_redirects=True, headers=headers, timeout=10)
        return response.url
    except Exception as e:
        print(f"⚠️ [DEBUG] 解析短網址失敗: {e}")
        return url

def extract_map_url(text):
    if not text: return None
    
    # ★★★ V1.1 修正點：超廣域捕獲 ★★★
    # 只要是 http 開頭，且網址中間包含 "google" 或 "goo.gl"，全部視為潛在目標
    # 這能抓到 googleusercontent, maps.app.goo.gl, www.google.com.tw 等所有變形
    match = re.search(r'(https?://[^\s]*(?:google|goo\.gl)[^\s]*)', text)
    
    return match.group(1) if match else None

def parse_google_maps_url(url):
    if not url: return None, None
    
    # 解碼網址 (處理中文亂碼)
    url = unquote(url)
    
    # 模式 A: @lat,lng (最常見)
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    
    # 模式 B: q=lat,lng (查詢參數)
    match = re.search(r'q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    
    # 模式 C: !3d...!4d (電腦版長網址內崁代碼)
    match_lat = re.search(r'!3d(-?\d+\.\d+)', url)
    match_lng = re.search(r'!4d(-?\d+\.\d+)', url)
    if match_lat and match_lng: return float(match_lat.group(1)), float(match_lng.group(2))
    
    return None, None

def determine_category(title):
    if not title: return "其它"
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "Hotel", "民宿", "景點", "文創"]
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

    # 1. 嘗試抓取網址
    target_url = extract_map_url(raw_message)
    
    # 2. 如果 Regex 沒抓到，但文字裡有 http 和 google，直接把整段當作網址試試
    if not target_url and "google" in raw_message and "http" in raw_message:
         target_url = raw_message.strip()

    print(f"🕵️ [DEBUG] 判定處理網址 -> [{target_url}]")

    # 嘗試提取標題
    temp_title = "未命名地點"
    if target_url and "/place/" in target_url:
        try:
            parts = unquote(target_url).split("/place/")[1].split("/")[0]
            temp_title = parts.replace("+", " ")
        except:
            temp_title = raw_message[:30]
    else:
        temp_title = raw_message[:30].replace("\n", " ")

    message_to_user = ""

    if target_url:
        # 3. 還原長網址 (這裡會解決 googleusercontent 跳轉問題)
        final_url = resolve_url(target_url)
        print(f"🕵️ [DEBUG] 還原後的長網址 -> [{final_url}]")
        
        # 4. 解析座標
        lat, lng = parse_google_maps_url(final_url)
        print(f"🕵️ [DEBUG] 解析座標結果 -> Lat: {lat}, Lng: {lng}")
        
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
            # 有網址但解不出座標 (可能是純圖片連結或未知的 Google 頁面)
            print("⚠️ [DEBUG] 有網址但抓不到座標，存入待處理")
            backup_save(user_id, temp_title, raw_message, target_url)
            message_to_user = "⚠️ 連結已接收，但無法解析座標 (已存入待處理清單)。"
    else:
        # 完全抓不到網址
        print("⚠️ [DEBUG] 無法識別為地圖連結，存為純文字")
        backup_save(user_id, temp_title, raw_message, "")
        message_to_user = "📝 已存為純文字筆記。"

    if message_to_user:
        reply_line(reply_token, [{"type": "text", "text": message_to_user}])

def backup_save(user_id, title, content, url):
    """純文字筆記或解析失敗的備份"""
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

# --- 4. 核心功能 B: 雷達模式 ---

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
                "type": "bubble",
                "size": "micro",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": cat_val, "weight": "bold", "color": cat_color, "size": "xxs"},
                        {"type": "text", "text": title_val, "weight": "bold", "size": "sm", "wrap": True, "margin": "xs"},
                        {"type": "text", "text": dist_text, "size": "xs", "color": "#aaaaaa", "margin": "xs"}
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": "導航", "uri": nav_url}}
                    ]
                }
            }
            bubbles.append(bubble)

        flex_message = {
            "type": "flex",
            "altText": "這是您附近的地點！",
            "contents": {
                "type": "carousel",
                "contents": bubbles
            }
        }
        
        reply_line(reply_token, [flex_message])

    except Exception as e:
        print(f"❌ 雷達搜尋失敗: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 系統忙碌中 (Radar Error)"}])

# --- 主程式進入點 ---

if __name__ == "__main__":
    if len(sys.argv) > 3:
        arg1 = sys.argv[1] # raw_message (文字或座標字串)
        arg2 = sys.argv[2] # user_id
        arg3 = sys.argv[3] # reply_token

        # 判斷是否為座標 (雷達模式)
        if re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', arg1):
            try:
                lat_str, lng_str = arg1.split(',')
                handle_radar_task(float(lat_str), float(lng_str), arg2, arg3)
            except:
                handle_save_task(arg1, arg2, arg3)
        else:
            # 存檔模式
            handle_save_task(arg1, arg2, arg3)
    else:
        print("❌ 參數不足: 需 message, user_id, reply_token")
