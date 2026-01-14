import os
import sys
import time
import re
import requests
import urllib.parse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# 設定路徑以引用 utils
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utils.supabase_client import init_supabase

# ===========================
# 系統設定
# ===========================
# 請填入你的 Make.com Webhook (用於降價通知)
MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/iqfx87wola6yp35c3ly7mqvugycxwlfx"
ICHANNELS_ID = "af000148084" # 通路王 ID

# ===========================
# 爬蟲核心 (共用)
# ===========================
def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument('--headless') 
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    # 偽裝成一般瀏覽器
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

def parse_price(driver, url):
    """
    通用解析器，支援 Momo 和 PChome
    回傳: (商品名稱, 價格)
    """
    print(f"🔍 正在解析: {url}...")
    driver.get(url)
    time.sleep(3) # 等待網頁載入
    
    title = "未命名商品"
    price = 99999999
    
    try:
        title = driver.title.split("-")[0].strip()
        
        # 嘗試解析 Momo
        if "momoshop" in url:
            try:
                price_text = driver.find_element("css selector", ".prdPrice").text
            except:
                try:
                    price_text = driver.find_element("css selector", "#pKwdPrice").text
                except:
                    price_text = "0"
        
        # 嘗試解析 PChome
        elif "pchome" in url:
            try:
                price_text = driver.find_element("css selector", ".o-prodPrice__price").text
            except:
                try:
                    price_text = driver.find_element("css selector", "#PriceTotal").text
                except:
                    price_text = "0"
        else:
            print("⚠️ 非 Momo/PChome 網址，跳過")
            return None, None

        # 清理價格字串 (去掉 $ 和逗號)
        price = int(re.sub(r"[^\d]", "", price_text))
        return title, price

    except Exception as e:
        print(f"❌ 解析失敗: {e}")
        return title, price

# ===========================
# 功能 A: 新增商品 (LINE 觸發)
# ===========================
def add_new_product(url, user_id):
    print("🚀 啟動新增模式...")
    driver = setup_driver()
    supabase = init_supabase()
    
    try:
        title, price = parse_price(driver, url)
        
        if price and price < 99999999:
            print(f"✅ 抓取成功！\n商品: {title}\n價格: {price}")
            
            # 準備寫入資料
            data = {
                "user_id": user_id,
                "product_name": title,
                "original_url": url,
                "current_price": price,
                "lowest_price": price, # 剛加入時，現價就是最低價
                "target_price": 0,     # 預設不設目標價
                "is_active": True
            }
            
            # 寫入 products 表格
            result = supabase.table("products").insert(data).execute()
            
            # 順便寫入一筆歷史價格
            if result.data:
                product_id = result.data[0]['id']
                supabase.table("price_history").insert({
                    "product_id": product_id,
                    "price": price
                }).execute()
                print("🎉 商品已加入追蹤清單！")
        else:
            print("❌ 無法抓取價格，請確認網址是否正確。")
            
    except Exception as e:
        print(f"💥 新增失敗: {e}")
    finally:
        driver.quit()

# ===========================
# 功能 B: 每日檢查 (排程觸發)
# ===========================
def run_daily_check():
    print("🚀 啟動每日比價檢查...")
    driver = setup_driver()
    supabase = init_supabase()
    
    try:
        # 撈出所有啟用的商品
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        print(f"📋 共發現 {len(products)} 個監控商品")

        for p in products:
            try:
                title, current_price = parse_price(driver, p['original_url'])
                
                if current_price == 99999999:
                    print(f"⚠️ {p['product_name']} 解析失敗，跳過")
                    continue

                # 寫入歷史價格
                supabase.table("price_history").insert({
                    "product_id": p['id'],
                    "price": current_price
                }).execute()
                
                # 檢查是否創新低
                last_lowest = p.get('lowest_price') or 99999999
                is_lowest = False
                
                if current_price < last_lowest:
                    is_lowest = True
                    # 更新最低價紀錄
                    supabase.table("products").update({
                        "lowest_price": current_price,
                        "current_price": current_price,
                        "product_name": title # 順便更新標題
                    }).eq("id", p['id']).execute()
                else:
                    # 只更新現價
                    supabase.table("products").update({
                        "current_price": current_price
                    }).eq("id", p['id']).execute()

                # 發送通知邏輯
                target_price = p.get('target_price') or 0
                last_price = p.get('current_price') # 這裡其實是舊的價格，但在上面已經被我們更新了，所以邏輯上要小心
                # 簡化邏輯：只要創新低，或者低於目標價，就通知
                
                if is_lowest or (target_price > 0 and current_price <= target_price):
                    print(f"🔥 發現好價！發送通知...")
                    send_notification(title, current_price, p['original_url'], p['user_id'], is_lowest)
                
                time.sleep(2) # 禮貌性暫停

            except Exception as inner_e:
                print(f"處理商品 {p.get('id')} 錯誤: {inner_e}")

    except Exception as e:
        print(f"排程執行錯誤: {e}")
    finally:
        driver.quit()

def send_notification(product_name, price, url, user_id, is_lowest_price):
    # 簡單的分潤連結轉換
    affiliate_url = url
    if "momoshop" in url:
        encoded_url = urllib.parse.quote(url)
        affiliate_url = f"http://www.ichannels.com.tw/bbs.php?member={ICHANNELS_ID}&url={encoded_url}"

    status = "🔥 歷史新低！" if is_lowest_price else "📉 降價通知"
    message = f"{status}\n商品：{product_name}\n金額：${price:,}\n------------------\n點此購買：\n{affiliate_url}"
    
    try:
        requests.post(MAKE_WEBHOOK_URL, json={"message": message, "to": user_id})
    except Exception as e:
        print(f"Webhook 失敗: {e}")

# ===========================
# 主程式入口
# ===========================
if __name__ == "__main__":
    # 判斷是「新增模式」還是「每日檢查模式」
    if len(sys.argv) > 2:
        # 有參數傳入 -> 新增模式 (Make.com 呼叫)
        target_url = sys.argv[1]
        user_id = sys.argv[2]
        add_new_product(target_url, user_id)
    else:
        # 沒參數 -> 每日檢查模式 (GitHub Schedule 呼叫)
        run_daily_check()
