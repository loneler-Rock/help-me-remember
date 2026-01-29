import os
import json
import math
import requests
import re
from supabase import create_client, Client

# --- 接收 Make (GitHub Actions) 傳來的參數 ---
# 因為是 V5.3 架構，所以是用環境變數 or 參數接收
# 這裡我們為了相容性，直接讀取系統參數
import sys

# 初始化 (保持不變)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    print("⚠️ Supabase 設定有誤")

def reply_line(token, messages):
    if not token: return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json={"replyToken": token, "messages": messages})

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

def create_radar_flex(spots, center_lat, center_lng, mode="personal", category="美食"):
    title = f"🔥 熱門{category}" if mode == "hotspot" else f"🐾 私藏{category}"
    if not spots:
        return {"type": "text", "text": f"😿 附近沒有{category}資料耶 ({mode})"}
    
    bubbles = []
    for spot in spots:
        is_ad = False
        if mode == "hotspot":
            name = spot['name']
            if spot.get('ad_priority', 0) > 0:
                is_ad = True; name = f"👑 {name}"
            map_url = spot.get('google_url') or "http://maps.google.com"
            note = f"🔥 {spot.get('popularity',0)} 人氣"
        else:
            name = spot['location_name']
            map_url = spot.get('google_map_url') or spot.get('address')
            note = f"🐾 {spot.get('dist_meters',0)} m"

        bubble = {
            "type": "bubble", "size": "micro",
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": name, "weight": "bold", "wrap": True, "color": "#E67E22" if is_ad else "#000000"},
                    {"type": "text", "text": note, "size": "xs", "color": "#aaaaaa"}
                ]
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "button", "action": {"type": "uri", "label": "前往", "uri": map_url}, "style": "primary", "height": "sm"}]
            }
        }
        bubbles.append(bubble)
    
    return {"type": "flex", "altText": title, "contents": {"type": "carousel", "contents": bubbles}}

# --- 主程式邏輯 (修正順序版) ---
def main():
    # 取得參數 (從 GitHub Actions 傳入)
    try:
        msg = sys.argv[1] # 訊息內容
        user_id = sys.argv[2]
        reply_token = sys.argv[3]
    except:
        return # 沒參數就不跑

    print(f"收到訊息: {msg}")

    # ★ 關鍵修正 1：優先判斷「混合指令」 (熱點 + 座標)
    # 如果訊息裡同時有 "熱點" 和 "逗號(座標)"，直接當作要搜尋，不要問位置
    if ("熱點" in msg or "帶路" in msg) and ("," in msg or "，" in msg):
        try:
            # 嘗試抓出座標
            # 簡單處理：把非數字與逗號的字都拿掉
            clean_msg = re.sub(r'[^\d.,-]', '', msg) 
            # 這裡假設清理後剩下 "24.123,121.123"
            lat_str, lng_str = clean_msg.split(',')
            lat = float(lat_str)
            lng = float(lng_str)

            # 判斷模式
            mode = "hotspot" if "熱點" in msg else "personal"
            cat = "美食" # 預設美食，若要更聰明可以再解析文字
            if "景點" in msg: cat = "景點"
            if "住宿" in msg: cat = "住宿"

            # 執行搜尋
            if mode == "hotspot":
                spots = get_hotspots_rpc(lat, lng, cat)
            else:
                spots = get_nearby_spots(user_id, lat, lng, 10, cat)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, cat)])
            return # 執行完畢，直接結束！
        except:
            # 如果解析失敗，就往下繼續跑，改用問的
            pass

    # ★ 關鍵修正 2：單純的座標 (透過 Location 按鈕傳送的)
    if "," in msg:
        try:
            # 防呆：去除空白
            clean_msg = msg.replace(" ", "")
            lat, lng = map(float, clean_msg.split(','))
            
            # 讀取記憶中的模式
            state = get_user_state(user_id)
            mode = state.get("last_mode", "personal")
            cat = state.get("last_category", "美食")

            if mode == "hotspot":
                spots = get_hotspots_rpc(lat, lng, cat)
            else:
                spots = get_nearby_spots(user_id, lat, lng, 10, cat)
            
            reply_line(reply_token, [create_radar_flex(spots, lat, lng, mode, cat)])
            return
        except: pass

    # ★ 最後才判斷：單純的文字指令 (問使用者在哪)
    if "說明" in msg or "教學" in msg:
        reply_line(reply_token, [{"type": "text", "text": "😺 我是順順！\n傳送 Google Maps 連結給我，我幫你存！\n想找店請按選單按鈕～"}])
    
    elif "熱點" in msg:
        # 記錄它是要找熱點，然後問位置
        update_user_state(user_id, "hotspot", "美食") # 預設美食
        reply_line(reply_token, [{"type": "text", "text": "🔥 搜尋熱點模式\n請傳送位置給我 (按 + 號 -> 位置資訊)"}])

    elif "帶路" in msg:
        update_user_state(user_id, "personal", "美食")
        reply_line(reply_token, [{"type": "text", "text": "🐾 搜尋私藏模式\n請傳送位置給我 (按 + 號 -> 位置資訊)"}])

if __name__ == "__main__":
    main()
