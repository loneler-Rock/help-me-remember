import os
import re
import requests
import sys
from supabase import create_client

def parse_map_url(target_url, user_id):
    print("==============================")
    print("🚀 系統啟動...")
    
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        print("❌ 錯誤：環境變數缺失，請檢查 Secrets 設定。")
        return False

    try:
        supabase = create_client(url, key)
        print(f"🔍 開始解析網址: {target_url}")
        
        # 偽裝成瀏覽器，避免被 Google 擋
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        # 策略 A: 還原短網址
        try:
            response = requests.get(target_url, headers=headers, timeout=10)
            final_url = response.url
            html_content = response.text
        except Exception as e:
            print(f"⚠️ 網址連線失敗: {e}")
            return False
        
        # 策略 B: 抓取座標
        coords = re.findall(r'!3d([0-9\.]+)!4d([0-9\.]+)', html_content)
        
        if coords:
            lat, lng = coords[0]
            
            # ★★★ 優化：改用 meta og:title 抓取準確店名 ★★★
            name_match = re.search(r'<meta property="og:title" content="(.*?)">', html_content)
            
            # 如果 meta 抓不到，才退回去抓 title
            if name_match:
                place_name = name_match.group(1).replace(" - Google 地圖", "")
            else:
                title_match = re.search(r'<title>(.*?)</title>', html_content)
                place_name = title_match.group(1).replace(" - Google 地圖", "") if title_match else "未命名地點"
            
            # 再次過濾：如果名字還是 "Google Maps"，嘗試從網址解碼
            if place_name == "Google Maps" or place_name == "Google 地圖":
                 print("⚠️ 標題抓取過於籠統，嘗試使用備案...")
                 # 這裡可以放過，或者暫時標記，不影響功能
            
            print(f"📍 找到地點: {place_name} ({lat}, {lng})")
            
            # 準備寫入資料
            data = {
                "user_id": user_id,
                "name": place_name,
                "latitude": float(lat),
                "longitude": float(lng),
                "original_url": target_url
            }
            
            try:
                supabase.table("ig_food_map").insert(data).execute()
                print(f"🎉 儲存成功！資料庫已更新。")
                return True
            except Exception as db_err:
                print(f"💥 資料庫寫入失敗: {db_err}")
                return False
        else:
            print("❌ 找不到座標，可能網址格式不支援。")
            return False
            
    except Exception as e:
        print(f"💥 程式發生未預期錯誤: {str(e)}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 2:
        parse_map_url(sys.argv[1], sys.argv[2])
    else:
        print("❌ 參數不足")
