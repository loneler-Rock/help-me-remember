import os
import sys
import time
import re
import math
import requests
import json
from supabase import create_client, Client
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import unquote
from selenium.webdriver.common.by import By

# --- 強制設定標準輸出編碼為 UTF-8 ---
sys.stdout.reconfigure(encoding='utf-8')

# --- 初始化與設定 ---
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
    if not token or token == "dummy_token":
        print("⚠️ [DEBUG] 本地測試/無 Token，略過回覆")
        return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    try:
        requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})
    except Exception as e:
        print(f"❌ LINE 回覆失敗: {e}")

# --- 數學工具：計算距離 (Haversine Formula) ---
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # 地球半徑 (km)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) * math.sin(dlat / 2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon / 2) * math.sin(dlon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance # 單位：公里

# --- Flex Message 工具 (通用版) ---
def create_flex_carousel(spots, title="您的收藏"):
    bubbles = []
    # 限制最多顯示 10 筆
    for spot in spots[:10]:
        name = spot.get('location_name', '未命名')
        category = spot.get('category', '其它')
        address = spot.get('address', '無地址')
        url = spot.get('google_map_url', 'http://googleusercontent.com/maps.google.com/3')
        
        # 處理距離顯示 (如果有 dist_km 欄位)
        dist_info = ""
        if 'dist_km' in spot:
            dist = spot['dist_km']
            dist_text = f"{int(dist*1000)}m" if dist < 1 else f"{dist:.1f}km"
            dist_info = dist_text

        # 設定顏色
        header_color = "#E67E22" # Default Orange
        if category == "景點": header_color = "#27AE60"
        if category == "住宿": header_color = "#8E44AD"
        if category == "其它": header_color = "#95A5A6"

        # 標題欄內容
        header_contents = [
            {"type": "text", "text": category, "color": "#ffffff", "weight": "bold", "size": "xs", "flex": 1}
        ]
        if dist_info:
            header_contents.append({"type": "text", "text": dist_info, "color": "#ffffff", "weight": "bold", "size": "xs", "align": "end", "flex": 1})

        bubble = {
            "type": "bubble",
            "size": "micro",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "box", "layout": "horizontal",
                        "contents": header_contents
                    },
                    {"type": "text", "text": name, "color": "#ffffff", "weight": "bold", "size": "sm", "wrap": True, "margin": "md"}
                ],
                "backgroundColor": header_color,
                "paddingAll": "8px"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "box", "layout": "baseline", "spacing": "sm",
                        "contents": [
                            {"type": "text", "text": "📍", "size": "xs", "flex": 1},
                            {"type": "text", "text": address[:18] + "..." if len(address)>18 else address, "wrap": True, "color": "#666666", "size": "xs", "flex": 5}
                        ]
                    }
                ],
                "paddingAll": "8px"
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "button", "action": {"type": "uri", "label": "導航", "uri": url}, "style": "link", "height": "sm"}
                ]
            }
        }
        bubbles.append(bubble)
    
    return {
        "type": "flex",
        "altText": title,
        "contents": {
            "type": "carousel",
            "contents": bubbles
        }
    }

# --- 功能邏輯 ---

def handle_location_search(user_lat, user_lng, user_id, reply_token):
    print(f"📡 [雷達模式] 搜尋 ({user_lat}, {user_lng}) 附近的點...")
    try:
        # 1. 抓取用戶所有資料
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).execute()
        spots = response.data
        
        if not spots:
            reply_line(reply_token, [{"type": "text", "text": "📭 你還沒有收藏任何地點喔！"}])
            return

        # 2. 計算距離
        valid_spots = []
        for spot in spots:
            if spot['latitude'] and spot['longitude']:
                d = calculate_distance(user_lat, user_lng, spot['latitude'], spot['longitude'])
                spot['dist_km'] = d
                valid_spots.append(spot)
        
        # 3. 排序：由近到遠
        valid_spots.sort(key=lambda x: x['dist_km'])
        
        # 4. 取前 5 筆
        nearest_spots = valid_spots[:5]
        
        if not nearest_spots:
             reply_line(reply_token, [{"type": "text", "text": "⚠️ 附近沒有找到收藏點。"}])
             return

        msg_text = "🔎 這是離你最近的地點："
        if nearest_spots[0]['dist_km'] > 50:
            msg_text = "🔎 附近沒有收藏，這是最近的幾個："

        flex_message = create_flex_carousel(nearest_spots, "附近地點")
        reply_line(reply_token, [{"type": "text", "text": msg_text}, flex_message])
        print("✅ 雷達搜尋完成")

    except Exception as e:
        print(f"❌ 雷達錯誤: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 搜尋失敗"}])

def handle_query_list(user_id, reply_token):
    print(f"🔍 [查詢模式] 撈取用戶 {user_id} 最近收藏...")
    try:
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute()
        data = response.data
        if not data:
            reply_line(reply_token, [{"type": "text", "text": "📭 目前沒有收藏紀錄。"}])
            return
        flex_message = create_flex_carousel(data, "最近收藏")
        reply_line(reply_token, [flex_message])
    except Exception as e:
        print(f"❌ 查詢失敗: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 讀取失敗"}])

# --- OSM 與分類工具 ---
def parse_osm_category(data):
    if not data: return None
    if isinstance(data, list): item = data[0] if data else None
    else: item = data
    if not item: return None
    osm_category = item.get('category', '') or item.get('class', '')
    osm_type = item.get('type', '')
    if not osm_category and 'addresstype' in item: osm_category = item['addresstype']
    food_types = ['restaurant', 'cafe', 'fast_food', 'food_court', 'bar', 'pub', 'ice_cream', 'biergarten', 'deli']
    if osm_category == 'amenity' and osm_type in food_types: return "美食"
    if osm_category == 'shop' and osm_type in ['food', 'bakery', 'pastry', 'beverage', 'coffee', 'tea', 'deli']: return "美食"
    sight_types = ['attraction', 'museum', 'viewpoint', 'artwork', 'gallery', 'zoo', 'theme_park', 'park', 'castle']
    if osm_category in ['tourism', 'historic', 'leisure', 'natural']: return "景點"
    if osm_category == 'tourism' and osm_type in ['hotel', 'hostel', 'guest_house', 'motel', 'apartment']: return "住宿"
    return None

def get_osm_by_coordinate(lat, lng):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/3.0'}
        r = requests.get(url, headers=headers, timeout=5)
        return parse_osm_category(r.json())
    except: return None

def get_osm_by_name(name, lat, lng):
    try:
        viewbox = f"{lng-0.002},{lat-0.002},{lng+0.002},{lat+0.002}"
        url = f"https://nominatim.openstreetmap.org/search?q={name}&format=json&viewbox={viewbox}&bounded=1&limit=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/3.0'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data: return parse_osm_category(data)
        return None
    except: return None

def determine_category_smart(title, full_text, lat, lng):
    # V2.8.1 修正版關鍵字
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉", "小吃", "早餐", "午餐", "晚餐", "食堂", "Tea", "飲", "冰", "滷味", "豆花", "炸雞", "烘焙", "居酒屋", "拉麵", "丼", "素食", "熟食", "攤", "舖"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "景點", "文創", "學校", "中心", "診所", "醫院", "教會", "宮", "殿", "古蹟", "老街", "夜市", "風景", "體育"]
    lodging_keywords = ["Hotel", "民宿", "飯店", "旅館", "酒店", "客棧", "旅店", "行館", "Resort", "住宿", "會館"]
    scan_text = (title + " " + full_text[:1000]).replace("\n", " ")
    for kw in lodging_keywords:
        if kw in scan_text: return "住宿"
    for kw in travel_keywords:
        if kw in scan_text: return "景點"
    for kw in food_keywords:
        if kw in scan_text: return "美食"
    if title and title != "未命名地點":
        cat = get_osm_by_name(title, lat, lng)
        if cat: return cat
    cat = get_osm_by_coordinate(lat, lng)
    if cat: return cat
    return "其它"

def get_real_url_with_browser(url):
    print(f"🕵️ [DEBUG] 啟動 Chrome (V3.1)... 目標: {url}")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option('prefs', {'intl.accept_languages': 'zh-TW,zh;q=0.9,en;q=0.8'})
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
    driver = None
    final_url = url
    page_title = ""
    page_text = ""
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        params = {"latitude": 25.033964, "longitude": 121.564468, "accuracy": 100}
        driver.execute_cdp_cmd("Emulation.setGeolocationOverride", params)
        target_url = url + "&hl=zh-TW&gl=TW" if "?" in url else url + "?hl=zh-TW&gl=TW"
        driver.get(target_url)
        time.sleep(6)
        final_url = driver.current_url
        page_title = driver.title
        try: page_text = driver.find_element(By.TAG_NAME, "body").text
        except: page_text = ""
    except Exception as e: print(f"⚠️ 瀏覽器錯誤: {e}")
    finally:
        if driver: driver.quit()
    return final_url, page_title, page_text

def extract_map_url(text):
    if not text: return None
    match = re.search(r'(https?://[^\s]*(?:google|goo\.gl|maps\.app\.goo\.gl)[^\s]*)', text)
    return match.group(1) if match else None

def parse_coordinates(url):
    if not url: return None, None
    url = unquote(url)
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    match = re.search(r'search/(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    match_lat = re.search(r'!3d(-?\d+\.\d+)', url)
    match_lng = re.search(r'!4d(-?\d+\.\d+)', url)
    if match_lat and match_lng: return float(match_lat.group(1)), float(match_lng.group(2))
    return None, None

def check_duplicate(user_id, location_name):
    try:
        response = supabase.table("map_spots").select("id").eq("user_id", user_id).eq("location_name", location_name).execute()
        if response.data: return response.data[0]['id']
        return None
    except: return None

def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 開始處理...")
    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message:
         target_url = raw_message.strip()

    if not target_url:
        reply_line(reply_token, [{"type": "text", "text": "📝 無法識別地圖連結，已略過。"}])
        return

    final_url, page_title, page_text = get_real_url_with_browser(target_url)
    lat, lng = parse_coordinates(final_url)
    final_title = page_title.replace(" - Google 地圖", "").replace(" - Google Maps", "").strip()
    if final_title == "Google Maps" or not final_title: final_title = "未命名地點"

    category = determine_category_smart(final_title, page_text, lat, lng)
    
    if lat and lng:
        existing_id = check_duplicate(user_id, final_title)
        data = {
            "user_id": user_id, "location_name": final_title, "google_map_url": final_url,
            "address": final_url, "latitude": lat, "longitude": lng, "category": category,
            "geom": f"POINT({lng} {lat})", "created_at": "now()"
        }
        try:
            if existing_id:
                supabase.table("map_spots").update(data).eq("id", existing_id).execute()
                reply_line(reply_token, [{"type": "text", "text": f"✅ 更新成功！\n店名: {final_title}"}])
            else:
                supabase.table("map_spots").insert(data).execute()
                reply_line(reply_token, [{"type": "text", "text": f"✅ 已收藏！\n店名: {final_title}\n分類: {category}"}])
        except Exception as e:
            reply_line(reply_token, [{"type": "text", "text": "❌ 資料庫寫入失敗"}])
    else:
        reply_line(reply_token, [{"type": "text", "text": "⚠️ 無法解析座標"}])

# --- 主入口 (V3.1 Router) ---
def main():
    if len(sys.argv) < 4:
        print("❌ 參數不足")
        return

    raw_message = sys.argv[1]
    user_id = sys.argv[2]
    reply_token = sys.argv[3]
    
    print(f"🚀 啟動! 訊息: {raw_message}")

    # 1. 判斷是否為「清單」指令
    if raw_message.strip().lower() in ["list", "清單", "列表", "help", "查詢"]:
        handle_query_list(user_id, reply_token)
        return

    # 2. 判斷是否為「位置訊息」 (格式: LOCATION:25.03,121.56)
    if raw_message.startswith("LOCATION:"):
        try:
            parts = raw_message.replace("LOCATION:", "").split(",")
            user_lat = float(parts[0])
            user_lng = float(parts[1])
            handle_location_search(user_lat, user_lng, user_id, reply_token)
            return
        except Exception as e:
            print(f"❌ 解析座標失敗: {e}")

    # 3. 否則：預設進入存檔模式 (處理 Google Maps 連結)
    handle_save_task(raw_message, user_id, reply_token)

if __name__ == "__main__":
    main()
