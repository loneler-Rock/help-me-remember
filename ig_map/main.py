import os
import json
import math
import requests
import re
import sys
from supabase import create_client, Client

# --- 1. 初始化設定 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

# 定義漂亮的顏色與圖示 (把這些妝容加回來)
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
            if target_category and spot.get('category', '其它') != target_category: continue
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

# --- 4. 產生漂亮卡片 (這段修復了！) ---
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
            name = spot['location_name']
            cat = spot.get('category', '其它')
            dist = spot.get('dist_meters', 0)
            note = f"🐾 距離 {dist} m"
            map_url = spot.get('google_map_url') or spot.get('address')

        # 決定顏色
        color = CATEGORY_COLORS.get(cat, "#7F8C8D")
        icon = CATEGORY_ICONS.get(cat, CATEGORY_ICONS["其它"])
        bg_color = color if not is_ad else "#F1C40F" 

        # 這裡恢復了 Header 和漂亮的排版
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

    # 最後加一張「切換模式」的卡片
    switch_bubble = {
        "type": "bubble", "size": "micro",
        "body": {
            "type": "box", "layout": "vertical", "justifyContent": "center", "height": "120px",
            "contents": [
                 {"type": "text", "text": "換個口味？", "align": "center", "weight": "bold"},
                 {"type": "button", "action": {"type": "message", "label": "🔥 看熱點" if mode == "personal" else "🐾 看私藏", "text": f"熱點 {category} {center_lat},{center_lng}" if mode == "personal" else f"{center_lat},{center_lng}"}, "style": "secondary", "margin": "md"}
            ]
        }
    }
    bubbles.append(switch_bubble)
    return {"type": "flex", "altText": title_text, "contents": {"type": "carousel", "contents": bubbles}}

# --- 5. 主程式邏輯 ---
def main():
    try:
        msg = sys.argv[1] # 訊息內容
        user_id = sys.argv[2]
        reply_token = sys.argv[3]
    except: return

    print(f"收到訊息: {msg}")

    # 定義「要求位置」的訊息 (包含按鈕 Quick Reply)
    def ask_location(text):
        return {
            "type": "text", 
            "text": text, 
            "quickReply": {
                "items": [{"type": "action", "action": {"type": "location", "label": "📍 傳送位置"}}]
            }
        }

    # ★ 優先判斷：混合指令 (熱點 + 座標) -> 修正順序的關鍵
    if ("熱點" in msg or "帶路" in msg) and ("," in msg or "，" in msg):
        try:
            clean_msg = re.sub(r'[^\d.,-]', '', msg) 
            lat_str, lng_str = clean_msg.split(',')
            lat = float(lat_str); lng = float(lng_str)

            mode = "hotspot" if "熱點" in msg else "personal"
            cat = "美食" # 預設
            if "景點" in msg: cat = "景點"
            if "住宿" in msg: cat = "住宿"

            if mode == "hotspot": spots = get_hotspots_rpc(lat, lng, cat)
            else: spots = get_nearby_spots(user_id, lat, lng, 10, cat)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, cat)])
            return
        except: pass

    # ★ 判斷：純座標 (從按鈕傳來的)
    if "," in msg:
        try:
            clean_msg = msg.replace(" ", "")
            lat, lng = map(float, clean_msg.split(','))
            
            state = get_user_state(user_id)
            mode = state.get("last_mode", "personal")
            cat = state.get("last_category", "美食")

            if mode == "hotspot": spots = get_hotspots_rpc(lat, lng, cat)
            else: spots = get_nearby_spots(user_id, lat, lng, 10, cat)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, cat)])
            return
        except: pass

    # ★ 判斷：文字指令 (加上了按鈕！)
    if "說明" in msg or "教學" in msg:
        reply_line(reply_token, [{"type": "text", "text": "😺 我是順順！\n傳送 Google Maps 連結給我，我幫你存！\n想找店請按選單按鈕～"}])
    
    elif "熱點" in msg:
        update_user_state(user_id, "hotspot", "美食")
        reply_line(reply_token, [ask_location("🔥 搜尋熱點模式\n請傳送位置給我！")])

    elif "帶路" in msg:
        update_user_state(user_id, "personal", "美食")
        reply_line(reply_token, [ask_location("🐾 搜尋私藏模式\n請傳送位置給我！")])

if __name__ == "__main__":
    main()
