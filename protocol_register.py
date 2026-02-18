"""
协议版 ChatGPT 注册（本仓库只做协议）
入口：register_one_protocol(email, password, jwt_token, get_otp_fn, user_info, **kwargs)。
流程：visit_homepage -> get_csrf -> signin -> authorize -> [GET create-account/password] -> register -> send_otp -> validate_otp -> create_account -> callback。
与参考 chatgpt_register.py 对齐：Chrome 指纹(sec-ch-ua*)、oai-did cookie、traceparent/datadog 头、
signin(login_or_signup+login_hint)、authorize 不追加参数、register/send_otp/validate_otp/create_account 的 URL 与头。
"""

import os
import random
import re
import time
import uuid
from urllib.parse import urlparse, parse_qs
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    cfg,
    HTTP_TIMEOUT,
    get_proxy_url_for_session,
)
from utils import get_user_agent

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

CHATGPT_ORIGIN = "https://chatgpt.com"
AUTH_ORIGIN = "https://auth.openai.com"

# 密码规则：OpenAI 要求最少 12 位
PASSWORD_MIN_LENGTH = 12


class RetryException(Exception):
    """需换 IP/会话重试时抛出；主循环捕获后重新开始。"""
    pass


class RegistrationCancelled(Exception):
    """用户请求停止注册时抛出。"""
    pass


# Chrome 指纹：与参考 chatgpt_register.py 对齐，impersonate 与 sec-ch-ua 匹配
_CHROME_PROFILES = [
    {"major": 131, "impersonate": "chrome131", "build": 6778, "patch_range": (69, 205),
     "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'},
    {"major": 136, "impersonate": "chrome136", "build": 7103, "patch_range": (48, 175),
     "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"'},
    {"major": 124, "impersonate": "chrome124", "build": 6367, "patch_range": (50, 120),
     "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not_A Brand";v="24"'},
]


def _random_chrome_version():
    profile = random.choice(_CHROME_PROFILES)
    major, build = profile["major"], profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], full_ver, ua, profile["sec_ch_ua"]


def _reorder_headers_chrome(session):
    order = ["Accept", "Accept-Language", "Accept-Encoding", "User-Agent", "Referer", "Origin", "Content-Type", "Authorization"]
    h = dict(session.headers)
    session.headers.clear()
    for k in order:
        if k in h:
            session.headers[k] = h.pop(k)
    for k, v in h.items():
        session.headers[k] = v


def _make_trace_headers():
    """与参考 chatgpt_register.py 一致：traceparent + datadog 头。"""
    trace_id = random.randint(10**17, 10**18 - 1)
    parent_id = random.randint(10**17, 10**18 - 1)
    tp = f"00-{uuid.uuid4().hex}-{format(parent_id, '016x')}-01"
    return {
        "traceparent": tp, "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum", "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(trace_id), "x-datadog-parent-id": str(parent_id),
    }


def _make_session(device_id: str = None):
    """创建 Session：与参考对齐。device_id 与 signin 的 ext-oai-did 一致，并写入 oai-did cookie。"""
    proxy = get_proxy_url_for_session()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    if device_id is None:
        device_id = str(uuid.uuid4())

    if CURL_CFFI_AVAILABLE:
        impersonate, full_ver, ua, sec_ch_ua = _random_chrome_version()
        print(f"[*] Using curl_cffi impersonate={impersonate}", flush=True)
        session = curl_requests.Session(impersonate=impersonate)
        if proxies:
            session.proxies = proxies
        session.headers.update({
            "User-Agent": ua,
            "Accept-Language": random.choice([
                "en-US,en;q=0.9", "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9", "en-US,en;q=0.8",
            ]),
            "sec-ch-ua": sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": f'"{full_ver}"',
            "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
            "Accept": "application/json, text/plain, */*",
            "Referer": CHATGPT_ORIGIN + "/",
        })
        try:
            session.cookies.set("oai-did", device_id, domain="chatgpt.com")
        except Exception:
            pass
        _reorder_headers_chrome(session)
        return session

    session = requests.Session()
    retry = Retry(
        total=getattr(cfg.retry, "http_max_retries", 5),
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "OPTIONS"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    ua = get_user_agent() or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    session.headers.update({
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": CHATGPT_ORIGIN + "/",
        "Origin": CHATGPT_ORIGIN,
    })
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


# -------------------- 注册流程步骤（与参考一致） --------------------

def _visit_homepage(session):
    """与参考一致：GET chatgpt.com/"""
    url = f"{CHATGPT_ORIGIN}/"
    session.get(url, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }, timeout=HTTP_TIMEOUT, allow_redirects=True)


def _get_csrf(session) -> str:
    url = f"{CHATGPT_ORIGIN}/api/auth/csrf"
    r = session.get(url, headers={"Accept": "application/json", "Referer": f"{CHATGPT_ORIGIN}/"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    token = data.get("csrfToken", "")
    if not token:
        raise ValueError("Failed to get CSRF token")
    return token


def _signin(session, email: str, csrf: str, device_id: str, auth_session_logging_id: str) -> str:
    """与参考一致：screen_hint=login_or_signup, login_hint=email"""
    url = f"{CHATGPT_ORIGIN}/api/auth/signin/openai"
    params = {
        "prompt": "login",
        "ext-oai-did": device_id,
        "auth_session_logging_id": auth_session_logging_id,
        "screen_hint": "login_or_signup",
        "login_hint": email,
    }
    form_data = {"callbackUrl": f"{CHATGPT_ORIGIN}/", "csrfToken": csrf, "json": "true"}
    r = session.post(url, params=params, data=form_data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Referer": f"{CHATGPT_ORIGIN}/",
        "Origin": CHATGPT_ORIGIN,
    }, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    authorize_url = data.get("url", "")
    if not authorize_url:
        raise ValueError("Failed to get authorize URL")
    return authorize_url


def _authorize(session, url: str) -> str:
    """GET 授权 URL；若未带 signup 则追加 screen_hint=signup，避免落到 log-in/password（登录）导致 invalid_auth_step。"""
    if "screen_hint=signup" not in url and "create-account" not in url:
        url = url + ("&" if "?" in url else "?") + "screen_hint=signup"
    r = session.get(url, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": f"{CHATGPT_ORIGIN}/",
        "Upgrade-Insecure-Requests": "1",
    }, timeout=HTTP_TIMEOUT, allow_redirects=True)
    return str(r.url)


def _ensure_password_page(session, state: str = None) -> None:
    """Authorize 后 GET create-account/password，确保会话处于密码页步骤（与参考中 final_path 含 create-account/password 等价）。"""
    url = f"{AUTH_ORIGIN}/create-account/password"
    if state:
        url = f"{url}?state={state}"
    r = session.get(url, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": f"{CHATGPT_ORIGIN}/",
        "Upgrade-Insecure-Requests": "1",
    }, timeout=HTTP_TIMEOUT, allow_redirects=True)


def _register(session, email: str, password: str, state: str = None):
    url = f"{AUTH_ORIGIN}/api/accounts/user/register"
    # Referer 仅路径，不带 state，避免服务端 state 校验过严导致 409
    referer = f"{AUTH_ORIGIN}/create-account/password"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": referer,
        "Origin": AUTH_ORIGIN,
    }
    headers.update(_make_trace_headers())
    r = session.post(url, json={"username": email, "password": password}, headers=headers, timeout=HTTP_TIMEOUT)
    try:
        data = r.json()
    except Exception:
        data = {"text": (r.text or "")[:500]}
    if r.status_code == 409:
        err = data.get("error") or {}
        print(f"[x] 4. Register 409: {err}", flush=True)
        # 409 invalid_state 多为当前 IP/出口被风控；可换住宅代理或使用浏览器自动化（Playwright）完成 1～4 步
        if err.get("code") == "invalid_state" or "invalid" in str(err).lower():
            raise RetryException("Step register returned 409 invalid_state")
    return r.status_code, data


def _send_otp(session):
    url = f"{AUTH_ORIGIN}/api/accounts/email-otp/send"
    r = session.get(url, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": f"{AUTH_ORIGIN}/create-account/password",
        "Upgrade-Insecure-Requests": "1",
    }, timeout=HTTP_TIMEOUT, allow_redirects=True)
    try:
        data = r.json()
    except Exception:
        data = {"final_url": str(r.url), "status": r.status_code}
    return r.status_code, data


def _validate_otp(session, code: str):
    url = f"{AUTH_ORIGIN}/api/accounts/email-otp/validate"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"{AUTH_ORIGIN}/email-verification",
        "Origin": AUTH_ORIGIN,
    }
    headers.update(_make_trace_headers())
    r = session.post(url, json={"code": code}, headers=headers, timeout=HTTP_TIMEOUT)
    try:
        data = r.json()
    except Exception:
        data = {"text": (r.text or "")[:500]}
    return r.status_code, data


def _create_account(session, name: str, birthdate: str):
    url = f"{AUTH_ORIGIN}/api/accounts/create_account"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"{AUTH_ORIGIN}/about-you",
        "Origin": AUTH_ORIGIN,
    }
    headers.update(_make_trace_headers())
    r = session.post(url, json={"name": name, "birthdate": birthdate}, headers=headers, timeout=HTTP_TIMEOUT)
    try:
        data = r.json()
    except Exception:
        data = {"text": (r.text or "")[:500]}
    return r.status_code, data


def _callback(session, url: str):
    if not url or not url.startswith("http"):
        return None, None
    r = session.get(url, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }, timeout=HTTP_TIMEOUT, allow_redirects=True)
    return r.status_code, {"final_url": str(r.url)}


def _parse_refresh_token_from_url(final_url: str) -> str:
    """从 callback 最终 URL 的 query 或 fragment 中解析 refresh_token。"""
    if not final_url or not isinstance(final_url, str):
        return ""
    try:
        parsed = urlparse(final_url)
        for part in (parsed.query, parsed.fragment):
            if not part:
                continue
            params = parse_qs(part, keep_blank_values=False)
            for key in ("refresh_token", "refresh_token_secret"):
                vals = params.get(key) or params.get(key.replace("_", "."))
                if vals and isinstance(vals[0], str) and vals[0].strip():
                    return vals[0].strip()
    except Exception:
        pass
    return ""


# -------------------- 入口 --------------------

def register_one_protocol(email: str, password: str, jwt_token: str, get_otp_fn, user_info: dict, **kwargs):
    """
    协议注册入口。
    入参：email, password, jwt_token, get_otp_fn(), user_info(name/year/month/day), step_log_fn, stop_check 等。
    返回：(email, password, success: bool[, status_extra[, tokens]])。
    """
    step_log_fn = kwargs.pop("step_log_fn", None)
    stop_check = kwargs.pop("stop_check", None)

    def _step(msg: str):
        if stop_check and callable(stop_check) and stop_check():
            raise RegistrationCancelled()
        if msg:
            print(msg, flush=True)
            if step_log_fn:
                try:
                    step_log_fn(msg.strip())
                except Exception:
                    pass

    _step(f"[*] register_one_protocol start {email}")
    pwd = (password or "").strip()
    if len(pwd) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password length must be >= {PASSWORD_MIN_LENGTH}, got {len(pwd)}. Set password in email row or use runner which auto-generates.")
    password = pwd
    name = user_info.get("name", "User")
    year = user_info.get("year", "1990")
    month = user_info.get("month", "01")
    day = user_info.get("day", "01")
    birthdate = f"{year}-{month}-{day}"

    device_id = str(uuid.uuid4())
    auth_session_logging_id = str(uuid.uuid4())
    session = _make_session(device_id)
    try:
        _step("[*] 0. Visit homepage")
        _visit_homepage(session)
        time.sleep(random.uniform(0.3, 0.8))

        _step("[*] 1. Get CSRF")
        csrf = _get_csrf(session)
        time.sleep(random.uniform(0.2, 0.5))

        _step("[*] 2. Signin")
        authorize_url = _signin(session, email, csrf, device_id, auth_session_logging_id)
        time.sleep(random.uniform(0.3, 0.8))

        _step("[*] 3. Authorize")
        final_url = _authorize(session, authorize_url)
        state = None
        if "state=" in final_url:
            m = re.search(r"state=([^&\s]+)", final_url)
            if m:
                state = m.group(1)
        time.sleep(random.uniform(0.3, 0.8))

        on_password_page = "create-account/password" in final_url
        on_email_verification = "email-verification" in final_url or "email-otp" in final_url

        if on_password_page:
            _step("[*] 3.5 GET create-account/password")
            _ensure_password_page(session, state)
            time.sleep(random.uniform(0.5, 1.0))
            _step("[*] 4. Register (user/register)")
            status_reg, data_reg = _register(session, email, password)
            if status_reg not in (200, 201, 204):
                print(f"[x] 4. Register failed: status={status_reg} data={data_reg}", flush=True)
                return email, password, False
            print("[ok] 4. Register OK", flush=True)
            _step("")
            _step("[*] 5. Send OTP")
            status_otp, data_otp = _send_otp(session)
            if status_otp not in (200, 201, 204) and (not isinstance(data_otp, dict) or data_otp.get("error")):
                print(f"[x] 5. Send OTP failed: status={status_otp} data={data_otp}", flush=True)
                return email, password, False
        elif on_email_verification:
            print("[*] 3. Authorize 已到 email-verification，跳过 4/5，直接等 OTP", flush=True)
            _step("[*] (skip 4 Register + 5 Send OTP, authorize 已触发 OTP)")
        else:
            print(f"[x] 3. Authorize 意外落地: {final_url[:100]}", flush=True)
            return email, password, False

        _step("[*] Waiting for email OTP...")
        if stop_check and callable(stop_check) and stop_check():
            return email, password, False
        code = get_otp_fn()
        if not code or len(str(code).strip()) < 4:
            print("[x] No OTP received or invalid", flush=True)
            return email, password, False
        # 规范为纯 6 位数字，避免空格/换行导致 wrong_email_otp_code
        code = re.sub(r"\D", "", str(code).strip())
        if len(code) < 6:
            print("[x] OTP too short after normalizing", flush=True)
            return email, password, False
        code = code[:6]

        _step("[*] 6. Validate OTP")
        status_val, data_val = _validate_otp(session, code)
        if status_val not in (200, 201, 204):
            err = (data_val.get("error") or {}) if isinstance(data_val, dict) else {}
            print(f"[x] 6. Validate OTP failed: status={status_val} data={data_val}", flush=True)
            return email, password, False

        _step("[*] 7. Create account")
        status_create, data_create = _create_account(session, name, birthdate)
        if status_create not in (200, 201, 204):
            print(f"[x] 7. Create account failed: status={status_create} data={data_create}", flush=True)
            return email, password, False

        callback_url = None
        if isinstance(data_create, dict):
            callback_url = data_create.get("continue_url") or data_create.get("url") or data_create.get("redirect_url")

        _step("[*] 8. Callback")
        callback_data = None
        if callback_url:
            _, callback_data = _callback(session, callback_url)

        print("[ok] Protocol registration success", flush=True)
        tokens = dict(data_create) if isinstance(data_create, dict) else {}
        if isinstance(callback_data, dict) and callback_data.get("final_url"):
            rt = _parse_refresh_token_from_url(callback_data["final_url"])
            if rt:
                tokens["refresh_token"] = rt
        return email, password, True, None, (tokens if tokens else None)
    except RegistrationCancelled:
        print("[*] 注册已停止", flush=True)
        return email, password, False
    except RetryException:
        raise
    except (requests.RequestException, ValueError) as e:
        print(f"[x] {e}", flush=True)
        return email, password, False
    except Exception as e:
        print(f"[x] Unexpected error: {e}", flush=True)
        return email, password, False
    finally:
        try:
            session.close()
        except Exception:
            pass


def activate_sora(tokens, email: str, **kwargs):
    """Sora 激活（注册成功后可调）。当前为桩，返回 False。"""
    return False
