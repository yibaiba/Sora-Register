#!/usr/bin/env python3
"""
用浏览器登录一次，拿到 OAuth2 的 refresh_token，填到 mail.txt 第 4 列。
用法：项目根目录  python -m protocol.scripts.get_outlook_refresh_token [client_id]
"""
import re
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

LIVE_AUTHORIZE = "https://login.live.com/oauth20_authorize.srf"
LIVE_TOKEN = "https://login.live.com/oauth20_token.srf"
REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
SCOPE = "wl.imap wl.offline_access"


def main():
    client_id = None
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        client_id = sys.argv[1].strip()
    if not client_id:
        try:
            from config import cfg
            client_id = (getattr(cfg.email, "outlook_client_id", None) or "").strip()
        except Exception:
            pass
    if not client_id:
        print("[x] Need client_id. Usage: python -m protocol.scripts.get_outlook_refresh_token <client_id>")
        return

    auth_url = (
        f"{LIVE_AUTHORIZE}?"
        f"client_id={client_id}&"
        f"scope={SCOPE.replace(' ', '%20')}&"
        f"response_type=code&"
        f"redirect_uri={REDIRECT_URI}"
    )
    print("[*] 1. Open this URL in browser and sign in with your Outlook account:")
    print(auth_url)
    print()
    print("[*] 2. After consent, copy the FULL URL from the address bar (it contains code=...) and paste below.")
    try:
        raw = input("Paste redirect URL (or line with code=): ").strip()
    except EOFError:
        print("[x] No input.")
        return
    if not raw:
        print("[x] Empty input.")
        return

    if "code=" not in raw:
        print("[x] Pasted text does not contain 'code='.")
        return
    m = re.search(r"code=([^&\s]+)", raw)
    code = (m.group(1).strip() if m else "").strip()
    if not code:
        print("[x] Could not find 'code=' in the pasted text.")
        return

    from utils import http_session
    from config import HTTP_TIMEOUT

    data = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    try:
        r = http_session.post(LIVE_TOKEN, data=data, timeout=HTTP_TIMEOUT)
        body = r.json() if r.text else {}
        if r.status_code != 200:
            err = body.get("error", "") or body.get("error_description", "") or r.text[:300]
            print(f"[x] Token exchange failed: HTTP {r.status_code} - {err}")
            return
        refresh = body.get("refresh_token")
        if not refresh:
            print(f"[x] No refresh_token in response. Keys: {list(body.keys())}")
            return
        print()
        print("[ok] refresh_token (copy to mail.txt 4th column):")
        print(refresh)
    except Exception as e:
        print(f"[x] Error: {e}")


if __name__ == "__main__":
    main()
