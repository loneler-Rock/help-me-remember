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

def reply_line(token, messages):
    if not token:
        print("⚠️ [DEBUG] 沒有 Reply Token，略過回覆")
        return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    try:
        requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})
    except Exception as e:
        print(f"❌ LINE 回覆失敗: {e}")

# --- 2. 輔助工具：重複檢查與 OSM ---

def check_duplicate(user_id, location_name):
    """檢查資料庫是否已有相同店名"""
    try:
        # 搜尋該使用者是否存過完全一樣的店名
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).eq("location_name", location_name).execute()
        if response.data and len(response.data) > 0:
            return response.data[0] # 回傳已存在的資料
        return None
    except Exception as e:
        print(f"⚠️ 檢查重複失敗: {e}")
        return None

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
    
    # OSM 的住宿判定
    if osm_category == 'tourism' and osm_type in ['hotel', 'hostel', 'guest_house', 'motel', 'apartment']: return "住宿"
    
    return None

def get_osm_by_coordinate(lat, lng):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/2.7'}
        r = requests.get(url, headers=headers, timeout=5)
        return parse_osm_category(r.json())
    except:
        return None

def get_osm_by_name(name, lat, lng):
    try:
        viewbox = f"{lng-0.002},{lat-0.002},{lng+0.002},{lat+0.002}"
        print(f"🕵️ [DEBUG] 啟動 OSM 姓名偵探: 搜尋 '{name}'...")
        url = f"https://nominatim.openstreetmap.org/search?q={name}&format=json&viewbox={viewbox}&bounded=1&limit=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/2.7'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data:
            print("   ✅ OSM 姓名搜尋命中！")
            return parse_osm_category(data)
        return None
    except:
        return None

def determine_category_smart(title, full_text, lat, lng):
    """V2.7 分類：新增住宿關鍵字"""
    
    print(f"🕵️ [DEBUG] 啟動關鍵字掃描 (全文長度: {len(full_text)} 字)...")
    
    # 關鍵字庫
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉", "小吃", "早餐", "午餐", "晚餐", "食堂", "Tea", "飲", "冰", "滷味", "豆花", "炸雞", "烘焙", "居酒屋", "拉麵", "丼", "素食", "熟食", "攤", "店", "舖", "館", "菜", "肉", "湯"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "景點", "文創", "步道", "學校", "中心", "診所", "醫院", "教會", "宮", "殿", "古蹟", "老街", "夜市", "風景"]
    lodging_keywords = ["Hotel", "民宿", "飯店", "旅館", "酒店", "客棧", "旅店", "行館", "Resort", "住宿", "會館"]

    scan_text = (title + " " + full_text[:1000]).replace("\n", " ")
    
    # 1. 關鍵字掃描
    for kw in food_keywords:
        if kw in scan_text: return "美食"
    for kw in lodging_keywords:
        if kw in scan_text: return "住宿"
    for kw in travel_keywords:
        if kw in scan_text: return "景點"

    # 2. OSM 名字偵探
    if title and title != "未命名地點":
        cat = get_osm_by_name(title, lat, lng)
        if cat: return cat

    # 3. OSM 座標偵探
    cat = get_osm_by_coordinate(lat, lng)
    if cat: return cat
        
    return "其它"

# --- 3. 瀏覽器爬蟲 ---

def get_real_url_with_browser(url):
    print(f"🕵️ [DEBUG] 啟動 Chrome (V2.7)... 目標: {url}")
    
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
        
        # 偽造 GPS (台北)
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

# --- 4. 主流程 ---

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

def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 開始處理...")
    
    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message:
         target_url = raw_message.strip()

    if not target_url:
        print("⚠️ [DEBUG] 非地圖連結")
        reply_line(reply_token, [{"type": "text", "text": "📝 已存為純文字筆記。"}])
        return

    # 1. 爬蟲
    final_url, page_title, page_text = get_real_url_with_browser(target_url)
    
    # 2. 解析
    lat, lng = parse_coordinates(final_url)
    final_title = page_title.replace(" - Google 地圖", "").replace(" - Google Maps", "").strip()
    if final_title == "Google Maps": final_title = "未命名地點"

    # 3. 分類
    category = determine_category_smart(final_title, page_text, lat, lng)

    print(f"🕵️ [DEBUG] 準備存檔 -> 店名: {final_title} | 類別: {category}")

    if lat and lng:
        # ★★★ V2.7 新功能：檢查是否重複 ★★★
        existing_spot = check_duplicate(user_id, final_title)
        
        if existing_spot:
            # 如果已經存在，就不存了，直接回覆
            print(f"⚠️ [DEBUG] 發現重複資料，跳過寫入。")
            msg = f"😅 這家店你存過囉！\n店名: {final_title}\n分類: {existing_spot.get('category', category)}"
            reply_line(reply_token, [{"type": "text", "text": msg}])
            return

        # 不重複才寫入
        data = {
            "user_id": user_id,
            "location_name": final_title,
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
            print(f"✅ 成功寫入資料庫")
            reply_line(reply_token, [{"type": "text", "text": f"✅ 已收藏！\n店名: {final_title}\n分類: {category}"}])
        except Exception as
