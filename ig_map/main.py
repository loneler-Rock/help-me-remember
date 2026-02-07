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
from selenium.webdriver.common.by import By

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
    if not token:
        print("⚠️ [DEBUG] 沒有 Reply Token，略過回覆")
        return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    try:
        requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})
    except Exception as e:
        print(f"❌ LINE 回覆失敗: {e}")

# --- OSM 與分類工具 (保持 V2.8 原樣) ---
def parse_osm_category(data):
    # ... (保持原本 V2.8 邏輯，省略以節省篇幅) ...
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
        headers = {'User-Agent': 'HelpMeRememberBot/2.9'}
        r = requests.get(url, headers=headers, timeout=5)
        return parse_osm_category(r.json())
    except: return None

def get_osm_by_name(name, lat, lng):
    try:
        viewbox = f"{lng-0.002},{lat-0.002},{lng+0.002},{lat+0.002}"
        url = f"https://nominatim.openstreetmap.org/search?q={name}&format=json&viewbox={viewbox}&bounded=1&limit=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/2.9'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data: return parse_osm_category(data)
        return None
    except: return None

def determine_category_smart(title, full_text, lat, lng):
    # ... (保持原本 V2.8 邏輯，省略以節省篇幅) ...
    print(f"🕵️ [DEBUG] 啟動關鍵字掃描 (全文長度: {len(full_text)} 字)...")
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉", "小吃", "早餐", "午餐", "晚餐", "食堂", "Tea", "飲", "冰", "滷味", "豆花", "炸雞", "烘焙", "居酒屋", "拉麵", "丼", "素食", "熟食", "攤", "店", "舖", "館", "菜", "肉", "湯"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "景點", "文創", "步道", "學校", "中心", "診所", "醫院", "教會", "宮", "殿", "古蹟", "老街", "夜市", "風景"]
    lodging_keywords = ["Hotel", "民宿", "飯店", "旅館", "酒店", "客棧", "旅店", "行館", "Resort", "住宿", "會館"]
    scan_text = (title + " " + full_text[:1000]).replace("\n", " ")
    for kw in food_keywords:
        if kw in scan_text: return "美食"
    for kw in lodging_keywords:
        if kw in scan_text: return "住宿"
    for kw in travel_keywords:
        if kw in scan_text: return "景點"
    if title and title != "未命名地點":
        cat = get_osm_by_name(title, lat, lng)
        if cat: return cat
    cat = get_osm_by_coordinate(lat, lng)
    if cat: return cat
    return "其它"

# --- 瀏覽器與爬蟲 (保持 V2.8 原樣) ---
def get_real_url_with_browser(url):
    print(f"🕵️ [DEBUG] 啟動 Chrome (V2.9)... 目標: {url}")
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
        print("   ⏳ 等待頁面載入 (6秒)...")
        time.sleep(6)
        
        final_url = driver.current_url
        page_title = driver.title
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
        except: page_text = ""
        print(f"   ✅ 標題: {page_title}")
    except Exception as e:
        print(f"⚠️ [DEBUG] 瀏覽器執行錯誤: {e}")
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
    # ... (保持 V2.8 存檔邏輯) ...
    print(f"📥 [存檔模式] 開始處理...")
    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message:
         target_url = raw_message.strip()

    if not target_url:
        reply_line(reply_token, [{"type": "text", "text": "📝 已存為純文字筆記。"}])
        return

    final_url, page_title, page_text = get_real_url_with_browser(target_url)
    lat, lng = parse_coordinates(final_url)
    final_title = page_title.replace(" - Google 地圖", "").replace(" - Google Maps", "").strip()
    if final_title == "Google Maps": final_title = "未命名地點"

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
            else:
                supabase.table("map_spots").insert(data).execute()
            reply_line(reply_token, [{"type": "text", "text": f"✅ 已收藏！\n店名: {final_title}\n分類: {category}"}])
        except Exception as e:
            print(f"❌ DB Error: {e}")
            reply_line(reply_token, [{"type": "text", "text": "❌ 系統錯誤"}])
    else:
        reply_line(reply_token, [{"type": "text", "text": "⚠️ 連結已接收，但無法解析座標。"}])

# --- 新增功能: Flex Message 建構器 ---
def create_spot_bubble(spot):
    """建立單一地點的 Flex Bubble"""
    category_colors = {
        "美食": "#FF6B6B", # 紅色
        "景點": "#4ECDC4", # 青色
        "住宿": "#FFE66D", # 黃色
        "其它": "#95A5A6"  # 灰色
    }
    color = category_colors.get(spot.get('category'), "#95A5A6")
    
    # 計算距離顯示 (假設 Supabase 回傳 dist_meters)
    dist = spot.get('dist_meters', 0)
    dist_str = f"{int(dist)}m" if dist < 1000 else f"{round(dist/1000, 1)}km"

    bubble = {
        "type": "bubble",
        "size": "micro",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": color,
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": spot.get('category', '其它'),
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "xs"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": spot.get('location_name', '未命名'),
                    "weight": "bold",
                    "size": "sm",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": f"距離: {dist_str}",
                    "size": "xs",
                    "color": "#888888",
                    "margin": "sm"
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "導航",
                        "uri": spot.get('google_map_url', 'https://maps.google.com')
                    },
                    "style": "link",
                    "height": "sm"
                }
            ]
        }
    }
    return bubble

def handle_search_task(lat_str, lng_str, user_id, reply_token, radius_meters=500):
    print(f"📡 [雷達模式] 搜尋半徑: {radius_meters}m | 座標: {lat_str}, {lng_str}")
    
    try:
        lat = float(lat_str)
        lng = float(lng_str)
        
        # 使用 Supabase RPC 呼叫 (需先在 DB 建立 function，見下方說明)
        # 參數: lat, lng, radius_meters
        rpc_params = {
            "lat_input": lat, 
            "lng_input": lng, 
            "radius_meters": int(radius_meters),
            "user_id_input": user_id 
        }
        
        # 呼叫 Supabase stored procedure (推薦做法，效能最好)
        response = supabase.rpc("get_nearby_spots", rpc_params).execute()
        spots = response.data

        if not spots:
            # 查無資料，回傳 Quick Reply 建議擴大範圍
            msg = {
                "type": "text",
                "text": f"🧐 半徑 {radius_meters}m 內沒有收藏的地點。",
                "quickReply": {
                    "items": [
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "🔍 1KM內",
                                "text": f"雷達搜尋 {lat},{lng} 1000"
                            }
                        },
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "🔍 5KM內",
                                "text": f"雷達搜尋 {lat},{lng} 5000"
                            }
                        }
                    ]
                }
            }
            reply_line(reply_token, [msg])
            return

        # 建構 Flex Message Carousel
        bubbles = [create_spot_bubble(spot) for spot in spots[:10]] # 最多顯示10筆
        
        flex_message = {
            "type": "flex",
            "altText": f"附近有 {len(spots)} 個收藏地點",
            "contents": {
                "type": "carousel",
                "contents": bubbles
            },
            # 在 Flex Message 下方附帶 Quick Reply，讓使用者隨時可以切換範圍
            "quickReply": {
                "items": [
                    {
                        "type": "action",
                        "action": {
                            "type": "message",
                            "label": "🔍 1KM內",
                            "text": f"雷達搜尋 {lat},{lng} 1000"
                        }
                    },
                    {
                        "type": "action",
                        "action": {
                            "type": "message",
                            "label": "🔍 5KM內",
                            "text": f"雷達搜尋 {lat},{lng} 5000"
                        }
                    }
                ]
            }
        }
        
        reply_line(reply_token, [flex_message])
        print(f"✅ 已回傳 {len(spots)} 筆地點")

    except Exception as e:
        print(f"❌ Search Error: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 搜尋時發生錯誤"}])

# --- 主程式入口 ---
if __name__ == "__main__":
    # 參數格式約定:
    # 模式 A (存檔): python main.py "http..." "user123" "token"
    # 模式 B (搜尋): python main.py "SEARCH" "25.03" "121.56" "user123" "token" "500"
    
    args = sys.argv
    if len(args) > 1:
        if args[1] == "SEARCH":
            # 搜尋模式參數: SEARCH, lat, lng, user_id, token, radius(opt)
            lat = args[2]
            lng = args[3]
            u_id = args[4]
            token = args[5]
            radius = args[6] if len(args) > 6 else 500
            handle_search_task(lat, lng, u_id, token, radius)
        elif len(args) > 3:
            # 預設為存檔模式
            handle_save_task(args[1], args[2], args[3])
    else:
        print("❌ 參數不足")
