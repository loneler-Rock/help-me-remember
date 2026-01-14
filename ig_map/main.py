import os
import re
import requests
import sys
import urllib.parse  # 👈 新增這個工具來翻譯網址亂碼
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
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        # 1. 取得網頁內容
        try:
            response = requests.get(target_url, headers=headers, timeout=10)
            final_url = response.url # 取得最終網址
            html_content = response.text
        except Exception as e:
            print(f"⚠️ 網址連線失敗: {e}")
            return False
        
        # 2. 抓取座標
        coords = re.findall(r'!3d([0-9\.]+)!4d([0-9\.]+)', html_content)
        
        if coords:
            lat, lng = coords[0]
            
            # 3. 抓取店名 (優先策略：從網址抓，因為最準！)
            place_name = "未命名地點"
            
            # 嘗試從網址解碼 (例如 .../place/奕順軒/...)
            if "/place/" in final_url:
                try:
                    start = final_url.find("/place/") + 7
                    end = final_url.find("/@", start)
                    if end != -1:
                        raw_name = final_url[start:end]
                        # 把網址亂碼翻譯回中文
                        decoded_name = urllib.parse.unquote(raw_name).replace("+", " ")
                        place_name = decoded_name
                        print(f"✅ 從網址成功解碼店名: {place_name}")
                except:
                    pass

            # 如果網址沒抓到，才去抓網頁標題
            if place_name == "未命名地點":
                name_match = re.search(r'<meta property="og:title" content="(.*?)">', html_content)
                if name_match:
                    title_text = name_match.group(1).replace(" - Google 地圖", "").replace("Google Maps", "")
                    if title_text.strip(): # 確保不是空白
                        place_name = title_text

            # 如果還是抓到 Google Maps，就標示一下
            if "Google Maps" in place_name or "Google 地圖" in place_name:
                 place_name = "未知地點 (請手動更新)"

            print(f"📍 最終確認地點: {place_name} ({lat}, {lng})")
            
            # 4. 寫入資料庫
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
