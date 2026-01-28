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

# --- 1. 初始化設定 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

# --- UI配色設定 ---
CATEGORY_COLORS = {
    "美食": "#E67E22", "景點": "#27AE60", "住宿": "#2980B9", "其它": "#7F8C8D", "熱點": "#E74C3C"
}
CATEGORY_ICONS = {
    "美食": "https://cdn-icons-png.flaticon.com/512/706/706164.png",
    "景點": "https://cdn-icons-png.flaticon.com/512/2664/2664531.png",
    "住宿": "https://cdn-icons-png.flaticon.com/512/2983/2983803.png",
    "其它": "https://cdn-icons-png.flaticon.com/512/447/447031.png",
    "熱點": "https://cdn-icons-png.flaticon.com/512/785/785116.png"
}

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("缺少 SUPABASE_URL 或 SUPABASE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

def reply_line(token, messages):
    if not token: return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    try: requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})
    except Exception as e: print(f"❌ LINE 回覆失敗: {e}")

# --- 2. 輔助工具 ---
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
    sight_types = ['attraction', 'museum', 'viewpoint', 'artwork', 'gallery', 'zoo', 'theme_park', 'park', 'castle']
    if osm_cat in ['tourism', 'historic', 'leisure', 'natural']: return "景點"
    if osm_cat == 'tourism' and osm_type in ['hotel', 'hostel', 'guest_house', 'motel', 'apartment']: return "住宿"
    return None

def get_osm_by_coordinate(lat, lng):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=zh-TW"
        headers = {'User-Agent': 'ShunShunBot/4.7'}
        r = requests.get(url, headers=headers, timeout=5)
        return parse_osm_category(r.json())
    except: return None

def get_osm_by_name(name, lat, lng):
    try:
        viewbox = f"{lng-0.002},{lat-0.002},{lng+0.002},{lat+0.002}"
        url = f"https://nominatim.openstreetmap.org/search?q={name}&format=json&viewbox={viewbox}&bounded=1&limit=1&accept-language=zh-TW"
        headers = {'User-Agent': 'ShunShunBot/4.7'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data: return parse_osm_category(data)
        return None
    except: return None

def determine_category_smart(title, full_text, lat, lng):
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

# --- 4. 雷達模式 ---
def get_nearby_spots(user_id, lat, lng, limit=10, target_category="美食"):
    try:
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).execute()
        spots = response.data
        results = []
        for spot in spots:
            if target_category and spot.get('category', '其它') != target_category: continue
            s_lat, s_lng = spot.get('latitude'), spot.get('longitude')
            if s_lat and s_lng:
                degree_dist = math.sqrt((s_lat - lat)**2 + (s_lng - lng)**2)
                spot['dist_score'] = degree_dist
                spot['dist_meters'] = int(degree_dist * 111 * 1000)
                results.append(spot)
        results.sort(key=lambda x: x['dist_score'])
        return results[:limit]
    except Exception as e:
        print(f"❌ 雷達查詢失敗: {e}")
        return []

def get_hotspots_rpc(lat, lng):
    try:
        print(f"🔥 呼叫 RPC: get_hotspots, 中心: {lat}, {lng}")
        response = supabase.rpc("get_hotspots", {"user_lat": lat, "user_lng": lng}).execute()
        return response.data
    except Exception as e:
        print(f"❌ RPC 呼叫失敗: {e}")
        return []

def create_radar_flex(spots, center_lat, center_lng, is_hotspot_mode=False):
    if not spots and not is_hotspot_mode:
        return {"type": "text", "text": "😿 喵嗚... 附近的碗盤是空的。\n順順找不到您存過的店，試試看「貓友熱點」偷看別家貓咪吃什麼？"}
    
    if not spots and is_hotspot_mode:
        return {"type": "text", "text": "❄️ 這裡冷冷清清...\n方圓 500 公尺內還沒有貓咪來踩點過，快當第一個開拓者吧！🐈"}

    bubbles = []
    for spot in spots:
        if is_hotspot_mode:
            name = spot['name']
            cat = "熱點"
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
        
        bubble = {
          "type": "bubble", "size": "micro",
          "header": {
            "type": "box", "layout": "vertical",
            "contents": [{"type": "text", "text": cat, "color": "#ffffff", "size": "xs", "weight": "bold"}],
            "backgroundColor": color, "paddingAll": "sm"
          },
          "body": {
            "type": "box", "layout": "vertical",
            "contents": [
              {"type": "text", "text": name, "weight": "bold", "size": "sm", "wrap": True},
              {
                "type": "box", "layout": "baseline",
                "contents": [
                  {"type": "icon", "url": icon, "size": "xs"},
                  {"type": "text", "text": note, "size": "xs", "color": "#8c8c8c", "margin": "sm"}
                ], "margin": "md"
              }
            ]
          },
          "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
              {"type": "button", "action": {"type": "uri", "label": "🐾 跟著順順走", "uri": map_url}, "style": "primary", "color": color, "height": "sm"}
            ]
          }
        }
        bubbles.append(bubble)
        if len(bubbles) >= 10: break

    if not is_hotspot_mode:
        switch_bubble = {
            "type": "bubble", "size": "micro",
            "body": {
                "type": "box", "layout": "vertical", "justifyContent": "center", "height": "150px",
                "contents": [
                    {"type": "text", "text": "別家貓咪\n都吃什麼？", "align": "center", "weight": "bold", "wrap": True},
                    {"type": "button", 
                     "action": {"type": "message", "label": "🐟 貓友熱點", "text": f"熱點 {center_lat},{center_lng}"}, 
                     "style": "secondary", "margin": "md"}
                ]
            }
        }
        bubbles.append(switch_bubble)

    title_text = "🔥 貓友們都吃這家" if is_hotspot_mode else "🐾 順順的私房筆記"
    return {"type": "flex", "altText": title_text, "contents": {"type": "carousel", "contents": bubbles}}

# --- 5. 說明模式 ---
def handle_help_message(reply_token):
    help_text = (
        "😺 **順順地圖使用手冊** 😺\n\n"
        "我是站長順順，專門幫你記下好吃的！\n\n"
        "👇 **【順順帶路】(右邊按鈕)**\n"
        "傳送位置給我，我會找出 **你** 存過的私房名單！\n\n"
        "👇 **【貓友熱點】(中間按鈕)**\n"
        "傳送位置給我，我會找出 **大家** 都在吃的熱門店！\n\n"
        "👇 **【怎麼存檔？】**\n"
        "直接把 Google Maps 連結分享給我，我就會收進筆記本囉！🐾"
    )
    reply_line(reply_token, [{"type": "text", "text": help_text}])

# --- 6. 喚醒位置工具 ---
def request_user_location(reply_token):
    msg = {
        "type": "text", "text": "👇 奴才請按下面按鈕，告訴順順你在哪裡？",
        "quickReply": {"items": [{"type": "action", "action": {"type": "location", "label": "📍 傳送位置給順順"}}]}
    }
    reply_line(reply_token, [msg])

# --- 7. 主程式邏輯 (V4.7 絕對優先版) ---
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
        reply_line(reply_token, [{"type": "text", "text": "😿 這是什麼？順順只吃 Google Maps 的連結喔！\n\n(如果是想找餐廳，請按【順順帶路】或【貓友熱點】)"}])
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

def handle_radar_task(lat_str, lng_str, user_id, reply_token, mode="personal"):
    print(f"📡 [雷達模式: {mode}] 順順開始偵測... 中心: {lat_str}, {lng_str}")
    try:
        lat = float(lat_str)
        lng = float(lng_str)
        if mode == "hotspot":
            spots = get_hotspots_rpc(lat, lng)
            flex_msg = create_radar_flex(spots, lat, lng, is_hotspot_mode=True)
        else:
            spots = get_nearby_spots(user_id, lat, lng, limit=10, target_category="美食")
            flex_msg = create_radar_flex(spots, lat, lng, is_hotspot_mode=False)
        reply_line(reply_token, [flex_msg])
    except ValueError:
        reply_line(reply_token, [{"type": "text", "text": "❌ 座標資料錯誤"}])

if __name__ == "__main__":
    if len(sys.argv) > 3:
        input_content = sys.argv[1].strip()
        user_id = sys.argv[2]
        reply_token = sys.argv[3]
        
        # ★★★ 1. 強制優先偵測「教學/說明」 ★★★
        # 只要文字裡包含「教學」或「說明」，不管有沒有其他字，立刻啟動說明模式
        if "教學" in input_content or "說明" in input_content or "help" in input_content.lower():
            handle_help_message(reply_token)

        # 2. 偵測熱點指令 (熱點 25.03,121.56)
        elif input_content.startswith("熱點 "):
            try:
                coords = input_content.split(" ")[1]
                lat_str, lng_str = coords.split(',')
                handle_radar_task(lat_str, lng_str, user_id, reply_token, mode="hotspot")
            except: reply_line(reply_token, [{"type": "text", "text": "😿 熱點指令格式錯誤"}])

        # 3. 偵測座標 (傳送位置時)
        elif re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', input_content):
            lat_str, lng_str = input_content.split(',')
            handle_radar_task(lat_str, lng_str, user_id, reply_token, mode="personal")
        
        # 4. 偵測功能按鈕 (順順帶路、貓友熱點)
        # 只要不是上面那幾種狀況，且包含關鍵字，就跳出位置按鈕
        elif any(k in input_content for k in ["雷達", "位置", "附近美食", "找餐廳", "順順", "帶路", "貓友", "熱點"]):
            request_user_location(reply_token)
            
        # 5. 最後才試著當作網址存檔
        else:
            handle_save_task(input_content, user_id, reply_token)
    else:
        print("❌ 參數不足")
