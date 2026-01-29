import os
import json
import math
import requests
import re
from flask import Flask, request, jsonify
from supabase import create_client, Client

app = Flask(__name__)

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
    if not SUPABASE_URL or not SUPABASE_KEY: 
        print("⚠️ 缺少 Supabase 設定")
    else:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase Init Error: {e}")

def reply_line(token, messages):
    if not token: return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})

# --- 狀態管理 ---
def update_user_state(user_id, mode, category):
    try:
        data = {"user_id": user_id, "last_mode": mode, "last_category": category, "updated_at": "now()"}
        supabase.table("user_states").upsert(data).execute()
        print(f"🧠 [記憶] {user_id}: {mode}/{category}")
    except Exception as e: print(f"❌ 記憶失敗: {e}")

def get_user_state(user_id):
    try:
        response = supabase.table("user_states").select("*").eq("user_id", user_id).execute()
        if response.data: return response.data[0]
    except: pass
    return {"last_mode": "personal", "last_category": "美食"}

# --- 核心搜尋邏輯 ---
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
    except: return []

def get_hotspots_rpc(lat, lng, target_category=None):
    try:
        params = {"user_lat": lat, "user_lng": lng}
        if target_category: params["target_category"] = target_category
        response = supabase.rpc("get_hotspots", params).execute()
        return response.data
    except: return []

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
    help_text = "😺 **順順地圖 V6.0** 😺\n\n👇 **【私藏系列】**\n找你自己存過的美食、景點、住宿。\n\n👇 **【熱門系列】**\n看看大家都在哪裡排隊！\n\n👇 **【怎麼存檔？】**\n分享 Google Maps 連結給我即可！(這會稍微慢一點喔🐾)"
    reply_line(reply_token, [{"type": "text", "text": help_text}])

def request_user_location(reply_token, text_hint):
    msg = {"type": "text", "text": f"👇 {text_hint}", "quickReply": {"items": [{"type": "action", "action": {"type": "location", "label": "📍 傳送位置"}}]}}
    reply_line(reply_token, [msg])

# --- Flask Web Server 入口 ---
@app.route('/', methods=['POST'])
def callback():
    data = request.json
    # 接收 Make 傳來的資料
    message_text = data.get("message_text", "")
    user_id = data.get("user_id", "")
    reply_token = data.get("reply_token", "")
    
    if not message_text: return "OK", 200

    print(f"🚀 [快腦] 收到指令: {message_text}")

    # 1. 說明書
    if "教學" in message_text or "說明" in message_text or "help" in message_text.lower():
        handle_help_message(reply_token)

    # 2. 設定狀態指令
    elif message_text == "找美食":
        update_user_state(user_id, "personal", "美食")
        request_user_location(reply_token, "想吃什麼？傳送位置給順順！")
    elif message_text == "找景點":
        update_user_state(user_id, "personal", "景點")
        request_user_location(reply_token, "想去哪玩？傳送位置給順順！")
    elif message_text == "找住宿":
        update_user_state(user_id, "personal", "住宿")
        request_user_location(reply_token, "今晚住哪？傳送位置給順順！")
    elif "熱點" in message_text and "美食" in message_text:
        update_user_state(user_id, "hotspot", "美食")
        request_user_location(reply_token, "搜尋熱門美食中... 請傳送位置！")
    elif "熱點" in message_text and "景點" in message_text:
        update_user_state(user_id, "hotspot", "景點")
        request_user_location(reply_token, "搜尋熱門景點中... 請傳送位置！")

    # 3. 執行雷達 (熱點帶參數)
    elif message_text.startswith("熱點 "):
        parts = message_text.split(" ")
        if len(parts) >= 3 and "," in parts[-1]:
            cat = parts[1]
            coords = parts[2]
            try:
                lat_str, lng_str = coords.split(',')
                lat = float(lat_str)
                lng = float(lng_str)
                spots = get_hotspots_rpc(lat, lng, target_category=cat)
                flex_msg = create_radar_flex(spots, lat, lng, mode="hotspot", category=cat)
                reply_line(reply_token, [flex_msg])
            except: pass

    # 4. 執行雷達 (純座標)
    elif re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', message_text):
        try:
            lat_str, lng_str = message_text.split(',')
            lat = float(lat_str)
            lng = float(lng_str)
            
            state = get_user_state(user_id)
            mode = state.get("last_mode", "personal")
            category = state.get("last_category", "美食")
            
            if mode == "hotspot":
                spots = get_hotspots_rpc(lat, lng, target_category=category)
                flex_msg = create_radar_flex(spots, lat, lng, mode="hotspot", category=category)
            else:
                spots = get_nearby_spots(user_id, lat, lng, limit=10, target_category=category)
                flex_msg = create_radar_flex(spots, lat, lng, mode="personal", category=category)
            
            reply_line(reply_token, [flex_msg])
        except: pass

    # 5. 其他關鍵字
    elif any(k in message_text for k in ["雷達", "位置", "順順", "帶路"]):
        request_user_location(reply_token, "告訴順順你在哪裡？")

    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
