import os
import re
import sys
import json
import math
import requests
from supabase import create_client, Client

# --- 1. 初始化設定 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
# [新增] 讀取 LINE Token，讓 Python 可以回話
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("缺少 SUPABASE_URL 或 SUPABASE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

# --- [新增] LINE 回覆工具 ---
def reply_line(token, messages):
    """
    發送訊息回 LINE
    token: Reply Token
    messages: 訊息物件列表 (List of dict)
    """
    if not token:
        print("⚠️ 沒有 Reply Token，無法回覆 LINE")
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

def resolve_url(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.head(url, allow_redirects=True, headers=headers, timeout=10)
        return response.url
    except:
        return url

def extract_map_url(text):
    if not text: return None
    match = re.search(r'(https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|www\.google\.com/maps|google\.com/maps)/[a-zA-Z0-9\./\?=&]+)', text)
    return match.group(1) if match else None

def parse_google_maps_url(url):
    if not url: return None, None
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    match = re.search(r'q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    match_lat = re.search(r'!3d(-?\d+\.\d+)', url)
    match_lng = re.search(r'!4d(-?\d+\.\d+)', url)
    if match_lat and match_lng: return float(match_lat.group(1)), float(match_lng.group(2))
    return None, None

def determine_category(title):
    if not title: return "其它"
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "Hotel", "民宿"]
    for kw in food_keywords:
        if kw in title: return "美食"
    for kw in travel_keywords:
        if kw in title: return "景點"
    return "其它"

# --- 3. 核心功能 A: 存檔模式 ---

def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 處理中...")
    
    if not raw_message or not raw_message.strip():
        return

    target_url = extract_map_url(raw_message)
    temp_title = raw_message[:30].replace("\n", " ") if raw_message else "未命名地點"

    message_to_user = ""

    if target_url:
        final_url = resolve_url(target_url)
        lat, lng = parse_google_maps_url(final_url)
        category = determine_category(temp_title)

        if lat and lng:
            data = {
                "user_id": user_id,
                "title": temp_title,
                "url": final_url,
                "address": final_url,
                "latitude": lat,
                "longitude": lng,
                "category": category,
                "geom": f"POINT({lng} {lat})",
                "created_at": "now()"
            }
            try:
                supabase.table("ig_food_map").insert(data).execute()
                print(f"✅ 成功儲存: {temp_title}")
                # [修改] 改為發送 LINE
                message_to_user = f"✅ 已收藏地點！\n類別: {category}\n標題: {temp_title}"
            except Exception as e:
                print(f"❌ DB Error: {e}")
                message_to_user = "❌ 系統錯誤，儲存失敗。"
        else:
            backup_save(user_id, temp_title, raw_message, target_url)
            message_to_user = "⚠️ 連結已存入，但抓不到座標 (系統將稍後處理)。"
    else:
        # 純文字不回應，避免太吵，或者你可以開啟下面這行
        # message_to_user = "這不是地圖連結喔。"
        pass

    # 執行回覆
    if message_to_user:
        reply_line(reply_token, [{"type": "text", "text": message_to_user}])

def backup_save(user_id, title, content, url):
    data = {
        "user_id": user_id,
        "title": "[待處理] " + title,
        "url": url,
        "address": content,
        "latitude": 0,
        "longitude": 0,
        "category": "其它",
        "created_at": "now()"
    }
    try:
        supabase.table("ig_food_map").insert(data).execute()
    except Exception as e:
        print(f"❌ 待處理寫入失敗: {e}")

# --- 4. 核心功能 B: 雷達模式 ---

def handle_radar_task(user_lat, user_lng, user_id, reply_token):
    print(f"📡 [雷達模式] 搜尋附近: {user_lat}, {user_lng}")

    try:
        response = supabase.table("ig_food_map").select("*").neq("latitude", 0).execute()
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
            cat_color = "#E63946" if spot['category'] == "美食" else ("#457B9D" if spot['category'] == "景點" else "#1D8446")

            bubble = {
                "type": "bubble",
                "size": "micro",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": spot['category'], "weight": "bold", "color": cat_color, "size": "xxs"},
                        {"type": "text", "text": spot['title'], "weight": "bold", "size": "sm", "wrap": True, "margin": "xs"},
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
        
        # [修改] 直接回傳 Flex Message
        reply_line(reply_token, [flex_message])

    except Exception as e:
        print(f"❌ 雷達搜尋失敗: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 系統忙碌中 (Radar Error)"}])

# --- 主程式進入點 ---

if __name__ == "__main__":
    # 接收參數: script.py "訊息" "User_ID" "Reply_Token"
    if len(sys.argv) > 3:
        arg1 = sys.argv[1] # raw_message
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
