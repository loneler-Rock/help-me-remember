import os
import re
import sys
import json
import math
import requests
from supabase import create_client, Client
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

# --- LINE 回覆工具 ---
def reply_line(token, messages):
    if not token:
        print("⚠️ [DEBUG] 沒有 Reply Token，略過回覆")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}"
    }
    body = {
        "replyToken": token,
        "messages": messages
    }
    
    try:
        r = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=body)
        print(f"📤 LINE 回覆狀態: {r.status_code} {r.text}")
    except Exception as e:
        print(f"❌ LINE 回覆失敗: {e}")

# --- 2. 工具函式 ---

def calculate_distance(lat1, lon1, lat2, lon2):
    if lat2 is None or lon2 is None: return 99999
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def resolve_url_manual(url):
    """
    V1.9 核心升級：Magic Cookie + 擴大攔截
    """
    print(f"🕵️ [DEBUG] 啟動 V1.9 強力追蹤: {url}")
    
    current_url = url
    # ★★★ 殺招：直接帶入同意 Cookie，繞過 Consent Page ★★★
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cookie': 'CONSENT=YES+; SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg'
    }

    for i in range(10):
        try:
            # allow_redirects=False 禁止自動跳轉，我們要自己開車
            response = requests.get(current_url, headers=headers, allow_redirects=False, timeout=10)
            
            if response.status_code in [301, 302]:
                next_url = response.headers.get('Location')
                if not next_url:
                    break

                print(f"   ↳ 第 {i+1} 層跳轉: {next_url[:60]}...")

                # ★★★ 擴大攔截關鍵字 ★★★
                # 很多 APP 分享連結是 /search/ 或 ?query=，V1.8 漏掉了這些
                keywords = ["/place/", "!3d", "search", "query", "cid=", "googleusercontent.com/maps.google.com/46"]
                if any(k in next_url for k in keywords):
                    print("   🎯 攔截到黃金網址 (Target Found)！")
                    return next_url, ""

                if "consent.google.com" in next_url:
                    print("   ⛔ 偵測到 Consent Page 陷阱！(Cookie 失效?) 拒絕前往。")
                    return current_url, response.text 
                
                current_url = next_url
            
            elif response.status_code == 200:
                print("   ✅ 抵達終點頁面 (200 OK)")
                # 這裡的 HTML 可能就是我們要的，因為我們帶了 Cookie，理論上不會是 Consent Page
                return current_url, response.text
            
            else:
                break
                
        except Exception as e:
            print(f"⚠️ [DEBUG] 追蹤過程發生錯誤: {e}")
            break
            
    return current_url, ""

def extract_map_url(text):
    if not text: return None
    match = re.search(r'(https?://[^\s]*(?:google|goo\.gl|maps\.app\.goo\.gl)[^\s]*)', text)
    return match.group(1) if match else None

def extract_title_from_html(html_content):
    if not html_content: return None
    candidates = []
    
    match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_content)
    if match: candidates.append(match.group(1))

    match = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html_content)
    if match: 
        desc = match.group(1)
        name_part = desc.split('·')[0].strip()
        candidates.append(name_part)

    match = re.search(r'<title>(.*?)</title>', html_content)
    if match:
        t = re.sub(r' - Google\s*(Map|地圖).*', '', match.group(1)).strip()
        candidates.append(t)

    for name in candidates:
        if not name: continue
        if name.lower() in ["google maps", "google map", "google 地圖", "google"]:
            continue
        return name
    return None

def get_name_from_osm(lat, lng):
    try:
        print(f"🕵️ [DEBUG] 啟動 OSM 救援查詢 -> {lat}, {lng}")
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1&accept-language=zh-TW"
        headers = {'User-Agent': 'HelpMeRememberBot/1.0'}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if 'name' in data and data['name']: return data['name']
        if 'display_name' in data: return data['display_name'].split(',')[0]
        return None
    except:
        return None

def parse_coordinates(url, html_content=""):
    if not url: return None, None
    url = unquote(url)

    # 策略 A: 網址解析 (增強版 regex)
    # 支援 @lat,lng
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    # 支援 q=lat,lng 或 query=lat,lng
    match = re.search(r'(?:q|query)=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match: return float(match.group(1)), float(match.group(2))
    # 支援 !3d...!4d
    match_lat = re.search(r'!3d(-?\d+\.\d+)', url)
    match_lng = re.search(r'!4d(-?\d+\.\d+)', url)
    if match_lat and match_lng: return float(match_lat.group(1)), float(match_lng.group(2))

    # 策略 B: HTML 解析
    if html_content:
        # 檢查是否不幸還是掉進 Consent Page (特徵是 form action=consent...)
        if "consent.google.com" in html_content:
             print("⚠️ [DEBUG] 警告：HTML 內容疑似為 Consent Page")

        if "center=" in html_content:
            match = re.search(r'center=(-?\d+\.\d+)%2C(-?\d+\.\d+)', html_content)
            if not match: match = re.search(r'center=(-?\d+\.\d+),(-?\d+\.\d+)', html_content)
            if match: return float(match.group(1)), float(match.group(2))
        if "markers=" in html_content:
            match = re.search(r'markers=(-?\d+\.\d+)%2C(-?\d+\.\d+)', html_content)
            if not match: match = re.search(r'markers=(-?\d+\.\d+),(-?\d+\.\d+)', html_content)
            if match: return float(match.group(1)), float(match.group(2))
                
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

# --- 3. 核心功能 A: 存檔模式 ---

def handle_save_task(raw_message, user_id, reply_token):
    print(f"📥 [存檔模式] 開始處理...")
    print(f"🕵️ [DEBUG] 收到原始字串 -> [{raw_message}]")

    if not raw_message or not raw_message.strip():
        return

    target_url = extract_map_url(raw_message)
    if not target_url and ("google" in raw_message or "goo.gl" in raw_message) and "http" in raw_message:
         target_url = raw_message.strip()

    print(f"🕵️ [DEBUG] 判定處理網址 -> [{target_url}]")

    final_title = "未命名地點"
    message_to_user = ""

    if target_url:
        final_url, html_content = resolve_url_manual(target_url)
        print(f"🕵️ [DEBUG] 最終鎖定網址 -> [{final_url}]")
        
        # 1. 網址找店名
        if "/place/" in final_url:
            try:
                parts = unquote(final_url).split("/place/")[1].split("/")[0]
                final_title = parts.replace("+", " ")
            except:
                pass
        
        # 2. HTML 找店名
        if final_title == "未命名地點" or final_title.startswith("http"):
            html_title = extract_title_from_html(html_content)
            if html_title:
                final_title = html_title
        
        lat, lng = parse_coordinates(final_url, html_content)
        print(f"🕵️ [DEBUG] 解析結果 -> 座標: {lat}, {lng}, 店名: {final_title}")

        is_bad_name = (final_title == "未命名地點" or final_title.startswith("http") or "google" in final_title.lower())
        if lat and lng and is_bad_name:
            osm_name = get_name_from_osm(lat, lng)
            if osm_name:
                final_title = osm_name
                print(f"🕵️ [DEBUG] OSM 救援成功: {final_title}")
        
        category = determine_category(final_title)

        if lat and lng:
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
                print(f"✅ 成功寫入資料庫: {final_title}")
                message_to_user = f"✅ 已收藏地點！\n類別: {category}\n店名: {final_title}"
            except Exception as e:
                print(f"❌ DB Error: {e}")
                message_to_user = "❌ 系統錯誤，儲存失敗。"
        else:
            print("⚠️ [DEBUG] 找不到座標，存入待處理")
            backup_save(user_id, final_title, raw_message, target_url)
            message_to_user = "⚠️ 連結已接收，但無法解析座標。"
    else:
        print("⚠️ [DEBUG] 非地圖連結")
        backup_save(user_id, raw_message[:30], raw_message, "")
        message_to_user = "📝 已存為純文字筆記。"

    if message_to_user:
        reply_line(reply_token, [{"type": "text", "text": message_to_user}])

def backup_save(user_id, title, content, url):
    data = {
        "user_id": user_id,
        "location_name": "[待處理] " + title,
        "google_map_url": url,
        "address": content,
        "latitude": 0,
        "longitude": 0,
        "category": "其它",
        "created_at": "now()"
    }
    try:
        supabase.table("map_spots").insert(data).execute()
        print("✅ 已寫入備份")
    except Exception as e:
        print(f"❌ 備份寫入失敗: {e}")

# --- 4. 核心功能 B: 雷達模式 (不變) ---

def handle_radar_task(user_lat, user_lng, user_id, reply_token):
    # ... (保持原樣，篇幅關係略，反正這部分沒問題) ...
    # 為了確保完整性，如果你直接複製，請保留你原本的 handle_radar_task 
    # 或是直接把上面的 handle_radar_task 複製下來 (我為了節省篇幅這裡只有 def)
    print(f"📡 [雷達模式] 搜尋附近: {user_lat}, {user_lng}")

    try:
        response = supabase.table("map_spots").select("*").neq("latitude", 0).execute()
        spots = response.data

        for spot in spots:
            dist = calculate_distance(user_lat, user_lng, spot['latitude'], spot['longitude'])
            spot['distance_km'] = dist

        nearby_spots = sorted(spots, key=lambda x: x['distance_km'])[:5]

        if not nearby_spots:
            reply_line(reply_token, [{"type": "text", "text": "📭 附近 5km 內沒有你的收藏。"}])
            return

        bubbles = []
        for spot in nearby_spots:
            dist_text = f"{spot['distance_km']:.1f} km"
            nav_url = f"https://www.google.com/maps/search/?api=1&query={spot['latitude']},{spot['longitude']}"
            cat_val = spot.get('category') or "其它"
            title_val = spot.get('location_name') or "未命名"
            cat_color = "#E63946" if cat_val == "美食" else ("#457B9D" if cat_val == "景點" else "#1D8446")

            bubble = {
                "type": "bubble", "size": "micro",
                "body": {
                    "type": "box", "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": cat_val, "weight": "bold", "color": cat_color, "size": "xxs"},
                        {"type": "text", "text": title_val, "weight": "bold", "size": "sm", "wrap": True, "margin": "xs"},
                        {"type": "text", "text": dist_text, "size": "xs", "color": "#aaaaaa", "margin": "xs"}
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [{"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": "導航", "uri": nav_url}}]
                }
            }
            bubbles.append(bubble)
        flex_message = {"type": "flex", "altText": "附近地點", "contents": {"type": "carousel", "contents": bubbles}}
        reply_line(reply_token, [flex_message])
    except Exception as e:
        print(f"❌ 雷達搜尋失敗: {e}")
        reply_line(reply_token, [{"type": "text", "text": "❌ 系統忙碌中 (Radar Error)"}])

if __name__ == "__main__":
    if len(sys.argv) > 3:
        arg1 = sys.argv[1]
        arg2 = sys.argv[2]
        arg3 = sys.argv[3]
        if re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', arg1):
            try:
                lat_str, lng_str = arg1.split(',')
                handle_radar_task(float(lat_str), float(lng_str), arg2, arg3)
            except:
                handle_save_task(arg1, arg2, arg3)
        else:
            handle_save_task(arg1, arg2, arg3)
    else:
        print("❌ 參數不足")
