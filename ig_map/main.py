
import os
import re
import requests
import sys
from supabase import create_client

def parse_map_url(target_url, user_id):
    print("==============================")
    print("🚀 系統啟動，開始檢查環境變數...")
    
    # 1. 讀取環境變數 (GitHub Secrets)
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    # 2. 嚴格檢查：如果抓不到，直接印出「人話」錯誤
    if not url:
        print("❌ 錯誤：找不到 SUPABASE_URL！")
        print("💡 請檢查 GitHub Settings -> Secrets，名字是不是打成 SUPABASE_URI 了？要改成 URL (L 結尾)！")
        return False
        
    if not key:
        print("❌ 錯誤：找不到 SUPABASE_SERVICE_ROLE_KEY！")
        print("💡 請檢查 GitHub Secrets 名字有沒有空格？應該要用底線 _ 連接。")
        return False

    print(f"✅ 環境變數讀取成功！URL 長度: {len(url)}")
    
    try:
        # 建立連線
        supabase = create_client(url, key)
        
        print(f"🔍 開始解析網址: {target_url}")
        
        # 策略 A: 還原短網址
        try:
            response = requests.get(target_url, timeout=10)
            final_url = response.url
            html_content = response.text
        except Exception as e:
            print(f"⚠️ 網址連線失敗: {e}")
            return False
        
        # 策略 B: 抓取座標
        coords = re.findall(r'!3d([0-9\.]+)!4d([0-9\.]+)', html_content)
        
        if coords:
            lat, lng = coords[0]
            # 抓取店名
            name_match = re.search(r'<title>(.*?)</title>', html_content)
            place_name = name_match.group(1).replace(" - Google 地圖", "") if name_match else "未命名地點"
            
            print(f"📍 找到地點: {place_name} ({lat}, {lng})")
            
            # 準備寫入資料
            data = {
                "user_id": user_id,
                "name": place_name,
                "latitude": float(lat),
                "longitude": float(lng),
                "original_url": target_url
            }
            
            # 寫入 Supabase
            try:
                supabase.table("ig_food_map").insert(data).execute()
                print(f"🎉 儲存成功！請重新整理地圖網頁。")
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
    # 接收 GitHub Actions 傳進來的參數
    if len(sys.argv) > 2:
        target_url = sys.argv[1]
        user_id = sys.argv[2]
        parse_map_url(target_url, user_id)
    else:
        print("❌ 參數不足：請確認 YAML 檔案有傳送 url 和 user_id")
