#!/usr/bin/env python3
"""根据邮箱从拉信 API 取 6 位验证码，供浏览器填写。用法: python scripts/get_otp_for_browser.py <email> [password] [uuid] [token]；或从 DB 读该邮箱的 password/uuid/token。"""
import os
import sys
import json
from pathlib import Path

PROTOCOL_ROOT = Path(__file__).resolve().parent.parent
WEB_BACKEND = PROTOCOL_ROOT / "web" / "backend"
if str(WEB_BACKEND) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND))
os.environ.setdefault("DATA_DIR", str(PROTOCOL_ROOT / "data"))

from app.database import init_db, get_db
from app.services.registration_runner import _get_registration_settings, fetch_one_unregistered_email
from app.services.otp_resolver import get_otp_for_email

def main():
    email = (sys.argv[1] or "").strip()
    if not email:
        # 从 DB 取一条未注册邮箱
        init_db()
        with get_db() as conn:
            row = fetch_one_unregistered_email(conn)
        if not row:
            print(json.dumps({"error": "无未注册邮箱，且未传 email 参数"}), flush=True)
            sys.exit(1)
        _, email, password, uuid_val, token = row
        password = (password or "").strip()
        uuid_val = (uuid_val or "").strip()
        token = (token or "").strip()
    else:
        args = sys.argv[2:]
        password = (args[0] or "").strip() if len(args) > 0 else ""
        uuid_val = (args[1] or "").strip() if len(args) > 1 else ""
        token = (args[2] or "").strip() if len(args) > 2 else ""
        if not password and not uuid_val:
            init_db()
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT email, password, uuid, token FROM emails WHERE LOWER(TRIM(email)) = LOWER(TRIM(?)) LIMIT 1", (email,))
                r = c.fetchone()
            if r:
                password = (r[1] or "").strip()
                uuid_val = (r[2] or "").strip()
                token = (r[3] or "").strip()

    settings = _get_registration_settings()
    base = (settings.get("email_api_url") or "https://gapi.hotmail007.com").rstrip("/")
    key = (settings.get("email_api_key") or "").strip()
    account_str = f"{email}:{password or ''}:{token or ''}:{uuid_val or ''}"

    if not key:
        print(json.dumps({"error": "未配置 email_api_key"}), flush=True)
        sys.exit(1)

    otp = get_otp_for_email(base, key, account_str, timeout_sec=100, interval_sec=5)
    if otp:
        print(otp, flush=True)
    else:
        print(json.dumps({"error": "超时未收到验证码"}), flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
