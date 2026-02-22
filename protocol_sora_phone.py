# -*- coding: utf-8 -*-
"""
Sora 激活与手机号绑定 HTTP 逻辑（对齐 genz27/sora-phone-bind）。
全部使用 curl_cffi 移动端指纹请求 sora.chatgpt.com / auth.openai.com。
供「开始绑定手机」任务调用，参数均显式传入（不依赖 config）。
"""
import re
import random
import string
import uuid
import time

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

import requests

SORA_ORIGIN = "https://sora.chatgpt.com"
AUTH_ORIGIN = "https://auth.openai.com"
# 移动端 client_id / redirect_uri（与 sora-phone-bind 一致，用于 RT 换 AT）
MOBILE_CLIENT_ID = "app_LlGpXReQgckcGGUo2JrYvtJK"
MOBILE_REDIRECT_URI = "com.openai.chat://auth0.openai.com/ios/com.openai.chat/callback"

MOBILE_FINGERPRINTS = ["safari17_2_ios", "safari18_0_ios"]
MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
]

SORA_HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Origin": SORA_ORIGIN,
    "Pragma": "no-cache",
    "Referer": f"{SORA_ORIGIN}/",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "Sec-Ch-Ua-Mobile": "?1",
    "Sec-Ch-Ua-Platform": '"iOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Content-Type": "application/json",
}

DEFAULT_TIMEOUT = 30


def _session_get(url: str, headers: dict, proxy_url: str = None, timeout: int = DEFAULT_TIMEOUT):
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    if CURL_CFFI_AVAILABLE and curl_requests:
        return curl_requests.get(
            url,
            headers=headers,
            proxies=proxies,
            timeout=timeout,
            impersonate=random.choice(MOBILE_FINGERPRINTS),
        )
    return requests.get(url, headers=headers, proxies=proxies, timeout=timeout, verify=False)


def _session_post(url: str, headers: dict, json: dict = None, proxy_url: str = None, timeout: int = DEFAULT_TIMEOUT):
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    if CURL_CFFI_AVAILABLE and curl_requests:
        return curl_requests.post(
            url,
            headers=headers,
            json=json,
            proxies=proxies,
            timeout=timeout,
            impersonate=random.choice(MOBILE_FINGERPRINTS),
        )
    return requests.post(url, headers=headers, json=json or {}, proxies=proxies, timeout=timeout, verify=False)


def _build_headers(access_token: str) -> dict:
    h = dict(SORA_HEADERS_BASE)
    h["Authorization"] = f"Bearer {access_token}"
    h["User-Agent"] = random.choice(MOBILE_USER_AGENTS)
    h["oai-device-id"] = str(uuid.uuid4())
    return h


def rt_to_at_mobile(refresh_token: str, proxy_url: str = None, log_fn=None) -> dict:
    """
    RT 换 AT（移动端 client_id/redirect_uri）。返回 {"access_token": str, "refresh_token": str|None}，失败抛异常或返回空。
    """
    rt = (refresh_token or "").strip()
    if not rt:
        if log_fn:
            try:
                log_fn("[phone_bind] RT 为空")
            except Exception:
                pass
        return {}
    for attempt in range(2):
        try:
            r = _session_post(
                f"{AUTH_ORIGIN}/oauth/token",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={
                    "client_id": MOBILE_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "redirect_uri": MOBILE_REDIRECT_URI,
                    "refresh_token": rt,
                },
                proxy_url=proxy_url,
                timeout=30,
            )
            if r.status_code == 200:
                d = r.json()
                at = (d.get("access_token") or "").strip()
                if at:
                    return {"access_token": at, "refresh_token": d.get("refresh_token")}
            if log_fn and attempt == 0:
                try:
                    log_fn(f"[phone_bind] RT 换 AT HTTP {r.status_code}")
                except Exception:
                    pass
        except Exception as e:
            if log_fn:
                try:
                    log_fn(f"[phone_bind] RT 换 AT 异常: {e}")
                except Exception:
                    pass
            if attempt == 0:
                time.sleep(2)
                continue
    return {}


def sora_bootstrap(access_token: str, proxy_url: str = None, log_fn=None) -> bool:
    """GET backend/m/bootstrap 激活 Sora2。"""
    try:
        r = _session_get(
            f"{SORA_ORIGIN}/backend/m/bootstrap",
            headers=_build_headers(access_token),
            proxy_url=proxy_url,
        )
        if log_fn and r.status_code != 200:
            try:
                log_fn(f"[phone_bind] bootstrap HTTP {r.status_code}")
            except Exception:
                pass
        return r.status_code == 200
    except Exception as e:
        if log_fn:
            try:
                log_fn(f"[phone_bind] bootstrap 异常: {e}")
            except Exception:
                pass
        return False


def sora_me(access_token: str, proxy_url: str = None, log_fn=None) -> dict:
    """GET backend/me 获取当前用户信息。返回 dict，含 username 等；失败返回 {}."""
    try:
        r = _session_get(
            f"{SORA_ORIGIN}/backend/me",
            headers=_build_headers(access_token),
            proxy_url=proxy_url,
        )
        if r.status_code == 200:
            return r.json() if hasattr(r, "json") and callable(r.json) else {}
        return {}
    except Exception as e:
        if log_fn:
            try:
                log_fn(f"[phone_bind] me 异常: {e}")
            except Exception:
                pass
        return {}


def _random_username() -> str:
    return "user_" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))


def sora_username_check(access_token: str, username: str, proxy_url: str = None, log_fn=None) -> bool:
    """POST profile/username/check 检查用户名是否可用。"""
    try:
        r = _session_post(
            f"{SORA_ORIGIN}/backend/project_y/profile/username/check",
            headers=_build_headers(access_token),
            json={"username": username},
            proxy_url=proxy_url,
        )
        if r.status_code == 200:
            d = r.json() if hasattr(r, "json") and callable(r.json) else {}
            return d.get("available", False)
        return False
    except Exception:
        return False


def sora_username_set(access_token: str, username: str, proxy_url: str = None, log_fn=None) -> bool:
    """POST profile/username/set 设置用户名。"""
    try:
        r = _session_post(
            f"{SORA_ORIGIN}/backend/project_y/profile/username/set",
            headers=_build_headers(access_token),
            json={"username": username},
            proxy_url=proxy_url,
        )
        if log_fn and r.status_code != 200:
            try:
                log_fn(f"[phone_bind] username/set HTTP {r.status_code}")
            except Exception:
                pass
        return r.status_code == 200
    except Exception as e:
        if log_fn:
            try:
                log_fn(f"[phone_bind] username/set 异常: {e}")
            except Exception:
                pass
        return False


def sora_ensure_activated(access_token: str, proxy_url: str = None, log_fn=None) -> bool:
    """
    确保 Sora 已激活（有 username）。顺序：bootstrap -> me -> 若无 username 则 check+set。
    返回 True 表示已激活或激活成功。
    """
    if not sora_bootstrap(access_token, proxy_url, log_fn):
        pass  # 不阻断，继续 me
    me = sora_me(access_token, proxy_url, log_fn)
    if me and me.get("username"):
        if log_fn:
            try:
                log_fn(f"[phone_bind] 已激活 username={me.get('username')}")
            except Exception:
                pass
        return True
    for _ in range(5):
        uname = _random_username()
        if sora_username_check(access_token, uname, proxy_url, log_fn):
            if sora_username_set(access_token, uname, proxy_url, log_fn):
                if log_fn:
                    try:
                        log_fn(f"[phone_bind] 设置用户名成功: {uname}")
                    except Exception:
                        pass
                return True
    return False


def sora_phone_enroll_start(access_token: str, phone_number: str, proxy_url: str = None, log_fn=None) -> tuple:
    """
    POST phone_number/enroll/start 发送验证码。
    返回 (True, None) 成功；(False, "phone_used") 该号已被占用；(False, "other") 其他失败。
    """
    try:
        r = _session_post(
            f"{SORA_ORIGIN}/backend/project_y/phone_number/enroll/start",
            headers=_build_headers(access_token),
            json={"phone_number": phone_number, "verification_expiry_window_ms": None},
            proxy_url=proxy_url,
        )
        if r.status_code == 200:
            return True, None
        text = (r.text or "").lower()
        if "already verified" in text or "phone number already" in text:
            return False, "phone_used"
        if log_fn:
            try:
                log_fn(f"[phone_bind] enroll/start HTTP {r.status_code} {r.text[:150]}")
            except Exception:
                pass
        return False, "other"
    except Exception as e:
        if log_fn:
            try:
                log_fn(f"[phone_bind] enroll/start 异常: {e}")
            except Exception:
                pass
        return False, "other"


def sora_phone_enroll_finish(access_token: str, phone_number: str, verification_code: str, proxy_url: str = None, log_fn=None) -> bool:
    """POST phone_number/enroll/finish 提交验证码。"""
    code = re.sub(r"\D", "", (verification_code or "").strip())[:6]
    if not code:
        return False
    try:
        r = _session_post(
            f"{SORA_ORIGIN}/backend/project_y/phone_number/enroll/finish",
            headers=_build_headers(access_token),
            json={"phone_number": phone_number, "verification_code": code},
            proxy_url=proxy_url,
        )
        ok = r.status_code == 200
        if log_fn and not ok:
            try:
                log_fn(f"[phone_bind] enroll/finish HTTP {r.status_code}")
            except Exception:
                pass
        return ok
    except Exception as e:
        if log_fn:
            try:
                log_fn(f"[phone_bind] enroll/finish 异常: {e}")
            except Exception:
                pass
        return False
