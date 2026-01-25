import os
import sys
import time
import re
import requests
import json
import math
import csv
import io
from supabase import create_client, Client
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import unquote
from selenium.webdriver.common.by import By

# --- 1. 初始化設定 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

# --- UI配色設定 ---
CATEGORY_COLORS = {
    "美食": "#E67E22",
    "景點": "#27AE60",
    "住宿": "#2980B9",
    "其它": "#7F8C8D"
}

CATEGORY_ICONS = {
    "美食": "https://cdn-icons-png.flaticon.com/512/706/706164.png",
    "景點": "https://cdn-icons-png.flaticon.com/512/2664/2664531.png",
    "住宿": "https://cdn-icons-png.flaticon.com/512/2983/2983803.png",
    "其它": "https://cdn-icons-png.flaticon.com/512/447/447031.png"
}

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

# --- 2. 輔助工具：OSM 與 分類 ---

def parse_osm_category(data):
    if not data: return None
    if isinstance(data, list):
        if not data: return None
        item = data[0]
    else:
        item = data

    osm_category = item.get('category', '') or item.get('class', '')
    osm_type = item.get('type', '')
    if not osm_category and 'addresstype' in item:
        osm_category = item['addresstype']

    print(f"   ↳ OSM 屬性分析: Class={osm_category}, Type={osm_type}")

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
        headers = {'User-Agent': 'HelpMeRememberBot/3.1'}
        r = requests.get(url, headers=headers, timeout=5)
        return parse_osm_category(r.json())
    except:
        return None

def get_osm_by_name(name, lat, lng):
    try:
        viewbox = f"{lng-0.002},{lat-0.002},{lng+0.002},{lat+0.002}"
        print(f"🕵️ [DEBUG] 啟動 OSM 姓名偵探: 搜尋 '{name}'...")
        url = f"https://nominatim.openstreetmap.org/search?q={name}&format=json&viewbox={viewbox}&bounded=1&limit=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/3.1'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data:
            print("   ✅ OSM 姓名搜尋命中！")
            return parse_osm_category(data)
        return None
    except:
        return None

def determine_category_smart(title, full_text, lat, lng):
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

# --- 3. 瀏覽器爬蟲 ---

def get_real_url_with_browser(url):
    print(f"🕵️ [DEBUG] 啟動 Chrome (V2.8)... 目標: {url}")
    
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

        if "?" in url: target_url = url + "&hl=zh-TW&gl=TW"
        else: target_url = url + "?hl=zh-TW&gl=TW"
            
        driver.get(target_url)
        print("   ⏳ 等待頁面載入 (6秒)...")
        time.sleep(6)
        
        final_url = driver.current_url
        page_title = driver.title
        try:
            body_element = driver.find_element(By.TAG_NAME, "body")
            page_text = body_element.text
        except:
            page_text = ""
                
        print(f"   ✅ 標題: {page_title}")
        
    except Exception as e:
        print(f"⚠️ [DEBUG] 瀏覽器執行錯誤: {e}")
    finally:
        if driver: driver.quit()
            
    return final_url, page_title, page_text

# --- 4. 雷達模式 (美食獵人版) ---

def get_nearby_spots(user_id, lat, lng, limit=10, target_category="美食"):
    try:
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).execute()
        spots = response.data
        
        results = []
        for spot in spots:
            current_cat = spot.get('category', '其它')
            if target_category and current_cat != target_category:
                continue

            s_lat = spot.get('latitude')
            s_lng = spot.get('longitude')
            if s_lat and s_lng:
                dist = math.sqrt((s_lat - lat)**2 + (s_lng - lng)**2)
                spot['dist_score'] = dist
                results.append(spot)
        
        results.sort(key=lambda x: x['dist_score'])
        return results[:limit]
    except Exception as e:
        print(f"❌ 雷達查詢失敗: {e}")
        return []

def create_radar_flex(spots):
    if not spots:
        return {"type": "text", "text": "🍜 哎呀！您的附近暫時沒有收藏的「美食」。\n\n快去 Google Maps 找些好吃的店分享給我吧！"}

    bubbles = []
    for spot in spots:
        cat = spot.get('category', '其它')
        color = CATEGORY_COLORS.get(cat, "#7F8C8D")
        icon = CATEGORY_ICONS.get(cat, CATEGORY_ICONS["其它"])
        
        map_url = spot.get('google_map_url') or spot.get('address') or "https://maps.google.com"

        bubble = {
          "type": "bubble",
          "size": "micro",
          "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
              {"type": "text", "text": cat, "color": "#ffffff", "size": "xs", "weight": "bold"}
            ],
            "backgroundColor": color,
            "paddingAll": "sm"
          },
          "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
              {"type": "text", "text": spot['location_name'], "weight": "bold", "size": "sm", "wrap": True},
              {
                "type": "box",
                "layout": "baseline",
                "contents": [
                  {"type": "icon", "url": icon, "size": "xs"},
                  {"type": "text", "text": "距離最近", "size": "xs", "color": "#8c8c8c", "margin": "sm"}
                ],
                "margin": "md"
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
                  "label": "導航去吃",
                  "uri": map_url
                },
                "style": "primary",
                "color": color,
                "height": "sm"
              }
            ]
          }
        }
        bubbles.append(bubble)
        if len(bubbles) >= 10: break

    return {
        "type": "flex",
        "altText": "🍽️ 附近的口袋美食清單",
        "contents": {
            "type": "carousel",
            "contents": bubbles
        }
    }

# --- 5. 匯出模式 (V3.1 新增) ---
def handle_export_task(user_id, reply_token):
    print(f"📤 [匯出模式] 準備匯出 {user_id} 的資料...")
    try:
        # 1. 抓取所有資料
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).execute()
        spots = response.data
        
        if not spots:
            reply_line(reply_token, [{"type": "text", "text": "📭 您的地圖還是空的，無法匯出。"}])
            return

        # 2. 製作 CSV
        output = io.StringIO()
        # 定義 CSV 欄位，這格式可以直接匯入 Google My Maps
        fieldnames = ['Name', 'Category', 'Address', 'Latitude', 'Longitude', 'GoogleMapURL']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        for spot in spots:
            writer.writerow({
                'Name': spot.get('location_name', '未命名'),
                'Category': spot.get('category', '其它'),
                'Address': spot.get('address', ''),
                'Latitude': spot.get('latitude', ''),
                'Longitude': spot.get('longitude', ''),
                'GoogleMapURL': spot.get('google_map_url', '')
            })
        
        csv_content = output.getvalue()
        
        # 3. 上傳到 file.io (一次性暫存)
        # 這裡設定 14 天有效 (14d)，但下載一次後就會銷毀，保證隱私
        files = {'file': ('my_map.csv', csv_content)}
        r = requests.post('https://file.io/?expires=14d', files=files)
        
        if r.status_code == 200:
            link = r.json().get('link')
            reply_line(reply_token, [
                {"type": "text", "text": f"✅ 匯出成功！\n\n這是一個一次性下載連結，請用電腦打開並下載 CSV 檔案：\n{link}\n\n💡 提示：您可以將此檔案匯入 Google Maps 的「我的地圖」功能。"}
            ])
        else:
            reply_line(reply_token, [{"type": "text", "text": "❌ 上傳失敗，請稍後再試。"}])

    except Exception as e:
        print(f"❌ 匯出錯誤: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 系統發生錯誤。"}])


# --- 6. 任務處理主邏輯 ---

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
        if response.data and len(response.data) > 0:
            return response.data[0]['id']
        return None
    except:
        return None

# 存檔模式
def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 開始處理...")
    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message:
         target_url = raw_message.strip()

    if not target_url:
        print("⚠️ [DEBUG] 非地圖連結")
        reply_line(reply_token, [{"type": "text", "text": "📝 已存為純文字筆記(尚未支援)。"}])
        return

    final_url, page_title, page_text = get_real_url_with_browser(target_url)
    lat, lng = parse_coordinates(final_url)
    final_title = page_title.replace(" - Google 地圖", "").replace(" - Google Maps", "").strip()
    if final_title == "Google Maps": final_title = "未命名地點"

    category = determine_category_smart(final_title, page_text, lat, lng)
    print(f"🕵️ [DEBUG] 準備存檔 -> 店名: {final_title} | 類別: {category}")

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
            reply_line(reply_token, [{"type": "text", "text": "❌ 系統錯誤"}])
    else:
        reply_line(reply_token, [{"type": "text", "text": "⚠️ 連結已接收，但無法解析座標。"}])

# 雷達模式
def handle_radar_task(lat_str, lng_str, user_id, reply_token):
    print(f"📡 [雷達模式] 啟動... 中心點: {lat_str}, {lng_str}")
    try:
        lat = float(lat_str)
        lng = float(lng_str)
        nearby_spots = get_nearby_spots(user_id, lat, lng, limit=10, target_category="美食")
        flex_message = create_radar_flex(nearby_spots)
        reply_line(reply_token, [flex_message])
    except ValueError:
        reply_line(reply_token, [{"type": "text", "text": "❌ 座標資料錯誤"}])

# --- 主程式入口 ---
if __name__ == "__main__":
    if len(sys.argv) > 3:
        input_content = sys.argv[1].strip()
        user_id = sys.argv[2]
        reply_token = sys.argv[3]
        
        # 1. 雷達模式：檢查是否為座標
        if re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', input_content):
            lat_str, lng_str = input_content.split(',')
            handle_radar_task(lat_str, lng_str, user_id, reply_token)
            
        # 2. 匯出模式：檢查關鍵字
        elif input_content.lower() in ["export", "匯出", "地圖匯出"]:
            handle_export_task(user_id, reply_token)
            
        # 3. 存檔模式：預設為網址處理
        else:
            handle_save_task(input_content, user_id, reply_token)
    else:
        print("❌ 參數不足")
