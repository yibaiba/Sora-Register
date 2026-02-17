"""
协议版 ChatGPT 注册
纯 HTTP 请求完成注册流程（无浏览器），对接文档见 docs/REGISTRATION_AND_PLUS_PROTOCOL.md 1.3 节。
使用 curl_cffi 模拟 Chrome TLS/JA3 指纹以绕过 chatgpt.com 的 403。
"""

import json
import random
import re
import time
import uuid
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 严格导入本地 config.py 存在的函数
from config import (
    cfg,
    HTTP_TIMEOUT,
    get_proxy_url_for_session,
)
from utils import get_user_agent

# 优先使用 curl_cffi 模拟 Chrome，绕过 403
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

# 域名（需在 _make_session 前定义）
CHATGPT_ORIGIN = "https://chatgpt.com"
AUTH_ORIGIN = "https://auth.openai.com"

# 随机指纹：与 impersonate 严格对应的 User-Agent，保证 JA3/UA 一致
IMPERSONATE_OPTIONS = ["chrome120", "chrome124", "chrome131", "edge101", "safari15_5"]
IMPERSONATE_UA = {
    "chrome120": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "chrome124": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "chrome131": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "edge101": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.0.0 Safari/537.36 Edg/101.0.0.0",
    "safari15_5": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Safari/605.1.15",
}

# 调试开关：True 时在关键请求前后打印 Method/URL/Headers/Status，403 时打印 <title>
DEBUG_REQUESTS = True

# Chrome 常见 Header 顺序，用于降低被检测为脚本的概率
CHROME_HEADER_ORDER = [
    "Accept", "Accept-Language", "Accept-Encoding", "User-Agent",
    "Referer", "Origin", "Content-Type", "Authorization",
]


def _sanitize_headers(headers):
    """脱敏：隐藏 Authorization、Cookie 等敏感内容。"""
    if headers is None:
        return {}
    out = {}
    for k, v in (list(headers.items()) if hasattr(headers, "items") else []):
        k = k if isinstance(k, str) else str(k)
        v = str(v) if v is not None else ""
        if k.lower() in ("authorization", "cookie", "x-api-key"):
            out[k] = "(redacted)" if v else ""
        else:
            out[k] = v[:80] + "..." if len(v) > 80 else v
    return out


def _debug_request(method, url, headers, status_code=None, response_preview=None, title_on_403=None):
    """调试输出：Method, URL, Headers（脱敏）, Status；403 时可选打印 <title>。"""
    if not DEBUG_REQUESTS:
        return
    print(f"[debug] {method} {url}", flush=True)
    print(f"[debug] Headers: {_sanitize_headers(headers)}", flush=True)
    if status_code is not None:
        print(f"[debug] Status: {status_code}", flush=True)
    if response_preview is not None and response_preview:
        print(f"[debug] Body preview: {response_preview[:200]}", flush=True)
    if title_on_403 is not None:
        print(f"[debug] 403 page title: {title_on_403}", flush=True)


def _reorder_headers_chrome(session):
    """按 Chrome 常见顺序重排 session.headers，仅处理已存在的键。"""
    if not hasattr(session, "headers") or not session.headers:
        return
    order = [k for k in CHROME_HEADER_ORDER if k in session.headers]
    rest = [k for k in session.headers if k not in order]
    new_headers = {}
    for k in order + rest:
        new_headers[k] = session.headers[k]
    session.headers.clear()
    session.headers.update(new_headers)


def _make_session():
    """创建 Session：有 curl_cffi 则随机浏览器指纹+匹配 UA，否则用 requests。"""
    proxy = get_proxy_url_for_session()
    proxies = {"http": proxy, "https": proxy} if proxy else None

    if CURL_CFFI_AVAILABLE:
        impersonate = random.choice(IMPERSONATE_OPTIONS)
        ua = IMPERSONATE_UA.get(impersonate) or IMPERSONATE_UA["chrome131"]
        print(f"[*] Using curl_cffi impersonate={impersonate}", flush=True)
        session = curl_requests.Session(impersonate=impersonate)
        if proxies:
            session.proxies = proxies
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": ua,
            "Referer": CHATGPT_ORIGIN + "/",
        })
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


def _get_csrf(session):
    try:
        session.get(CHATGPT_ORIGIN + "/", timeout=HTTP_TIMEOUT)
        time.sleep(0.2)
    except Exception:
        pass
    url = f"{CHATGPT_ORIGIN}/api/auth/csrf"
    last_err = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code == 403:
                last_err = f"403 Forbidden (attempt {attempt + 1}/3)"
                time.sleep(1 + attempt)
                continue
            r.raise_for_status()
            data = r.json() if r.text else {}
            token = data.get("csrfToken") or data.get("token") or data.get("csrf")
            if not token:
                raise ValueError(f"Step1 csrf: no token in response, body={data}")
            return token
        except requests.HTTPError as e:
            last_err = e
            if e.response is not None and e.response.status_code == 403:
                time.sleep(1 + attempt)
                continue
            raise
    raise ValueError(last_err or "Step1 csrf: 403 after retries")


def _signin_openai(session, csrf_token, login_hint=None):
    device_id = str(uuid.uuid4())
    loggin_id = str(uuid.uuid4())
    url = (
        f"{CHATGPT_ORIGIN}/api/auth/signin/openai"
        f"?prompt=login&screen_hint=login_or_signup&ext-oai-did={device_id}&auth_session_logging_id={loggin_id}"
    )
    if login_hint:
        from urllib.parse import quote
        url += f"&login_hint={quote(login_hint, safe='')}"
    session.headers["Referer"] = CHATGPT_ORIGIN + "/"
    session.headers["Origin"] = CHATGPT_ORIGIN
    form = {
        "callbackUrl": f"{CHATGPT_ORIGIN}/",
        "csrfToken": csrf_token or "",
        "json": "true",
    }
    r = session.post(url, data=form, timeout=HTTP_TIMEOUT, allow_redirects=False)
    r.raise_for_status()
    auth_url = ""
    if r.status_code in (301, 302, 303, 307, 308):
        auth_url = r.headers.get("Location") or ""
        if auth_url.startswith("/"):
            auth_url = AUTH_ORIGIN + auth_url
    if not auth_url:
        try:
            data = r.json()
            auth_url = data.get("url") or data.get("continue_url") or data.get("location") or ""
        except Exception:
            pass
    if not auth_url or not auth_url.startswith("http"):
        raise ValueError(f"Step2: no auth URL, status={r.status_code}")
    return auth_url


def _get_authorize_page(session, auth_url, follow_redirects=True):
    if not auth_url or not auth_url.startswith("http"):
        raise ValueError("Step3: invalid auth_url")
    session.headers["Referer"] = CHATGPT_ORIGIN + "/"
    orig_accept = session.headers.get("Accept")
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    last_err = None
    max_attempts = 8
    for attempt in range(max_attempts):
        try:
            _debug_request("GET", auth_url, session.headers)
            r = session.get(auth_url, timeout=HTTP_TIMEOUT, allow_redirects=follow_redirects)
            _debug_request("GET", auth_url, session.headers, status_code=r.status_code, response_preview=(r.text or "")[:200] if r.text else None)
            if r.status_code == 403:
                title = ""
                if r.text and "<title>" in r.text:
                    mt = re.search(r"<title[^>]*>([^<]+)</title>", r.text, re.I | re.S)
                    if mt:
                        title = mt.group(1).strip()[:100]
                _debug_request("GET", auth_url, session.headers, status_code=403, title_on_403=title or "(no title)")
                last_err = f"403 (attempt {attempt + 1}/{max_attempts})"
                time.sleep(3 + attempt)
                continue
            state = ""
            check_url = r.url
            if r.status_code in (301, 302, 303, 307, 308):
                check_url = r.headers.get("Location") or r.url
            if "state=" in check_url:
                m = re.search(r"state=([^&]+)", check_url)
                if m:
                    state = m.group(1)
            if not follow_redirects and r.status_code in (301, 302, 303, 307, 308):
                return state, check_url
            r.raise_for_status()
            return state, r.url
        except requests.HTTPError as e:
            if orig_accept:
                session.headers["Accept"] = orig_accept
            last_err = e
            if e.response is not None and e.response.status_code == 403:
                time.sleep(1 + attempt)
                continue
            raise
        except Exception as e:
            if orig_accept:
                session.headers["Accept"] = orig_accept
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(2 + attempt)
                continue
            raise
    if orig_accept:
        session.headers["Accept"] = orig_accept
    raise ValueError(last_err or "Step3: 403 after retries")


def _ensure_create_account_flow(session, state, create_account_url=None):
    if create_account_url:
        url = create_account_url
    elif state:
        # 这一步至关重要：它告诉服务端“我已经准备好输入密码了”
        url = f"{AUTH_ORIGIN}/create-account/password?state={state}"
    else:
        return None

    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    try:
        # 【关键】：允许跟随重定向并抓取最终的 URL
        r = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        new_state = state
        if "state=" in r.url:
            m = re.search(r"state=([^&]+)", r.url)
            if m:
                new_state = m.group(1)
        session.headers["Accept"] = "application/json, text/plain, */*"
        return new_state
    except Exception:
        session.headers["Accept"] = "application/json, text/plain, */*"
        return state


def _post_authorize_continue(session, state=None):
    url = f"{AUTH_ORIGIN}/api/accounts/authorize/continue"
    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Origin"] = AUTH_ORIGIN
    body = {}
    if state:
        body["state"] = state
    r = session.post(url, json=body, timeout=HTTP_TIMEOUT, allow_redirects=False)
    next_url = ""
    try:
        data = r.json() if r.text and r.text.strip() else {}
    except ValueError:
        data = {}
    next_url = data.get("continue_url") or data.get("url") or ""
    if not next_url and r.status_code in (301, 302, 303, 307, 308):
        next_url = r.headers.get("Location") or ""
        if next_url.startswith("/"):
            next_url = AUTH_ORIGIN + next_url
    return next_url, data


def _post_user_register(session, state=None, email=None, password=None, continue_url_or_state=None):
    # 【改动点 1】：把 state 拼接到 URL 后面
    url = f"{AUTH_ORIGIN}/api/accounts/user/register"
    if state:
        url = f"{url}?state={state}"

    session.headers["Origin"] = AUTH_ORIGIN
    body = {}

    # 【改动点 2】：把刚才加进去的 body["state"] = str(state) 删掉！

    if password is not None:
        body["password"] = str(password)
    if email is not None:
        body["username"] = str(email)
    if continue_url_or_state and continue_url_or_state.startswith("http"):
        body["callback_url"] = continue_url_or_state

    payload = json.dumps(body, ensure_ascii=False)
    headers = {"Content-Type": "application/json", "Referer": session.headers.get("Referer", ""), "Origin": session.headers.get("Origin", "")}

    _debug_request("POST", url, {**dict(session.headers), **headers})
    r = session.post(url, data=payload.encode("utf-8"), headers=headers, timeout=HTTP_TIMEOUT)
    _debug_request("POST", url, None, status_code=r.status_code, response_preview=(r.text or "")[:200] if r.text else None)
    if r.status_code == 403 and r.text and "<title>" in r.text:
        mt = re.search(r"<title[^>]*>([^<]+)</title>", r.text, re.I | re.S)
        if mt:
            _debug_request("POST", url, None, status_code=403, title_on_403=mt.group(1).strip()[:100])

    # 200 但返回 HTML 说明被 Cloudflare 静默验证页拦截
    if r.status_code == 200 and r.text and "<html" in r.text.lower():
        title = ""
        mt = re.search(r"<title[^>]*>([^<]+)</title>", r.text, re.I | re.S)
        if mt:
            title = mt.group(1).strip()
        print(f"[x] [5/8] user/register blocked by challenge page: '{title}'", flush=True)
        return "", {"error": "blocked_by_cloudflare_challenge"}

    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}

    next_url = data.get("continue_url") or data.get("url") or ""
    return next_url, data


def _post_email_otp_send(session, email, continue_url_or_state=None):
    url = f"{AUTH_ORIGIN}/api/accounts/email-otp/send"

    # 【修复】：必须把 state 挂在 URL 上
    if continue_url_or_state and not continue_url_or_state.startswith("http"):
        url = f"{url}?state={continue_url_or_state}"

    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Origin"] = AUTH_ORIGIN

    # 【修复】：改为 POST 请求
    r = session.post(url, json={}, timeout=HTTP_TIMEOUT, allow_redirects=False)

    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}

    if r.status_code >= 400:
        data["error"] = data.get("error") or f"HTTP {r.status_code}"

    next_url = data.get("continue_url") or data.get("url") or ""
    return next_url, data


def _post_email_otp_validate(session, code, continue_url_or_state=None):
    url = f"{AUTH_ORIGIN}/api/accounts/email-otp/validate"

    # 【修复】：验证的时候也必须带上 state
    if continue_url_or_state and not continue_url_or_state.startswith("http"):
        url = f"{url}?state={continue_url_or_state}"

    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Origin"] = AUTH_ORIGIN
    body = {"code": code}

    # 原有的 callback_url 逻辑保留
    if continue_url_or_state and continue_url_or_state.startswith("http"):
        body["callback_url"] = continue_url_or_state

    r = session.post(url, json=body, timeout=HTTP_TIMEOUT)
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}

    next_url = data.get("continue_url") or data.get("url") or ""
    return next_url, data


def _post_create_account(session, email, password, name, year, month, day, continue_url_or_state=None, referer_url=None):
    url = f"{AUTH_ORIGIN}/api/accounts/create_account"
    if referer_url and AUTH_ORIGIN in referer_url:
        session.headers["Referer"] = referer_url
    elif "create-account" not in (session.headers.get("Referer") or ""):
        session.headers["Referer"] = AUTH_ORIGIN + "/create-account/password"
    session.headers["Origin"] = AUTH_ORIGIN
    birthdate = f"{year}-{month.zfill(2) if len(month) < 2 else month}-{day.zfill(2) if len(day) < 2 else day}"
    body = {"name": name, "birthdate": birthdate}

    # 【新增】：新流程需要在最后一步提交密码
    if password is not None:
        body["password"] = str(password)

    r = session.post(url, json=body, timeout=HTTP_TIMEOUT)
    data = r.json() if r.text else {}
    return r.status_code, data


def _follow_continue_url(session, url):
    if not url or not url.startswith("http"):
        return None
    if AUTH_ORIGIN not in url and CHATGPT_ORIGIN not in url:
        return None
    try:
        r = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        return r.url
    except Exception:
        return None


class RegistrationCancelled(Exception):
    """用户请求停止注册时抛出。"""
    pass


def register_one_protocol(email: str, password: str, jwt_token: str, get_otp_fn, user_info: dict, **kwargs):
    step_log_fn = kwargs.pop("step_log_fn", None)
    stop_check = kwargs.pop("stop_check", None)
    print(f"[*] register_one_protocol start {email}", flush=True)
    if step_log_fn:
        try:
            step_log_fn(f"[*] register_one_protocol start {email}")
        except Exception:
            pass
    name = user_info.get("name", "User")
    year = user_info.get("year", "1990")
    month = user_info.get("month", "01")
    day = user_info.get("day", "01")

    def _step(msg: str, ok: str = "") -> None:
        if stop_check and callable(stop_check) and stop_check():
            raise RegistrationCancelled()
        if msg:
            print(msg, flush=True)
            if step_log_fn:
                try:
                    step_log_fn(msg.strip())
                except Exception:
                    pass
        if ok:
            print(ok, flush=True)
            if step_log_fn:
                try:
                    step_log_fn(ok.strip())
                except Exception:
                    pass

    for round_no in range(2):
        if stop_check and callable(stop_check) and stop_check():
            return email, password, False
        if round_no > 0:
            print("[*] Retry with new session...", flush=True)
        session = _make_session()
        try:
            # 检查代理生效：解析并打印 ip= / loc=，确认代理与地区
            try:
                r = session.get("https://chatgpt.com/cdn-cgi/trace", timeout=10)
                if r.status_code == 200 and r.text:
                    ip_line = loc_line = ""
                    for line in r.text.strip().splitlines():
                        if line.startswith("ip="):
                            ip_line = line
                        elif line.startswith("loc="):
                            loc_line = line
                    if ip_line or loc_line:
                        print(f"[*] proxy: {ip_line or 'ip=?'} {loc_line or 'loc=?'}", flush=True)
                    else:
                        print(f"[*] cdn-cgi/trace: {r.text.strip()[:300]}", flush=True)
            except Exception as e:
                print(f"[*] cdn-cgi/trace check failed: {e}", flush=True)

            _step("[*] [1/8] Getting CSRF...")
            csrf = _get_csrf(session)
            _step("", "[ok] [1/8] CSRF done")

            _step("[*] [2/8] signin/openai...")
            auth_url = _signin_openai(session, csrf, login_hint=None)
            _step("", f"[ok] [2/8] Got authorize URL")

            state = ""
            if "state=" in auth_url:
                m = re.search(r"state=([^&]+)", auth_url)
                if m:
                    state = m.group(1)

            _step("[*] [3/8] GET authorize (follow)...")
            auth_url_to_use = auth_url
            if "screen_hint=signup" not in auth_url_to_use:
                if "screen_hint=" in auth_url_to_use:
                    auth_url_to_use = re.sub(r"screen_hint=[^&]+", "screen_hint=signup", auth_url_to_use)
                else:
                    auth_url_to_use = auth_url_to_use + ("&" if "?" in auth_url_to_use else "?") + "screen_hint=signup"

            # 正常跟随，以便种下所有的 Auth0 cookies
            state_from_redirect, final_url = _get_authorize_page(session, auth_url_to_use, follow_redirects=True)
            if state_from_redirect:
                state = state_from_redirect
            if final_url and isinstance(final_url, str) and final_url.startswith("/"):
                final_url = AUTH_ORIGIN + final_url
            if "state=" in (final_url or ""):
                m = re.search(r"state=([^&]+)", final_url)
                if m:
                    state = m.group(1)
            _step("", f"[ok] [3/8] Landed: {(final_url or '')[:55]}...")

            # ==========================================
            # 终极动态适配：Auth0 状态机严格遵守
            # ==========================================
            time.sleep(0.5)

            # [4/8] authorize/continue (仅当卡在 authorize 页面时才需要推一把)
            if "authorize" in (final_url or ""):
                _step("[*] [4/8] authorize/continue...")
                next_url_4, d4 = _post_authorize_continue(session, state)
                if next_url_4 and "state=" in next_url_4:
                    m = re.search(r"state=([^&]+)", next_url_4)
                    if m:
                        state = m.group(1)
                _step("", "[ok] [4/8] Done")
                time.sleep(0.5)
                session.headers["Referer"] = final_url or f"{AUTH_ORIGIN}/create-account?state={state}"
            else:
                _step("[*] [4/8] authorize/continue (Skipped, already on create-account)...")
                session.headers["Referer"] = final_url or f"{AUTH_ORIGIN}/create-account?state={state}"

            # [5/8] user/register（一次提交，不补救。authorize 流程 Step 4 已 continue；create-account 直接提交）
            _step("[*] [5/8] user/register...")
            next_url_5, d5 = _post_user_register(session, state=state, email=email, password=password)
            if d5.get("error"):
                print(f"[x] [5/8] user/register failed: {d5}", flush=True)
                return email, password, False
            _step("", "[ok] [5/8] Done")
            time.sleep(0.5)

            # [6/8] email-otp/send
            _step("[*] [6/8] Sending OTP to email...")
            # 现在会话里已经成功存入邮箱了，这下是真的发邮件了！
            next_url_6, d6 = _post_email_otp_send(session, email, next_url_5 or state)
            if d6.get("error") or (hasattr(d6.get("error"), "__len__") and len(d6.get("error", "")) > 0):
                print(f"[x] [6/8] Send failed: {d6}", flush=True)
                return email, password, False
            _step("", "[ok] [6/8] OTP sent")

            print("[*] Waiting for email OTP...", flush=True)
            if stop_check and callable(stop_check) and stop_check():
                return email, password, False
            otp = get_otp_fn()
            if not otp or len(otp) < 4:
                print("[x] No OTP received", flush=True)
                return email, password, False
            print("[ok] OTP received", flush=True)

            _step("[*] [7/8] Validating OTP...")
            next_url, d7 = _post_email_otp_validate(session, otp.strip(), next_url_6 or state)
            callback_for_8 = next_url or state
            final_url_7 = None
            if next_url:
                final_url_7 = _follow_continue_url(session, next_url)
                if final_url_7:
                    callback_for_8 = final_url_7
                    if "chatgpt.com" in final_url_7 or "code=" in final_url_7:
                        _step("", "[ok] [7/8] OTP OK")
                        print("[ok] [8/8] Callback reached (registration complete) 🎉", flush=True)
                        return email, password, True

            if callback_for_8 and not callback_for_8.startswith("http"):
                callback_for_8 = None
            _step("", "[ok] [7/8] OTP OK")

            referer_8 = final_url_7 if (final_url_7 and "state=" in final_url_7) else f"{AUTH_ORIGIN}/create-account/profile?state={state}"
            session.headers["Referer"] = referer_8

            _step("[*] [8/8] Creating account...")
            status, d8 = _post_create_account(session, email, password, name, year, month, day, callback_for_8, referer_url=referer_8)

            if status in (200, 201, 204):
                print("[ok] [8/8] Protocol registration success 🎉", flush=True)
                return email, password, True

            print(f"[x] [8/8] Failed status={status} body={d8}", flush=True)
            return email, password, False

        except RegistrationCancelled:
            print("[*] 注册已停止", flush=True)
            return email, password, False
        except requests.RequestException as e:
            print(f"[x] Request error: {e}", flush=True)
            return email, password, False
        except ValueError as e:
            err_msg = str(e)
            if round_no == 0 and ("Step3" in err_msg or "Step1" in err_msg or "csrf" in err_msg.lower() or "403" in err_msg):
                continue
            print(f"[x] {e}", flush=True)
            return email, password, False
        except Exception as e:
            err_msg = str(e)
            if ("403" in err_msg or "TLS" in err_msg or "curl" in err_msg) and round_no == 0:
                continue
            print(f"[x] Unexpected error: {e}", flush=True)
            return email, password, False
        finally:
            try:
                session.close()
            except Exception:
                pass
                
    return email, password, False