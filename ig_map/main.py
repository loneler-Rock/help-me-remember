import os
import sys
import time
import re
import requests
import json
import math
from supabase import create_client, Client
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import unquote
from selenium.webdriver.common.by import By

# --- 初始化 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

CATEGORY_COLORS = {
    "美食": "#E67E22", "景點": "#27AE60", "住宿": "#2980B9", 
    "其它": "#7F8C8D", "熱點": "#E74C3C", "廣告": "#D4AF37"
}

CATEGORY_ICONS = {
    "美食": "https://cdn-icons-png.flaticon.com/512/706/706164.png",
    "景點": "https://cdn-icons-png.flaticon.com/512/2664/2664531.png",
    "住宿": "https://cdn-icons-png.flaticon.com/512/2983/2983803.png",
    "其它": "https://cdn-icons-png.flaticon.com/512/447/447031.png",
    "熱點": "https://cdn-icons-png.flaticon.com/512/785/785116.png",
    "廣告": "https://cdn-icons-png.flaticon.com/512/2549/2549860.png"
}

try:
    if not SUPABASE_URL or not SUPABASE_KEY: raise ValueError("缺少 SUPABASE KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase Init Error: {e}")
    sys.exit(1)

def reply_line(token, messages):
    if not token: return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})

# --- ★ V5.3 新增：狀態管理 (順順的記憶力) ---
def update_user_state(user_id, mode, category):
    """更新使用者的當下意圖"""
    try:
        data = {"user_id": user_id, "last_mode": mode, "last_category": category, "updated_at": "now()"}
        supabase.table("user_states").upsert(data).execute()
        print(f"🧠 [記憶] 用戶 {user_id} 想找: {mode} / {category}")
    except Exception as e:
        print(f"❌ 記憶寫入失敗: {e}")

def get_user_state(user_id):
    """讀取使用者的當下意圖"""
    try:
        response = supabase.table("user_states").select("*").eq("user_id", user_id).execute()
        if response.data:
            return response.data[0]
    except: pass
    # 預設值
    return {"last_mode": "personal", "last_category": "美食"}

# --- 功能函式 (維持不變) ---
def parse_osm_category(data):
    if not data: return None
    item = data[0] if isinstance(data, list) and data else data
    if not item: return None
    osm_cat = item.get('category', '') or item.get('class', '')
    osm_type = item.get('type', '')
    if not osm_cat and 'addresstype' in item: osm_cat = item['addresstype']
    food_types = ['restaurant', 'cafe', 'fast_food', 'food_court', 'bar', 'pub', 'ice_cream', 'biergarten', 'deli']
    if osm_cat == 'amenity' and osm_type in food_types: return "美食"
    if osm_cat == 'shop' and osm_type in ['food', 'bakery', 'pastry', 'beverage', 'coffee', 'tea', 'deli']: return "美食"
    sight_types = ['attraction', 'museum', 'viewpoint', 'artwork', 'gallery', 'zoo', 'theme_park', 'park', 'castle', 'aquarium']
    if osm_cat in ['tourism', 'historic', 'leisure', 'natural']: return "景點"
    if osm_cat == 'amenity' and osm_type in ['arts_centre', 'library', 'theatre', 'place_of_worship']: return "景點"
    if osm_cat == 'tourism' and osm_type in ['hotel', 'hostel', 'guest_house', 'motel', 'apartment', 'camp_site']: return "住宿"
    return None

def get_osm_by_coordinate(lat, lng):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=zh-TW"
        headers = {'User-Agent': 'ShunShunBot/5.3'}
        r = requests.get(url, headers=headers, timeout=5)
        return parse_osm_category(r.json())
    except: return None

def get_osm_by_name(name, lat, lng):
    try:
        viewbox = f"{lng-0.002},{lat-0.002},{lng+0.002},{lat+0.002}"
        url = f"https://nominatim.openstreetmap.org/search?q={name}&format=json&viewbox={viewbox}&bounded=1&limit=1&accept-language=zh-TW"
        headers = {'User-Agent': 'ShunShunBot/5.3'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data: return parse_osm_category(data)
        return None
    except: return None

def determine_category_smart(title, full_text, lat, lng):
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉", "小吃", "早餐", "午餐", "晚餐", "食堂", "Tea", "飲", "冰", "滷味", "豆花", "炸雞", "烘焙", "居酒屋", "拉麵", "丼", "素食", "熟食", "攤", "店", "舖", "館", "菜", "肉", "湯", "餅", "餃"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "景點", "文創", "步道", "學校", "中心", "診所", "醫院", "教會", "宮", "殿", "古蹟", "老街", "夜市", "風景", "漁港", "碼頭", "溫泉", "瀑布", "吊橋", "露營", "Camp", "DIY", "劇場", "影城", "動物園", "植物園", "美術館", "紀念館", "廣場", "遊客中心"]
    lodging_keywords = ["Hotel", "民宿", "飯店", "旅館", "酒店", "客棧", "旅店", "行館", "Resort", "住宿", "會館", "商旅", "BnB"]
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
    print(f"🕵️ [DEBUG] 順順正在聞這個網址... 目標: {url}")
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
    except Exception as e: print(f"⚠️ [DEBUG] 瀏覽器執行錯誤: {e}")
    finally:
        if driver: driver.quit()
    return final_url, page_title, page_text

# --- 核心：雷達搜尋 ---
def get_nearby_spots(user_id, lat, lng, limit=10, target_category="美食"):
    try:
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).execute()
        spots = response.data
        results = []
        for spot in spots:
            if target_category and spot.get('category', '其它') != target_category: 
                continue
            s_lat, s_lng = spot.get('latitude'), spot.get('longitude')
            if s_lat and s_lng:
                degree_dist = math.sqrt((s_lat - lat)**2 + (s_lng - lng)**2)
                spot['dist_score'] = degree_dist
                spot['dist_meters'] = int(degree_dist * 111 * 1000)
                results.append(spot)
        results.sort(key=lambda x: x['dist_score'])
        return results[:limit]
    except Exception as e: return []

# ★ V5.3 升級：熱點也支援分類篩選
def get_hotspots_rpc(lat, lng, target_category=None):
    try:
        params = {"user_lat": lat, "user_lng": lng}
        if target_category:
            params["target_category"] = target_category
        
        response = supabase.rpc("get_hotspots", params).execute()
        return response.data
    except Exception as e: return []

# --- 核心：產生 Flex Message ---
def create_radar_flex(spots, center_lat, center_lng, mode="personal", category="美食"):
    
    title_text = f"🐾 順順的{category}筆記" if mode == "personal" else f"🔥 熱門{category}"
    
    if not spots:
        msg = f"😿 喵嗚... 附近找不到「{category}」耶。"
        return {"type": "text", "text": msg}

    bubbles = []
    for spot in spots:
        is_ad = False
        if mode == "hotspot":
            name = spot['name']
            ad_priority = spot.get('ad_priority', 0)
            if ad_priority > 0:
                is_ad = True
                cat = "廣告"
                note = "👑 順順嚴選・人氣推薦"
                name = f"👑 {name}"
            else:
                cat = "熱點"
                # 熱點模式也要顯示原本的分類，或者統一顯示熱點
                real_cat = spot.get('category', '熱點')
                note = f"🔥 {spot['popularity']} 位貓友認證"
            map_url = spot['google_url'] or "http://maps.google.com"
        else:
            name = spot['location_name']
            cat = spot.get('category', '其它')
            dist = spot.get('dist_meters', 0)
            note = f"🐾 距離約 {round(dist/1000, 1)} km" if dist > 1000 else f"🐾 距離約 {dist} m"
            map_url = spot.get('google_map_url') or spot.get('address')

        color = CATEGORY_COLORS.get(cat, "#7F8C8D")
        icon = CATEGORY_ICONS.get(cat, CATEGORY_ICONS["其它"])
        bg_color = color if not is_ad else "#F1C40F" 

        bubble = {
          "type": "bubble", "size": "micro",
          "header": {
            "type": "box", "layout": "vertical",
            "contents": [{"type": "text", "text": "順順嚴選" if is_ad else cat, "color": "#ffffff", "size": "xs", "weight": "bold"}],
            "backgroundColor": bg_color, "paddingAll": "sm"
          },
          "body": {
            "type": "box", "layout": "vertical",
            "contents": [
              {"type": "text", "text": name, "weight": "bold", "size": "sm", "wrap": True, "color": "#E67E22" if is_ad else "#000000"},
              {
                "type": "box", "layout": "baseline",
                "contents": [
                  {"type": "icon", "url": icon, "size": "xs"},
                  {"type": "text", "text": note, "size": "xs", "color": "#D35400" if is_ad else "#8c8c8c", "margin": "sm", "weight": "bold" if is_ad else "regular"}
                ], "margin": "md"
              }
            ]
          },
          "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
              {"type": "button", "action": {"type": "uri", "label": "👑 立即前往" if is_ad else "🐾 跟著順順走", "uri": map_url}, "style": "primary", "color": bg_color, "height": "sm"}
            ]
          }
        }
        bubbles.append(bubble)
        if len(bubbles) >= 10: break

    # ★ V5.3 切換卡片簡化 (因為選單已經變 6 格，這裡只需要提供最核心的互換)
    # 邏輯：如果你在看私藏，最後一張卡片問你要不要看熱門；反之亦然。
    
    switch_mode = "hotspot" if mode == "personal" else "personal"
    switch_text = f"🔥 改找熱門{category}" if mode == "personal" else f"🐾 改找私藏{category}"
    # 這裡的指令必須精確，才能觸發狀態更新
    switch_cmd = f"熱點 {category} {center_lat},{center_lng}" if mode == "personal" else f"找{category} {center_lat},{center_lng}"
    
    # 修正：如果是「找私藏」，指令是 "找美食 座標"，但 "找美食" 已經在 main 被攔截為狀態更新，這裡直接傳座標?
    # 不，這裡我們用特殊的直接指令來繞過，或者依舊用狀態更新。
    # 最穩的做法：讓按鈕帶有關鍵字
    if mode == "personal":
        btn_cmd = f"熱點 {category} {center_lat},{center_lng}" # 觸發熱點模式
    else:
        # 回私藏
        btn_cmd = f"找{category} {center_lat},{center_lng}" 
        # 但 "找美食" 會被視為新按鈕按下，要求傳位置。
        # 這裡我們用一個技巧：直接傳座標，但因為我們沒更新狀態，它會讀取舊狀態?
        # 不，我們需要一個指令能同時「設定狀態 + 執行搜尋」。
        # 為了簡化，V5.3 這裡我們先只做「熱點切換」，回私藏建議重新按選單。
        btn_cmd = f"{center_lat},{center_lng}" # 直接傳座標，會讀取最後狀態 (通常就是你現在看的分類)

    switch_bubble = {
        "type": "bubble", "size": "micro",
        "body": {
            "type": "box", "layout": "vertical", "justifyContent": "center", "height": "160px",
            "contents": [
                 {"type": "text", "text": "換個口味？", "align": "center", "weight": "bold"},
                 {"type": "button", "action": {"type": "message", "label": "🔥 看看熱點" if mode == "personal" else "🐾 回看私藏", "text": f"熱點 {category} {center_lat},{center_lng}" if mode == "personal" else f"{center_lat},{center_lng}"}, "style": "secondary", "margin": "md"}
            ]
        }
    }
    bubbles.append(switch_bubble)

    return {"type": "flex", "altText": title_text, "contents": {"type": "carousel", "contents": bubbles}}

def handle_help_message(reply_token):
    help_text = (
        "😺 **順順地圖使用手冊** 😺\n\n"
        "👇 **【私藏系列】(上排按鈕)**\n"
        "找你自己存過的美食、景點、住宿。\n\n"
        "👇 **【熱門系列】(下排按鈕)**\n"
        "看看大家都在哪裡排隊！\n\n"
        "👇 **【怎麼存檔？】**\n"
        "直接把 Google Maps 連結分享給我即可！🐾"
    )
    reply_line(reply_token, [{"type": "text", "text": help_text}])

def request_user_location(reply_token, text_hint="告訴順順你在哪裡？"):
    msg = {
        "type": "text", "text": f"👇 {text_hint}",
        "quickReply": {"items": [{"type": "action", "action": {"type": "location", "label": "📍 傳送位置"}}]}
    }
    reply_line(reply_token, [msg])

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
    print(f"📥 [存檔模式] 順順收到罐罐了...")
    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message: target_url = raw_message.strip()
    if not target_url:
        reply_line(reply_token, [{"type": "text", "text": "😿 這是什麼？順順只吃 Google Maps 的連結喔！"}])
        return
    final_url, page_title, page_text = get_real_url_with_browser(target_url)
    lat, lng = parse_coordinates(final_url)
    final_title = page_title.replace(" - Google 地圖", "").replace(" - Google Maps", "").strip()
    if final_title == "Google Maps": final_title = "未命名地點"
    category = determine_category_smart(final_title, page_text, lat, lng)
    if lat and lng:
        existing_id = check_duplicate(user_id, final_title)
        data = {"user_id": user_id, "location_name": final_title, "google_map_url": final_url, "address": final_url, "latitude": lat, "longitude": lng, "category": category, "geom": f"POINT({lng} {lat})", "created_at": "now()"}
        try:
            if existing_id: supabase.table("map_spots").update(data).eq("id", existing_id).execute()
            else: supabase.table("map_spots").insert(data).execute()
            reply_line(reply_token, [{"type": "text", "text": f"🐾 順順幫你記好了！\n\n📍 {final_title}\n🏷️ 分類：{category}\n\n已放入秘密基地，隨時可以召喚！"}])
        except Exception as e: reply_line(reply_token, [{"type": "text", "text": "😿 系統吃壞肚子了 (Error)"}])
    else: reply_line(reply_token, [{"type": "text", "text": "😿 順順聞不到這個地點的味道 (無法解析座標)。"}])

def handle_radar_task(lat_str, lng_str, user_id, reply_token, mode=None, category=None):
    # ★ V5.3 邏輯：如果有指定模式就用指定的，沒有就去查「記憶」
    if not mode or not category:
        state = get_user_state(user_id)
        mode = mode or state.get("last_mode", "personal")
        category = category or state.get("last_category", "美食")
    
    print(f"📡 [雷達模式: {mode} - {category}] 順順開始偵測... 中心: {lat_str}, {lng_str}")
    
    try:
        lat = float(lat_str)
        lng = float(lng_str)
        if mode == "hotspot":
            spots = get_hotspots_rpc(lat, lng, target_category=category) # 熱點也支援分類
            flex_msg = create_radar_flex(spots, lat, lng, mode="hotspot", category=category)
        else:
            spots = get_nearby_spots(user_id, lat, lng, limit=10, target_category=category)
            flex_msg = create_radar_flex(spots, lat, lng, mode="personal", category=category)
        reply_line(reply_token, [flex_msg])
    except ValueError:
        reply_line(reply_token, [{"type": "text", "text": "❌ 座標資料錯誤"}])

# --- 主程式入口 (V5.3 記憶升級版) ---
if __name__ == "__main__":
    if len(sys.argv) > 3:
        try:
            raw_input = sys.argv[1].strip()
            input_content = raw_input
        except:
            input_content = ""
            
        user_id = sys.argv[2]
        reply_token = sys.argv[3]

        if "教學" in input_content or "說明" in input_content or "help" in input_content.lower():
            handle_help_message(reply_token)

        # ★ 六格按鈕邏輯 (先記住狀態，再要位置) ★
        
        # 1. 找美食 (私藏)
        elif input_content == "找美食":
            update_user_state(user_id, "personal", "美食")
            request_user_location(reply_token, "想吃什麼？傳送位置給順順！")

        # 2. 找景點 (私藏)
        elif input_content == "找景點":
            update_user_state(user_id, "personal", "景點")
            request_user_location(reply_token, "想去哪玩？傳送位置給順順！")

        # 3. 找住宿 (私藏)
        elif input_content == "找住宿":
            update_user_state(user_id, "personal", "住宿")
            request_user_location(reply_token, "今晚住哪？傳送位置給順順！")

        # 4. 熱門美食
        elif "熱點" in input_content and "美食" in input_content:
            update_user_state(user_id, "hotspot", "美食")
            request_user_location(reply_token, "搜尋熱門美食中... 請傳送位置！")
        
        # 5. 熱門景點
        elif "熱點" in input_content and "景點" in input_content:
            update_user_state(user_id, "hotspot", "景點")
            request_user_location(reply_token, "搜尋熱門景點中... 請傳送位置！")

        # --- 特殊處理：切換按鈕帶座標的指令 ---
        # 格式: "熱點 景點 25.03,121.56"
        elif input_content.startswith("熱點 "):
            parts = input_content.split(" ")
            if len(parts) >= 3 and "," in parts[-1]: # 格式: 熱點 分類 座標
                cat = parts[1]
                coords = parts[2]
                lat_str, lng_str = coords.split(',')
                # 直接執行，不存狀態(或是存也可以)
                handle_radar_task(lat_str, lng_str, user_id, reply_token, mode="hotspot", category=cat)
            elif len(parts) == 2 and "," in parts[1]: # 舊格式: 熱點 座標 (預設美食)
                coords = parts[1]
                lat_str, lng_str = coords.split(',')
                handle_radar_task(lat_str, lng_str, user_id, reply_token, mode="hotspot", category="美食")

        # 純座標 (這是關鍵！讀取記憶！)
        elif re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', input_content):
            lat_str, lng_str = input_content.split(',')
            handle_radar_task(lat_str, lng_str, user_id, reply_token) # 不傳參數，讓它去查 DB 記憶

        # 其他關鍵字
        elif any(k in input_content for k in ["雷達", "位置", "順順", "帶路"]):
            request_user_location(reply_token)

        else:
            handle_save_task(input_content, user_id, reply_token)
    else:
        print("❌ 參數不足")
