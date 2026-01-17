import os
import re
import sys
import json
import math
import requests
from supabase import create_client, Client

# --- 1. 初始化設定 ---
# 這裡會自動讀取 GitHub Actions 設定好的環境變數
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") # 使用 Service Role Key 確保有寫入權限

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("缺少 SUPABASE_URL 或 SUPABASE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

# --- 2. 工具函式 (計算與解析) ---

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    計算兩點間的距離 (單位: 公里)
    使用 Haversine 公式
    """
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
    """
    還原短網址 (例如 goo.gl -> google.com/maps/...)
    """
    try:
        # 模擬瀏覽器 Header，避免被 Google 擋
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.head(url, allow_redirects=True, headers=headers, timeout=10)
        return response.url
    except:
        return url

def extract_map_url(text):
    """
    從文字中抓取 Google Maps 連結
    """
    if not text: return None
    # 抓取常見的 Google Maps 網址格式
    match = re.search(r'(https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|www\.google\.com/maps|google\.com/maps)/[a-zA-Z0-9\./\?=&]+)', text)
    return match.group(1) if match else None

def parse_google_maps_url(url):
    """
    從長網址解析經緯度
    """
    if not url: return None, None

    # 格式 1: @lat,lng
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))

    # 格式 2: q=lat,lng (查詢參數)
    match = re.search(r'q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    
    # 格式 3: !3d lat !4d lng (內崁代碼)
    match_lat = re.search(r'!3d(-?\d+\.\d+)', url)
    match_lng = re.search(r'!4d(-?\d+\.\d+)', url)
    if match_lat and match_lng: return float(match_lat.group(1)), float(match_lng.group(2))

    return None, None

def determine_category(title):
    """簡單分類器"""
    if not title: return "其它"
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "茶", "餅", "甜點", "燒肉", "火鍋", "料理", "Breakfast", "Lunch", "Dinner", "Bistro"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "宮", "廟", "博物館", "美術館", "步道", "農場", "樂園", "展覽", "夜景", "View", "Hotel", "民宿"]
    
    for kw in food_keywords:
        if kw in title: return "美食"
    for kw in travel_keywords:
        if kw in title: return "景點"
    return "其它"

# --- 3. 核心功能 A: 存檔模式 ---

def handle_save_task(raw_message, user_id):
    print(f"📥 [存檔模式] 收到訊息: {raw_message}")
    
    # 防空機制
    if not raw_message or not raw_message.strip():
        print("⚠️ 訊息為空，跳過")
        return

    target_url = extract_map_url(raw_message)
    
    # 簡單取標題 (前20字)
    temp_title = raw_message[:20].replace("\n", " ") if raw_message else "未命名地點"

    if target_url:
        print(f"🔍 發現連結，正在解析: {target_url}")
        final_url = resolve_url(target_url)
        lat, lng = parse_google_maps_url(final_url)
        category = determine_category(temp_title)

        if lat and lng:
            # ✅ 成功解析：寫入完整資料 (包含 geom)
            data = {
                "user_id": user_id,
                "title": temp_title,
                "url": final_url,
                "address": final_url, # 暫時用網址當地址
                "latitude": lat,
                "longitude": lng,
                "category": category,
                "geom": f"POINT({lng} {lat})", # PostGIS 格式
                "created_at": "now()"
            }
            try:
                # 寫入 ig_food_map
                supabase.table("ig_food_map").insert(data).execute()
                print(f"✅ 成功儲存地點: {temp_title} ({category})")
            except Exception as e:
                print(f"❌ 寫入資料庫失敗: {e}")
        else:
            # ⚠️ 有連結但解析不出座標
            print("⚠️ 無法從連結解析座標，僅儲存文字")
            backup_save(user_id, temp_title, raw_message, target_url)
    else:
        # ⚠️ 純文字備份
        print("⚠️ 未發現連結，僅儲存文字")
        backup_save(user_id, temp_title, raw_message, "")

def backup_save(user_id, title, content, url):
    """備用儲存：當無法解析座標時"""
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
        print(f"✅ 已存入待處理清單")
    except Exception as e:
        print(f"❌ 待處理寫入失敗: {e}")

# --- 4. 核心功能 B: 雷達模式 (搜尋附近) ---

def handle_radar_task(user_lat, user_lng, user_id):
    print(f"📡 [雷達模式] 使用者位置: {user_lat}, {user_lng}")

    try:
        # 1. 抓出所有地點 (暫時做法：抓全部再過濾，之後會改用 SQL PostGIS 搜尋)
        response = supabase.table("ig_food_map").select("*").neq("latitude", 0).execute()
        spots = response.data

        # 2. 計算距離
        for spot in spots:
            dist = calculate_distance(user_lat, user_lng, spot['latitude'], spot['longitude'])
            spot['distance_km'] = dist

        # 3. 排序並取最近 5 個
        nearby_spots = sorted(spots, key=lambda x: x['distance_km'])[:5]

        if not nearby_spots:
            print("📭 附近沒有已儲存的地點")
            return

        # 4. 製作 LINE Flex Message 卡片
        bubbles = []
        for spot in nearby_spots:
            dist_text = f"{spot['distance_km']:.1f} km"
            # 導航連結
            nav_url = f"https://www.google.com/maps/search/?api=1&query={spot['latitude']},{spot['longitude']}"
            
            # 分類標籤顏色
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

        # ★ 印出 JSON 供 Make 抓取
        print("JSON_OUTPUT_START")
        print(json.dumps(flex_message))
        print("JSON_OUTPUT_END")

    except Exception as e:
        print(f"❌ 雷達搜尋失敗: {e}")

# --- 主程式進入點 ---

if __name__ == "__main__":
    # 接收參數: python main.py "訊息內容" "User_ID"
    if len(sys.argv) > 2:
        arg1 = sys.argv[1] # raw_message (文字或座標)
        arg2 = sys.argv[2] # user_id

        # 判斷 arg1 是否為座標格式 (例如: "24.123,121.456")
        # 這是給 Make 傳送 "Map Location" 時用的
        if re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', arg1):
            try:
                lat_str, lng_str = arg1.split(',')
                handle_radar_task(float(lat_str), float(lng_str), arg2)
            except:
                print("❌ 座標格式錯誤，切換回存檔模式")
                handle_save_task(arg1, arg2)
        else:
            # 不是座標，執行存檔
            handle_save_task(arg1, arg2)
    else:
        print("❌ 參數不足: 請提供 raw_message 和 user_id")
