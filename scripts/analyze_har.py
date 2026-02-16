#!/usr/bin/env python3
"""
Parse a HAR file and extract API requests for registration and Plus flow.
Usage: python -m protocol.scripts.analyze_har [path/to/file.har]
默认使用 protocol/zcchatgpt.com.har
"""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Tuple

def get_header(req, name: str) -> str:
    for h in req.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""

def get_post_body(req) -> Tuple[str, str]:
    post = req.get("postData") or {}
    return post.get("mimeType", ""), post.get("text", "")

def is_api_interest(url: str, method: str, has_body: bool) -> bool:
    u = url.lower()
    if "auth.openai.com" in u and ("/api/" in u or method != "GET" or has_body):
        return True
    if "chatgpt.com/api/" in u:
        return True
    return False

def path_topic(url: str) -> str:
    if "user/register" in url:
        return "REGISTER_STEP5"
    if "email-otp/send" in url:
        return "OTP_SEND_STEP6"
    if "email-otp/validate" in url:
        return "OTP_VALIDATE_STEP7"
    if "create_account" in url:
        return "CREATE_ACCOUNT_STEP8"
    if "authorize/continue" in url:
        return "AUTHORIZE_STEP4"
    if "signin/openai" in url or "auth/csrf" in url:
        return "AUTH_CSRF_SIGNIN"
    if "auth/callback" in url:
        return "AUTH_CALLBACK"
    if "billing" in url or "payment" in url or "subscription" in url or "plus" in url.lower():
        return "BILLING_PLUS"
    return "API"

def main():
    base = Path(__file__).resolve().parent.parent
    har_path = base / "zcchatgpt.com.har"
    if len(sys.argv) > 1:
        har_path = Path(sys.argv[1])
    if not har_path.exists():
        print(f"File not found: {har_path}")
        sys.exit(1)

    with open(har_path, "r", encoding="utf-8", errors="replace") as f:
        har = json.load(f)

    entries = har.get("log", {}).get("entries", [])
    print(f"Total entries: {len(entries)}\n")
    print("=" * 80)
    print("API requests (auth.openai.com + chatgpt.com/api, by time order)")
    print("=" * 80)

    api_entries = []
    for i, ent in enumerate(entries):
        req = ent.get("request", {})
        url = req.get("url", "")
        method = req.get("method", "GET")
        mime, body = get_post_body(req)
        if not is_api_interest(url, method, bool(body)):
            continue
        topic = path_topic(url)
        api_entries.append((i, topic, method, url, mime, body, ent.get("response", {})))

    for idx, topic, method, url, mime, body, resp in api_entries:
        status = resp.get("status", 0)
        status_ok = "OK" if 200 <= status < 300 else f"HTTP {status}"
        print(f"\n[{topic}] {method} {status_ok}")
        print(f"  URL: {url[:120]}{'...' if len(url) > 120 else ''}")
        if body:
            body_preview = body.strip()
            if mime and "json" in mime and body_preview.startswith("{"):
                try:
                    obj = json.loads(body_preview)
                    def mask(d, depth=0):
                        if depth > 5:
                            return "{...}"
                        if isinstance(d, dict):
                            return {k: ("***" if k.lower() in ("password", "token", "authorization", "cookie") else mask(v, depth + 1)) for k, v in d.items()}
                        if isinstance(d, list) and d and isinstance(d[0], dict) and depth < 3:
                            return [mask(d[0], depth + 1)] + (["..."] if len(d) > 1 else [])
                        return d
                    obj = mask(obj)
                    body_preview = json.dumps(obj, ensure_ascii=False, indent=2)[:2000]
                except Exception:
                    body_preview = body_preview[:500]
            else:
                body_preview = body_preview[:500]
            print(f"  Body ({mime or 'raw'}):")
            for line in body_preview.split("\n"):
                print(f"    {line}")
        print()

    topics = [t for _, t, _, _, _, _, _ in api_entries]
    print("=" * 80)
    print("Registration-related steps found in HAR:")
    for step in ["AUTH_CSRF_SIGNIN", "AUTHORIZE_STEP4", "REGISTER_STEP5", "OTP_SEND_STEP6", "OTP_VALIDATE_STEP7", "CREATE_ACCOUNT_STEP8", "AUTH_CALLBACK"]:
        count = topics.count(step)
        print(f"  {step}: {count}")

    post_urls = set()
    for _, _, method, url, _, body, _ in api_entries:
        if method == "POST" and body:
            parsed = urlparse(url)
            post_urls.add(parsed.netloc + parsed.path)
    print("\nAll POST URLs with body (auth/chatgpt only):")
    for u in sorted(post_urls):
        print(f"  https://{u}")

    relevant_hosts = ("chatgpt.com", "openai.com", "stripe.com", "backend-api")
    print("\n" + "=" * 80)
    print("POST requests with JSON body (chatgpt/openai/stripe/backend only):")
    print("=" * 80)
    count = 0
    for ent in entries:
        req = ent.get("request", {})
        if req.get("method") != "POST":
            continue
        url = req.get("url", "")
        if not any(h in url for h in relevant_hosts):
            continue
        mime, body = get_post_body(req)
        if not body or (not body.strip().startswith("{") and "json" not in (mime or "").lower()):
            continue
        try:
            keys = list(json.loads(body).keys())
        except Exception:
            keys = []
        print(f"\n  {url[:110]}")
        print(f"    Body keys: {keys}")
        count += 1
        if count >= 40:
            print("\n  ... (truncated)")
            break
    print("=" * 80)
    if topics.count("REGISTER_STEP5") == 0 and topics.count("OTP_SEND_STEP6") == 0:
        print("\n[!] This HAR does not contain registration steps.")

if __name__ == "__main__":
    main()
