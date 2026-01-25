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

# --- 2. 輔助工具 ---

def get_name_from_osm(lat, lng):
    """OSM 救援 (僅作為最後備案)"""
    try:
        print(f"🕵️ [DEBUG] Google 無名，啟動 OSM 救援 -> {lat}, {lng}")
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/2.2'}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        
        if 'name' in data and data['name']: return data['name']
        if 'display_name' in data: return data['display_name'].split(',')[0]
        return None
    except Exception as e:
        print(f"⚠️ [DEBUG] OSM 查詢失敗: {e}")
        return None

# --- 3. 瀏覽器爬蟲核心 (V2.2) ---

def get_real_url_with_browser(url):
    print(f"🕵️ [DEBUG] 啟動 Chrome (V2.2 Google優先模式)... 目標: {url}")
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # 強制設定 Accept-Language 標頭 (比 --lang 更有效)
    options.add_experimental_option('prefs', {'intl.accept_languages': 'zh-TW,zh;q=0.9,en;q=0.8'})
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

    driver = None
    final_url = url
    page_title = ""
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        # 偽造 GPS：台北 (為了讓 Google 覺得我們是台灣人)
        params = {
            "latitude": 25.033964,
            "longitude": 121.564468,
            "accuracy": 100
        }
        driver.execute_cdp_cmd("Emulation.setGeolocationOverride", params)

        # 開啟網址 (帶上強制中文參數)
        if "?" in url:
            target_url = url + "&hl=zh-TW&gl=TW"
        else:
            target_url = url + "?hl=zh-TW&gl=TW"
            
        driver.get(target_url)
        
        # 等待轉址
        print("   ⏳ 等待頁面載入 (6秒)...")
        time.sleep(6)
        
        final_url = driver.current_url
        page_title = driver.title
        print(f"   ✅ 瀏覽器抓取標題: {page_title}")
        
    except Exception as e:
        print(f"⚠️ [DEBUG] 瀏覽器執行錯誤: {e}")
    finally:
        if driver:
            driver.quit()
            
    return final_url, page_title

# --- 4. 解析與存檔 ---

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

def determine_category(title):
    if not title: return "其它"
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉", "小吃", "早餐", "午餐", "晚餐", "食堂", "Tea", "飲", "冰", "滷味"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "Hotel", "民宿", "景點", "文創", "步道", "學校", "中心", "診所", "醫院"]
    for kw in food_keywords:
        if kw in title: return "美食"
    for kw in travel_keywords:
        if kw in title: return "景點"
    return "其它"

def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 開始處理...")
    
    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message:
         target_url = raw_message.strip()

    if not target_url:
        print("⚠️ [DEBUG] 非地圖連結")
        reply_line(reply_token, [{"type": "text", "text": "📝 已存為純文字筆記。"}])
        return

    # 1. 啟動瀏覽器
    final_url, page_title = get_real_url_with_browser(target_url)
    
    # 2. 解析座標
    lat, lng = parse_coordinates(final_url)
    
    # 3. 處理店名
    # 清洗掉 " - Google 地圖" 等字樣
    final_title = page_title.replace(" - Google 地圖", "").replace(" - Google Maps", "").strip()
    
    # 移除 "Google Maps" 這種無意義標題
    if final_title == "Google Maps":
        final_title = "未命名地點"

    # ★★★ V2.2 邏輯：Google 有給名字就用 Google 的，不要雞婆去問 OSM (除非完全沒名字) ★★★
    if (not final_title or final_title == "未命名地點") and lat and lng:
        print("⚠️ Google 未提供店名，嘗試 OSM 救援...")
        osm_name = get_name_from_osm(lat, lng)
        if osm_name:
            final_title = osm_name
            print(f"✅ OSM 救援成功: {final_title}")
    
    # 如果 Google 給了英文名 (Countless Lu Wei)，我們就接受它！
    # 因為這代表這個座標真的是這家店，而不是隔壁的髮廊。

    print(f"🕵️ [DEBUG] 最終存檔資料 -> 座標: {lat}, {lng}, 店名: {final_title}")

    # 4. 存入資料庫
    if lat and lng:
        data = {
            "user_id": user_id,
            "location_name": final_title,
            "google_map_url": final_url,
            "address": final_url,
            "latitude": lat,
            "longitude": lng,
            "category": determine_category(final_title),
            "geom": f"POINT({lng} {lat})",
            "created_at": "now()"
        }
        try:
            supabase.table("map_spots").insert(data).execute()
            print(f"✅ 成功寫入資料庫: {final_title}")
            reply_line(reply_token, [{"type": "text", "text": f"✅ 已收藏！\n店名: {final_title}"}])
        except Exception as e:
            print(f"❌ DB Error: {e}")
    else:
        print("⚠️ [DEBUG] 瀏覽器跑完了，但還是沒座標")
        reply_line(reply_token, [{"type": "text", "text": "⚠️ 連結已接收，但無法解析座標。"}])

if __name__ == "__main__":
    if len(sys.argv) > 3:
        handle_save_task(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print("❌ 參數不足")
