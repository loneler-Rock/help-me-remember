import os
import sys
import re
import base64
from supabase import create_client, Client

# --- 初始化 ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

try:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ 警告: 未偵測到 Supabase 環境變數")
        supabase = None
    else:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase 初始化失敗: {e}")
    sys.exit(1)

# --- 工具函式 ---
def decode_base64_safe(data):
    if not data: return ""
    try:
        return base64.b64decode(data).decode('utf-8')
    except:
        return data

def extract_url_from_text(decoded_text):
    if not decoded_text: return None
    match = re.search(r'(https?://[^\s]+)', decoded_text)
    if match: return match.group(1)
    return decoded_text

def save_map_spot(user_id, raw_input):
    decoded_text = decode_base64_safe(raw_input)
    url = extract_url_from_text(decoded_text)
    
    print(f"📍 收到地圖任務，網址: {url}")
    
    # 嘗試抓取地點名稱 (取第一行)
    location_name = "未命名地點"
    if decoded_text:
        lines = decoded_text.split('\n')
        for line in lines:
            line = line.strip()
            if line and "http" not in line:
                location_name = line
                break
    
    print(f"📝 地點名稱: {location_name}")

    if not supabase: return

    try:
        data = {
            "user_id": user_id,
            "location_name": location_name,
            "google_map_url": url,
            "note": decoded_text,
            "created_at": "now()"
        }
        supabase.table("map_spots").insert(data).execute()
        print("✅ 地點收藏成功！")
    except Exception as e:
        print(f"❌ 地點存檔失敗: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        raw_msg = sys.argv[1]
        uid = sys.argv[2]
        save_map_spot(uid, raw_msg)
    else:
        print("❌ 錯誤：未提供參數")
