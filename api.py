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
    except Exception as e: print(f"❌ 記憶失敗: {e}")

def get_user_state(user_id):
    try:
        response = supabase.table("user_states").select("*").eq("user_id", user_id).execute()
        if response.data: return response.data[0]
    except: pass
    return {"last_mode": "personal", "last_category": "美食"}

# --- 搜尋核心 ---
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
                cat = "熱點"; note = f"🔥 {spot['popularity']} 人氣"
            map_url = spot['google_url'] or "http://google.com"
        else:
            name = spot['location_name']; cat = spot.get('category', '其它')
            dist = spot.get('dist_meters', 0)
            note = f"🐾 距離 {dist} m"
            map_url = spot.get('google_map_url') or spot.get('address')

        color = CATEGORY_COLORS.get(cat, "#7F8C8D")
        bg_color = color if not is_ad else "#F1C40F"
        
        bubble = {
          "type": "bubble", "size": "micro",
          "header": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": "嚴選" if is_ad else cat, "color": "#ffffff", "size": "xs", "weight": "bold"}], "backgroundColor": bg_color, "paddingAll": "sm"},
          "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": name, "weight": "bold", "size": "sm", "wrap": True}, {"type": "text", "text": note, "size": "xs", "color": "#8c8c8c"}]},
          "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "前往", "uri": map_url}, "style": "primary", "color": bg_color, "height": "sm"}]}
        }
        bubbles.append(bubble)
        if len(bubbles) >= 10: break
    
    return {"type": "flex", "altText": title_text, "contents": {"type": "carousel", "contents": bubbles}}

def request_user_location(reply_token, text_hint):
    msg = {"type": "text", "text": f"👇 {text_hint}", "quickReply": {"items": [{"type": "action", "action": {"type": "location", "label": "📍 傳送位置"}}]}}
    reply_line(reply_token, [msg])

# --- 主要入口 ---
@app.route('/', methods=['POST'])
def callback():
    data = request.json
    message_text = data.get("message_text", "")
    user_id = data.get("user_id", "")
    reply_token = data.get("reply_token", "")
    
    if not message_text: return "OK", 200

    print(f"🚀 [指令] {message_text}")

    # ★ 1. 對應舊按鈕：順順教學
    if "教學" in message_text or "說明" in message_text:
        reply_line(reply_token, [{"type": "text", "text": "😺 我是順順！\n按【順順帶路】找你存的店\n按【貓友熱點】看大家去的店\n分享 Google Maps 連結給我可以存檔喔！"}])

    # ★ 2. 對應舊按鈕：順順帶路 (預設找私藏美食)
    elif "順順帶路" in message_text or "帶路" in message_text:
        update_user_state(user_id, "personal", "美食")
        request_user_location(reply_token, "要去哪裡？(私藏模式)")

    # ★ 3. 對應舊按鈕：貓友熱點 (預設找熱門美食)
    elif "貓友熱點" in message_text or "熱點" in message_text:
        update_user_state(user_id, "hotspot", "美食")
        request_user_location(reply_token, "看看大家去哪？(熱點模式)")

    # ★ 4. 接收座標 (會去讀上面的設定)
    elif re.match(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$', message_text):
        try:
            lat, lng = map(float, message_text.split(','))
            state = get_user_state(user_id)
            # 讀取剛剛按鈕設定的模式
            mode = state.get("last_mode", "personal")
            category = state.get("last_category", "美食")
            
            if mode == "hotspot":
                spots = get_hotspots_rpc(lat, lng, target_category=category)
            else:
                spots = get_nearby_spots(user_id, lat, lng, limit=10, target_category=category)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, category)])
        except: pass

    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
