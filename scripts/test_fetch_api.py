"""
测试 outlook_fetch_url 接口的原始返回：状态码、响应头、响应体。
用法：项目根目录  python -m protocol.scripts.test_fetch_api
"""
import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import cfg
from utils import http_session, get_user_agent
from email_outlook import load_outlook_accounts

def main():
    accounts = load_outlook_accounts()
    if not accounts:
        print("[x] No accounts in mail.txt")
        return
    fetch_url = (getattr(cfg.email, "outlook_fetch_url", None) or "").strip()
    if not fetch_url:
        print("[x] outlook_fetch_url not set")
        return

    acc = accounts[0]
    email = (acc.get("email") or "").strip()
    body = {
        "email_id": email,
        "email": email,
        "password": (acc.get("password") or "").strip(),
        "uuid": (acc.get("uuid") or "").strip(),
        "token": (acc.get("token") or "").strip(),
    }
    print(f"[*] POST {fetch_url}")
    print(f"[*] account: {email}")
    print(f"[*] body keys: {list(body.keys())} (password/token masked)")
    print()

    try:
        r = http_session.post(
            fetch_url,
            json=body,
            headers={"Content-Type": "application/json", "User-Agent": get_user_agent() or "Mozilla/5.0"},
            timeout=30,
        )
        print(f"[*] status_code: {r.status_code}")
        print(f"[*] headers: {dict(r.headers)}")
        print()
        text = r.text or ""
        print(f"[*] body length: {len(text)}")
        if not text.strip():
            print("[*] body: (empty)")
            return
        if r.headers.get("content-type", "").strip().startswith("application/json"):
            try:
                data = r.json()
                print("[*] body (JSON):")
                print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
            except Exception:
                print(text[:2000])
        else:
            print(text[:2000])
    except Exception as e:
        print(f"[x] Error: {e}")

if __name__ == "__main__":
    main()
