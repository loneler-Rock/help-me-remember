import os
import re
import requests
from supabase import create_client

# 1. 從環境變數讀取金鑰 (等等要去 GitHub 設定)
url = os.environ.get("https://eovkimfqgoggxbkvkjxg.supabase.co")
key = os.environ.get("=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVvdmtpbWZxZ29nZ3hia3ZranhnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njc3NjI1NzksImV4cCI6MjA4MzMzODU3OX0.akX_HaZQwRh53KJ-ULuc5Syf2ypjhaYOg7DfWhYs8EY") # 注意：存資料要用 Service Role Key
supabase = create_client(url, key)

def parse_map_url(target_url, user_id):
    print(f"🚀 開始處理: {target_url}")
    
    try:
        # 策略 A: 還原短網址並抓取 HTML
        response = requests.get(target_url, timeout=10)
        final_url = response.url
        html_content = response.text
        
        # 策略 B: 用 Regex 暴力搜尋座標 (V7.0 核心)
        # 搜尋格式如: !3d25.0339!4d121.5644
        coords = re.findall(r'!3d([0-9\.]+)!4d([0-9\.]+)', html_content)
        
        if coords:
            lat, lng = coords[0]
            name = re.search(r'<title>(.*?)</title>', html_content)
            place_name = name.group(1).replace(" - Google 地圖", "") if name else "未命名地點"
            
            # 存入 Supabase
            data = {
                "user_id": user_id,
                "name": place_name,
                "latitude": float(lat),
                "longitude": float(lng),
                "original_url": target_url
            }
            
            res = supabase.table("ig_food_map").insert(data).execute()
            print(f"✅ 儲存成功: {place_name}")
            return True
        else:
            print("❌ 找不到座標，可能需要更高級的解析策略")
            return False
            
    except Exception as e:
        print(f"💥 發生錯誤: {str(e)}")
        return False

# 執行區 (GitHub Actions 會傳入參數)
if __name__ == "__main__":
    import sys
    # 這裡假設 Make.com 傳過來的是 URL 和 UserID
    # 測試用：python main.py "網址" "UID"
    if len(sys.argv) > 2:
        parse_map_url(sys.argv[1], sys.argv[2])
