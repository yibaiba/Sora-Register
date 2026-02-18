#!/usr/bin/env python3
"""从 DB 取一条未注册邮箱，供浏览器/Playwright 全流程测试。输出 JSON 一行：email, password, uuid, token。"""
import os
import sys
import json
from pathlib import Path

PROTOCOL_ROOT = Path(__file__).resolve().parent.parent
WEB_BACKEND = PROTOCOL_ROOT / "web" / "backend"
if str(WEB_BACKEND) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND))
os.environ.setdefault("DATA_DIR", str(PROTOCOL_ROOT / "data"))
Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True)

from app.database import init_db, get_db
from app.services.registration_runner import fetch_one_unregistered_email

def main():
    init_db()
    with get_db() as conn:
        row = fetch_one_unregistered_email(conn)
    if not row:
        print(json.dumps({"error": "无未注册邮箱"}), flush=True)
        sys.exit(1)
    email_id, email, password, uuid_val, token = row
    out = {
        "email_id": email_id,
        "email": email or "",
        "password": (password or "").strip() or None,
        "uuid": (uuid_val or "").strip() or None,
        "token": (token or "").strip() or None,
    }
    print(json.dumps(out, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
