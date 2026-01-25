import os
import sys
import time
import re
import requests
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

# --- 2. 瀏覽器爬蟲核心 ---

def get_real_url_with_browser(url):
    print(f"🕵️ [DEBUG] 啟動 Chrome 瀏覽器模擬... 目標: {url}")
    
    options = Options()
    options.add_argument("--headless")  # 無頭模式 (不顯示視窗)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # 偽裝成 iPhone 或一般電腦，騙過 Google
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    # 設定語系為繁體中文
    options.add_argument("--lang=zh-TW")

    driver = None
    final_url = url
    page_title = ""
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        # 開啟網址
        driver.get(url)
        
        # 等待 JavaScript 執行和跳轉 (給它一點時間)
        print("   ⏳ 等待 Google Maps JS 執行 (5秒)...")
        time.sleep(5)
        
        # 取得跳轉後的網址和標題
        final_url = driver.current_url
        page_title = driver.title
        print(f"   ✅ 瀏覽器目前網址: {final_url}")
        print(f"   ✅ 瀏覽器目前標題: {page_title}")
        
    except Exception as e:
        print(f"⚠️ [DEBUG] 瀏覽器執行錯誤: {e}")
    finally:
        if driver:
            driver.quit()
            
    return final_url, page_title

# --- 3. 解析邏輯 (共用) ---

def extract_map_url(text):
    if not text: return None
    match = re.search(r'(https?://[^\s]*(?:google|goo\.gl|maps\.app\.goo\.gl)[^\s]*)', text)
    return match.group(1) if match else None

def parse_coordinates(url):
    if not url: return None, None
    url = unquote(url)
    # 策略: 從網址抓座標 (因為瀏覽器已經跑完 JS，網址應該會變成有座標的長網址)
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    match = re.search(r'search/(-?\d+\.\d+),(-?\d+\.\d+)', url) # 某些格式
    if match: return float(match.group(1)), float(match.group(2))
    match_lat = re.search(r'!3d(-?\d+\.\d+)', url)
    match_lng = re.search(r'!4d(-?\d+\.\d+)', url)
    if match_lat and match_lng: return float(match_lat.group(1)), float(match_lng.group(2))
    return None, None

def determine_category(title):
    if not title: return "其它"
    food_keywords = ["餐廳", "咖啡", "Coffee", "Cafe", "麵", "飯", "食", "味", "餐酒館", "Bar", "甜點", "火鍋", "料理", "Bistro", "早午餐", "牛排", "壽司", "燒肉", "小吃", "早餐", "午餐", "晚餐", "食堂", "Tea", "飲", "冰"]
    travel_keywords = ["車站", "公園", "山", "海", "寺", "廟", "博物館", "步道", "農場", "樂園", "展覽", "View", "Hotel", "民宿", "景點", "文創", "步道", "學校", "中心", "診所", "醫院"]
    for kw in food_keywords:
        if kw in title: return "美食"
    for kw in travel_keywords:
        if kw in title: return "景點"
    return "其它"

# --- 4. 主流程 ---

def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 開始處理...")
    
    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message:
         target_url = raw_message.strip()

    if not target_url:
        print("⚠️ [DEBUG] 非地圖連結")
        reply_line(reply_token, [{"type": "text", "text": "📝 已存為純文字筆記。"}])
        return

    # ★★★ 使用瀏覽器爬蟲 ★★★
    final_url, page_title = get_real_url_with_browser(target_url)
    
    # 解析座標
    lat, lng = parse_coordinates(final_url)
    
    # 處理店名
    final_title = page_title.replace(" - Google 地圖", "").replace(" - Google Maps", "").strip()
    if not final_title or final_title == "Google Maps":
        final_title = "未命名地點"

    print(f"🕵️ [DEBUG] 瀏覽器解析結果 -> 座標: {lat}, {lng}, 店名: {final_title}")

    # 存入資料庫
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
            reply_line(reply_token, [{"type": "text", "text": f"✅ (瀏覽器版) 已收藏！\n店名: {final_title}"}])
        except Exception as e:
            print(f"❌ DB Error: {e}")
    else:
        print("⚠️ [DEBUG] 瀏覽器跑完了，但還是沒座標 (可能是無法解析)")
        reply_line(reply_token, [{"type": "text", "text": "⚠️ 連結已接收，但無法解析座標。"}])

if __name__ == "__main__":
    if len(sys.argv) > 3:
        # 為了測試方便，我們直接執行存檔邏輯
        handle_save_task(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print("❌ 參數不足")
