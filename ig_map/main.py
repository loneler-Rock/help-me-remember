import os
import json
import math
import requests
import re
import sys
from supabase import create_client, Client
from bs4 import BeautifulSoup # 引入分析網頁的工具

# --- 1. 初始化設定 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

# 定義漂亮的顏色與圖示
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

# ★★★ 新增：抓取網頁標題的函式 ★★★
def get_url_title(url):
    try:
        # 偽裝成一般瀏覽器 (User-Agent)，不然 Google 會擋
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # 設定 5 秒超時，避免卡太久
        response = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # 抓取 <title> 標籤
            if soup.title and soup.title.string:
                title = soup.title.string
                # 清理標題：把 "- Google 地圖" 這種字眼拿掉
                title = title.replace(" - Google 地圖", "").replace(" - Google Maps", "")
                return title.strip()
            
            # 如果抓不到 title，試試看 OpenGraph 標籤 (og:title)
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                return og_title["content"].strip()

    except Exception as e:
        print(f"⚠️ 抓標題失敗: {e}")
    
    return "新地標 (待整理)" # 真的抓不到才用這個

def save_map_link(user_id, url):
    """
    修正版：先抓標題，再存入
    """
    try:
        # 1. 嘗試抓取標題
        print(f"正在分析網址: {url}")
        fetched_name = get_url_title(url)
        print(f"抓到的店名: {fetched_name}")

        # 2. 存入 Supabase
        data = {
            "user_id": user_id,
            "google_map_url": url,
            "location_name": fetched_name, # 使用抓到的名字
            "category": "其它",
            "latitude": 0.0,
            "longitude": 0.0,
            "created_at": "now()"
        }
        
        supabase.table("map_spots").insert(data).execute()
        return fetched_name # 回傳抓到的名字，讓 LINE 可以回覆
    except Exception as e:
        print(f"❌ 儲存失敗: {e}")
        return None

# --- 4. 搜尋功能 (美食/景點) ---
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
            if s_lat and s_lng:
                dist = math.sqrt((s_lat - lat)**2 + (s_lng - lng)**2)
                spot['dist_score'] = dist
                spot['dist_meters'] = int(dist * 111 * 1000)
                results.append(spot)
        results.sort(key=lambda x: x['dist_score'])
        return results[:limit]
    except: return []

# --- 5. 產生卡片 ---
def create_radar_flex(spots, center_lat, center_lng, mode="personal", category="美食"):
    title_text = f"🐾 順順的{category}筆記" if mode == "personal" else f"🔥 熱門{category}"
    
    if not spots:
        return {"type": "text", "text": f"😿 附近找不到{category}耶... (目前模式: {mode})"}

    bubbles = []
    for spot in spots:
        is_ad = False
        if mode == "hotspot":
            name = spot['name']
            ad_priority = spot.get('ad_priority', 0)
            if ad_priority > 0:
                is_ad = True; cat = "廣告"; note = "👑 順順嚴選"; name = f"👑 {name}"
            else:
                cat = "熱點"; note = f"🔥 {spot.get('popularity',0)} 人氣"
            map_url = spot.get('google_url') or "http://maps.google.com"
        else:
            # 優先使用 location_name
            name = spot.get('location_name') or spot.get('name', '未命名')
            cat = spot.get('category', '其它')
            dist = spot.get('dist_meters', 0)
            note = f"🐾 距離 {dist} m"
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
              {"type": "button", "action": {"type": "uri", "label": "👑 立即前往" if is_ad else "🐾 帶我去", "uri": map_url}, "style": "primary", "color": bg_color, "height": "sm"}
            ]
          }
        }
        bubbles.append(bubble)
        if len(bubbles) >= 10: break

    switch_cmd_text = f"熱點 {category} {center_lat},{center_lng}" if mode == "personal" else f"{category} {center_lat},{center_lng}"
    
    switch_bubble = {
        "type": "bubble", "size": "micro",
        "body": {
            "type": "box", "layout": "vertical", "justifyContent": "center", "height": "120px",
            "contents": [
                 {"type": "text", "text": "換個口味？", "align": "center", "weight": "bold"},
                 {"type": "button", "action": {"type": "message", "label": "🔥 看大家去哪" if mode == "personal" else "🐾 回到私藏", "text": switch_cmd_text}, "style": "secondary", "margin": "md"}
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

    # 定義「要求位置」的訊息
    def ask_location(text):
        return {
            "type": "text", 
            "text": text, 
            "quickReply": {
                "items": [{"type": "action", "action": {"type": "location", "label": "📍 傳送位置"}}]
            }
        }
    
    # ★ 1. 儲存連結
    if "http" in msg:
        print("偵測到網址，執行儲存邏輯...")
        # 這裡接收回傳的店名
        saved_name = save_map_link(user_id, msg)
        
        if saved_name:
            reply_line(reply_token, [{"type": "text", "text": f"😺 順順幫你記下來了！\n\n📍 {saved_name}\n\n(已存入「其它」分類)"}])
        else:
            reply_line(reply_token, [{"type": "text", "text": "😿 哎呀，儲存失敗了... 再試一次看看？"}])
        return 

    # ★ 2. 智慧判斷類別
    target_cat = "美食" 
    if "景點" in msg or "玩" in msg:
        target_cat = "景點"
    elif "住宿" in msg or "住" in msg:
        target_cat = "住宿"
    
    # --- 邏輯 A：混合指令 ---
    if ("," in msg or "，" in msg) and ("熱點" in msg or "帶路" in msg or "景點" in msg or "美食" in msg):
        try:
            clean_msg = re.sub(r'[^\d.,-]', '', msg) 
            lat_str, lng_str = clean_msg.split(',')
            lat = float(lat_str); lng = float(lng_str)

            mode = "hotspot" if "熱點" in msg else "personal"
            
            if mode == "hotspot": spots = get_hotspots_rpc(lat, lng, target_cat)
            else: spots = get_nearby_spots(user_id, lat, lng, 10, target_cat)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, target_cat)])
            return
        except: pass

    # --- 邏輯 B：純座標 ---
    if "," in msg:
        try:
            clean_msg = msg.replace(" ", "")
            lat, lng = map(float, clean_msg.split(','))
            
            state = get_user_state(user_id)
            mode = state.get("last_mode", "personal")
            category = state.get("last_category", "美食") 

            if mode == "hotspot": spots = get_hotspots_rpc(lat, lng, category)
            else: spots = get_nearby_spots(user_id, lat, lng, 10, category)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, category)])
            return
        except: pass

    # --- 邏輯 C：文字指令 ---
    if "說明" in msg or "教學" in msg:
        reply_line(reply_token, [{"type": "text", "text": "😺 我是順順！\n\n你可以說：\n🔸「順順帶路」👉 找私藏美食\n🔸「找景點」👉 找私藏景點\n🔸「貓友熱點」👉 找熱門美食\n\n直接分享 Google Maps 連結給我，我會立刻幫你存！"}])
    
    elif "熱點" in msg:
        update_user_state(user_id, "hotspot", target_cat)
        reply_line(reply_token, [ask_location(f"🔥 搜尋{target_cat}模式\n請傳送位置給我！")])

    elif "帶路" in msg or "景點" in msg or "美食" in msg:
        update_user_state(user_id, "personal", target_cat)
        reply_line(reply_token, [ask_location(f"🐾 搜尋私藏{target_cat}模式\n請傳送位置給我！")])

if __name__ == "__main__":
    main()
