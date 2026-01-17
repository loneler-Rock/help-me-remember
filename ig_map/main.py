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
    temp_title = raw_message[:30].replace("\n", " ") if raw_message else "未命名地點"

    if target_url:
        final_url = resolve_url(target_url)
        lat, lng = parse_google_maps_url(final_url)
    
    category = determine_category(temp_title)
    
    if lat and lng:
        data = {"user_id": user_id, "title": temp_title, "url": final_url, "address": final_url, "latitude": lat, "longitude": lng, "category": category, "created_at": "now()"}
        try:
            supabase.table("map_spots").insert(data).execute()
            print(f"✅ 成功儲存: {temp_title} [{category}]")
        except Exception as e:
            print(f"❌ 寫入失敗: {e}")
    else:
        print("⚠️ 無法解析座標，寫入待處理")
        data = {"user_id": user_id, "title": "[待處理] " + temp_title, "url": final_url, "address": raw_message, "latitude": 0, "longitude": 0, "category": "其它", "created_at": "now()"}
        try:
            supabase.table("map_spots").insert(data).execute()
        except:
            pass

# --- 核心功能 B: 雷達 (搜尋最近地點) ---

def handle_radar_task(user_lat, user_lng, user_id):
    print(f"📡 [雷達模式] 使用者位置: {user_lat}, {user_lng}")
    
    try:
        # 1. 把所有地點抓出來 (如果不超過 1000 筆，這樣最快最穩，不用搞資料庫索引)
        response = supabase.table("map_spots").select("*").neq("latitude", 0).execute()
        spots = response.data
        
        # 2. Python 算距離並排序
        for spot in spots:
            dist = calculate_distance(user_lat, user_lng, spot['latitude'], spot['longitude'])
            spot['distance_km'] = dist
            
        # 3. 取出最近的 5 個
        nearby_spots = sorted(spots, key=lambda x: x['distance_km'])[:5]
        
        if not nearby_spots:
            print("📭 附近沒有已儲存的地點")
            # 這裡您可以選擇回傳文字訊息
            return

        # 4. 製作 LINE Flex Message (旋轉木馬卡片)
        bubbles = []
        for spot in nearby_spots:
            dist_text = f"{spot['distance_km']:.1f} km"
            # 產生 Google Map 導航連結
            nav_url = f"https://www.google.com/maps/dir/?api=1&destination={spot['latitude']},{spot['longitude']}"
            
            bubble = {
                "type": "bubble",
                "size": "micro",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": spot['category'], "weight": "bold", "color": "#1DB446", "size": "xxs"},
                        {"type": "text", "text": spot['title'], "weight": "bold", "size": "sm", "wrap": True},
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
            "altText": "這是在您附近的地點！",
            "contents": {
                "type": "carousel",
                "contents": bubbles
            }
        }
        
        # ★★★ 關鍵：直接印出 JSON，讓 Make 可以抓去用，或者這裡可以直接呼叫 LINE API 傳送 ★★★
        # 為了簡單，我們先印出來，看您 Make 怎麼接
        print("JSON_OUTPUT_START")
        print(json.dumps(flex_message))
        print("JSON_OUTPUT_END")
        
        # 如果您希望 Python 直接傳給 LINE，我們需要 LINE_CHANNEL_ACCESS_TOKEN
        # 目前先這樣，確認邏輯通了再加
        
    except Exception as e:
        print(f"❌ 雷達搜尋失敗: {e}")

# --- 主程式進入點 ---

if __name__ == "__main__":
    if len(sys.argv) > 2:
        arg1 = sys.argv[1] # raw_message
        arg2 = sys.argv[2] # user_id
        
        # ★ 智慧判斷：如果 arg1 看起來像座標 (例如 "25.033,121.565") -> 雷達模式
        # 否則 -> 存檔模式
        if re.match(r'^-?\d+(\.\d+)?,\s*-?\d+(\.\d+)?$', arg1):
            try:
                lat_str, lng_str = arg1.split(',')
                handle_radar_task(float(lat_str), float(lng_str), arg2)
            except:
                print("❌ 座標格式錯誤")
        else:
            handle_save_task(arg1, arg2)
    else:
        print("❌ 參數不足")
