import os
import json
import math
import requests
import re
import sys
from supabase import create_client, Client
from bs4 import BeautifulSoup
from urllib.parse import unquote

# --- 1. 初始化設定 ---
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
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    print("⚠️ Supabase 設定有誤")

# --- 2. LINE 回覆功能 ---
def reply_line(token, messages):
    if not token: return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})

# --- 3. 資料庫操作 ---
def update_user_state(user_id, mode, category):
    try:
        data = {"user_id": user_id, "last_mode": mode, "last_category": category, "updated_at": "now()"}
        supabase.table("user_states").upsert(data).execute()
    except: pass

def get_user_state(user_id):
    try:
        response = supabase.table("user_states").select("*").eq("user_id", user_id).execute()
        if response.data: return response.data[0]
    except: pass
    return {"last_mode": "personal", "last_category": "美食"}

# --- 關鍵字猜分類 ---
def guess_category_by_name(name):
    name = name.lower()
    spot_keywords = ["森林", "公園", "步道", "館", "寺", "廟", "宮", "堂", "中心", "農場", "樂園", "廣場", "車站", "碼頭", "瀑布", "景點", "風景", "山", "湖", "潭", "洞"]
    food_keywords = ["咖啡", "cafe", "coffee", "廚房", "餐廳", "料理", "麵", "飯", "食", "味", "飲", "茶", "湯", "肉", "鍋", "餅", "攤", "店", "bar", "bistro", "bakery", "甜點"]
    hotel_keywords = ["飯店", "酒店", "民宿", "旅店", "旅館", "hotel", "hostel", "bnb"]

    for k in hotel_keywords:
        if k in name: return "住宿"
    for k in spot_keywords:
        if k in name: return "景點"
    for k in food_keywords:
        if k in name: return "美食"
    return "其它"

# --- 強化版：抓標題 + 抓座標 ---
def analyze_url(url):
    try:
        headers = {
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        
        response = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        final_url = response.url 
        
        # 1. 抓座標
        lat, lng = 0.0, 0.0
        coords_match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
        if coords_match:
            lat = float(coords_match.group(1))
            lng = float(coords_match.group(2))
        
        # 2. 抓標題
        title_candidate = "新地標 (待整理)"
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            og_title = soup.find("meta", property="og:title")
            raw_title = ""
            if og_title and og_title.get("content"):
                raw_title = og_title["content"]
            elif soup.title and soup.title.string:
                raw_title = soup.title.string
            
            if raw_title:
                clean_title = raw_title.split('·')[0] 
                clean_title = clean_title.split('- Google')[0]
                clean_title = clean_title.strip()
                if clean_title and "Google Maps" not in clean_title:
                    title_candidate = clean_title
            
            if title_candidate == "新地標 (待整理)" and "/place/" in final_url:
                try:
                    parts = final_url.split("/place/")[1]
                    name_part = parts.split("/")[0] 
                    decoded_name = unquote(name_part).replace("+", " ")
                    title_candidate = decoded_name
                except: pass

        return title_candidate, lat, lng

    except Exception as e:
        print(f"⚠️ 分析失敗: {e}")
        return "新地標 (待整理)", 0.0, 0.0

def save_map_link(user_id, url):
    try:
        print(f"正在分析網址: {url}")
        fetched_name, lat, lng = analyze_url(url)
        
        if fetched_name in ["Google Maps", "Google 地圖"]:
            fetched_name = "新地標 (Google Maps)"

        guessed_category = guess_category_by_name(fetched_name)

        data = {
            "user_id": user_id,
            "address": url,
            "google_map_url": url, 
            "location_name": fetched_name,
            "category": guessed_category,
            "latitude": lat,
            "longitude": lng,
            "created_at": "now()"
        }
        
        supabase.table("map_spots").insert(data).execute()
        return fetched_name, guessed_category
    except Exception as e:
        print(f"❌ 儲存失敗: {e}")
        return None, "其它"

# --- 4. 搜尋功能 ---
def get_hotspots_rpc(lat, lng, target_category=None):
    try:
        params = {"user_lat": lat, "user_lng": lng}
        if target_category: params["target_category"] = target_category
        response = supabase.rpc("get_hotspots", params).execute()
        return response.data
    except: return []

def get_nearby_spots(user_id, lat, lng, limit=10, target_category="美食"):
    try:
        response = supabase.table("map_spots").select("*").eq("user_id", user_id).execute()
        spots = response.data
        results = []
        for spot in spots:
            db_cat = spot.get('category', '其它')
            if target_category and db_cat != target_category and target_category != "其它": 
                continue
            s_lat = spot.get('latitude')
            s_lng = spot.get('longitude')
            if s_lat and s_lng and (s_lat != 0.0 or s_lng != 0.0):
                dist = math.sqrt((s_lat - lat)**2 + (s_lng - lng)**2)
                spot['dist_score'] = dist
                spot['dist_meters'] = int(dist * 111 * 1000)
                if not spot.get('google_map_url'):
                    spot['google_map_url'] = spot.get('address')
                results.append(spot)
        results.sort(key=lambda x: x['dist_score'])
        return results[:limit]
    except: return []

# --- 5. 產生卡片 ---
def create_radar_flex(spots, center_lat, center_lng, mode="personal", category="美食"):
    # ★ 這裡文字改了：變成「人氣」與「私藏」
    title_text = f"🐾 順順的{category}筆記" if mode == "personal" else f"🔥 人氣{category}"
    
    if not spots:
        return {"type": "text", "text": f"😿 附近找不到{category}耶... (目前模式: {'私藏' if mode=='personal' else '人氣'})"}

    bubbles = []
    for spot in spots:
        is_ad = False
        if mode == "hotspot":
            name = spot['name']
            ad_priority = spot.get('ad_priority', 0)
            if ad_priority > 0:
                is_ad = True; cat = "廣告"; note = "👑 順順嚴選"; name = f"👑 {name}"
            else:
                # ★ 這裡文字改了：顯示人氣
                cat = "人氣"; note = f"🔥 {spot.get('popularity',0)} 人氣"
            map_url = spot.get('google_url') or "http://maps.google.com"
        else:
            name = spot.get('location_name') or spot.get('name', '未命名')
            cat = spot.get('category', '其它')
            dist = spot.get('dist_meters', 0)
            note = f"🐾 距離 {dist} m"
            map_url = spot.get('google_map_url') or spot.get('address') or ""

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
              {"type": "button", "action": {"type": "uri", "label": "👑 立即前往" if is_ad else "🐾 帶我去", "uri": map_url}, "style": "primary", "color": bg_color, "height": "sm"}
            ]
          }
        }
        bubbles.append(bubble)
        if len(bubbles) >= 10: break

    # ★ 這裡按鈕文字也改了：更直覺的切換
    switch_cmd_text = f"人氣 {category} {center_lat},{center_lng}" if mode == "personal" else f"{category} {center_lat},{center_lng}"
    switch_label = "🔥 改看人氣" if mode == "personal" else "🐾 改看私藏"

    switch_bubble = {
        "type": "bubble", "size": "micro",
        "body": {
            "type": "box", "layout": "vertical", "justifyContent": "center", "height": "120px",
            "contents": [
                 {"type": "text", "text": "換個口味？", "align": "center", "weight": "bold"},
                 {"type": "button", "action": {"type": "message", "label": switch_label, "text": switch_cmd_text}, "style": "secondary", "margin": "md"}
            ]
        }
    }
    bubbles.append(switch_bubble)
    return {"type": "flex", "altText": title_text, "contents": {"type": "carousel", "contents": bubbles}}

# --- 6. 主程式入口 ---
def main():
    try:
        msg = sys.argv[1] # 訊息內容
        user_id = sys.argv[2]
        reply_token = sys.argv[3]
    except: return

    print(f"收到訊息: {msg}")

    def ask_location(text):
        return {
            "type": "text", 
            "text": text, 
            "quickReply": {
                "items": [{"type": "action", "action": {"type": "location", "label": "📍 傳送位置"}}]
            }
        }
    
    # 1. 儲存連結
    if "http" in msg:
        saved_name, saved_category = save_map_link(user_id, msg)
        if saved_name:
            reply_line(reply_token, [{"type": "text", "text": f"😺 順順幫你記下來了！\n\n📍 {saved_name}\n\n(已歸類為「{saved_category}」)"}])
        else:
            reply_line(reply_token, [{"type": "text", "text": "😿 儲存失敗了... 再試一次看看？"}])
        return 

    # ★★★ 2. 判斷類別 (更簡單直覺) ★★★
    target_cat = "美食" # 預設找吃的
    if "景點" in msg or "玩" in msg:
        target_cat = "景點"
    elif "住宿" in msg or "住" in msg:
        target_cat = "住宿"
    
    # ★★★ 3. 判斷模式 (熱點 -> 人氣) ★★★
    # 預設是私藏模式 (personal)
    mode = "personal"
    if "人氣" in msg or "熱點" in msg: # 保留熱點以防舊習慣，但主推人氣
        mode = "hotspot"

    # --- 邏輯 A：混合指令 (有座標 + 關鍵字) ---
    if ("," in msg or "，" in msg) and ("人氣" in msg or "熱點" in msg or "帶路" in msg or "景點" in msg or "美食" in msg):
        try:
            clean_msg = re.sub(r'[^\d.,-]', '', msg) 
            lat_str, lng_str = clean_msg.split(',')
            lat = float(lat_str); lng = float(lng_str)
            
            if mode == "hotspot": spots = get_hotspots_rpc(lat, lng, target_cat)
            else: spots = get_nearby_spots(user_id, lat, lng, 10, target_cat)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, target_cat)])
            return
        except: pass

    # --- 邏輯 B：純座標 (通常是按了 + 號傳送位置) ---
    if "," in msg:
        try:
            clean_msg = msg.replace(" ", "")
            lat, lng = map(float, clean_msg.split(','))
            
            # 讀取上次狀態
            state = get_user_state(user_id)
            last_mode = state.get("last_mode", "personal")
            last_cat = state.get("last_category", "美食") 

            if last_mode == "hotspot": spots = get_hotspots_rpc(lat, lng, last_cat)
            else: spots = get_nearby_spots(user_id, lat, lng, 10, last_cat)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, last_mode, last_cat)])
            return
        except: pass

    # --- 邏輯 C：直覺關鍵字指令 ---
    
    # 1. 教學
    if "教學" in msg or "說明" in msg:
        reply_line(reply_token, [{"type": "text", "text": "😺 我是順順！\n\n你可以說：\n🔸「美食」👉 找我存的美食\n🔸「景點」👉 找我存的景點\n🔸「人氣」👉 看大家去哪裡\n\n直接把 Google Maps 網址貼給我，我就會幫你記住喔！"}])
    
    # 2. 人氣 (取代熱點)
    elif "人氣" in msg:
        update_user_state(user_id, "hotspot", target_cat)
        reply_line(reply_token, [ask_location(f"🔥 搜尋人氣{target_cat}模式\n請傳送位置給我！")])

    # 3. 美食 / 景點 / 帶路 (私藏)
    elif "美食" in msg or "景點" in msg or "住宿" in msg or "帶路" in msg:
        update_user_state(user_id, "personal", target_cat)
        reply_line(reply_token, [ask_location(f"🐾 搜尋私藏{target_cat}模式\n請傳送位置給我！")])

if __name__ == "__main__":
    main()
