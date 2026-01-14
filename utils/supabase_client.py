import os
from supabase import create_client

def init_supabase():
    """
    初始化 Supabase 客戶端
    從環境變數中讀取 URL 和 KEY
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        # 如果找不到鑰匙，就大聲報錯
        print("❌ 嚴重錯誤：找不到 Supabase 連線資訊！")
        print("💡 請檢查 GitHub Settings -> Secrets 是否有設定 SUPABASE_URL 和 SUPABASE_SERVICE_ROLE_KEY")
        raise ValueError("環境變數缺失")

    return create_client(url, key)
