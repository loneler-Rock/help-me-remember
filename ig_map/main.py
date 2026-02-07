import os
import json
import math
import requests
import re
import sys
from supabase import create_client, Client
from bs4 import BeautifulSoup
from urllib.parse import unquote

# --- 1. 初始化設定 ---
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

# ★★★ 強化版：標題抓取邏輯 ★★★
def get_url_title(url):
    try:
        # 1. 偽裝成 Facebook 爬蟲 (因為 Google 對社群爬蟲比較友善，會給正確的 Meta Tag)
        headers = {
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        
        # 取得網頁，設定 redirects=True 讓它追蹤短網址轉址
        response = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        final_url = response.url # 這是展開後的長網址
        
        title_candidate = "新地標 (待整理)"
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 策略 A: 優先抓取 og:title (社群分享標題)
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                t = og_title["content"]
                if "Google Maps" not in t and "Google 地圖" not in t:
                    return t.strip() # 抓到了！直接回傳
                title_candidate = t # 先存著備用
            
            # 策略 B: 抓取 <title>
            if soup.title and soup.title.string:
                t = soup.title.string.replace(" - Google 地圖", "").replace(" - Google Maps", "")
                if "Google Maps" not in t and "Google 地圖" not in t:
                    return t.strip()
            
            # 策略 C: 絕招！從網址 URL 分析
            # Google Maps 長網址通常長這樣:
