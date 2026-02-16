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
from urllib.parse import urlparse, parse_qs
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    cfg,
    HTTP_TIMEOUT,
    get_proxy_url_for_session,
    get_proxy_url_random,
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
# OAuth code 换 token：HAR 中 callback 为 chatgpt.com/api/auth/callback/openai，client_id 从 authorize URL 解析，此处为兜底
OPENAI_OAUTH_CLIENT_ID_DEFAULT = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
OPENAI_OAUTH_REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"
OPENAI_TOKEN_URL = f"{AUTH_ORIGIN}/oauth/token"
SORA_ORIGIN = "https://sora.chatgpt.com"


def _make_session():
    """创建 Session：有 curl_cffi 则用 Chrome 指纹，否则用 requests。代理从端口区间随机选一个以保持 IP 一致。"""
    proxy = get_proxy_url_random()
    proxies = {"http": proxy, "https": proxy} if proxy else None

    if CURL_CFFI_AVAILABLE:
        # 模拟 Chrome 131 的 TLS/JA3 + HTTP/2，绕过 Cloudflare 403
        print("[*] Using curl_cffi with Chrome fingerprint", flush=True)
        session = curl_requests.Session(impersonate="chrome131")
        if proxies:
            session.proxies = proxies
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": CHATGPT_ORIGIN + "/",
        })
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
    """Step 1: 先访问首页拿 Cookie，再请求 CSRF token；403 时重试。"""
    # 先 GET 首页，拿 Set-Cookie，降低 403
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
    """Step 2: POST signin/openai；login_hint 绑定会话到该邮箱（注册时传）。"""
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
        raise ValueError(f"Step2: no auth URL, status={r.status_code}, body={r.text[:300]}")
    return auth_url


def _get_authorize_page(session, auth_url, follow_redirects=True):
    """Step 3: GET authorize。必须跟到底( follow_redirects=True )，会话需落在 create-account/password 页，服务端才接受 user/register（HAR: 302 -> create-account/password）。"""
    if not auth_url or not auth_url.startswith("http"):
        raise ValueError("Step3: invalid auth_url")
    session.headers["Referer"] = CHATGPT_ORIGIN + "/"
    orig_accept = session.headers.get("Accept")
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    last_err = None
    max_attempts = 8
    for attempt in range(max_attempts):
        try:
            r = session.get(auth_url, timeout=HTTP_TIMEOUT, allow_redirects=follow_redirects)
            if orig_accept:
                session.headers["Accept"] = orig_accept
            if r.status_code == 403:
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
    """GET create-account/password 页，使会话进入 signup state（仅当 Step3 落地 log-in 时调用）。"""
    if create_account_url:
        url = create_account_url
    elif state:
        url = f"{AUTH_ORIGIN}/create-account/password?state={state}"
    else:
        return
    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    try:
        session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
    except Exception:
        pass
    session.headers["Accept"] = "application/json, text/plain, */*"


def _load_email_then_password_pages(session, state):
    """模拟「先邮箱页、再密码页」：先 GET create-account/email 再 GET create-account/password，再发 user/register。"""
    if not state:
        return
    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    for path in ("/create-account/email", "/create-account/password"):
        try:
            session.get(f"{AUTH_ORIGIN}{path}?state={state}", timeout=HTTP_TIMEOUT, allow_redirects=True)
            time.sleep(0.2)
        except Exception:
            pass
    session.headers["Accept"] = "application/json, text/plain, */*"


def _post_authorize_continue(session, state=None):
    """Step 4: POST authorize/continue。响应可能为 JSON 或 302，需兼容处理。"""
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
    """Step 5: 声明走注册流程。Referer 需带 state 供服务端识别事务 ID。"""
    # 探针：打印发包前 Cookie 键名，检查关键 Auth0 凭证是否健在
    if hasattr(session, "cookies"):
        try:
            keys = [c.name for c in session.cookies]
            print(f"[*] Debug - Cookies before user/register: {keys}", flush=True)
        except Exception as e:
            try:
                keys = list(session.cookies.keys()) if hasattr(session.cookies, "keys") else []
                print(f"[*] Debug - Cookies before user/register (keys()): {keys}", flush=True)
            except Exception:
                print(f"[*] Debug - Cookies before user/register: (read failed: {e})", flush=True)

    url = f"{AUTH_ORIGIN}/api/accounts/user/register"
    # 完全信任外层已设置的 Referer，不再在此修改
    session.headers["Origin"] = AUTH_ORIGIN
    body = {}
    if password is not None:
        body["password"] = str(password)
    if email is not None:
        body["username"] = str(email)
    if continue_url_or_state and continue_url_or_state.startswith("http"):
        body["callback_url"] = continue_url_or_state
    payload = json.dumps(body, ensure_ascii=False)
    headers = {"Content-Type": "application/json", "Referer": session.headers.get("Referer", ""), "Origin": session.headers.get("Origin", "")}

    def _do_post(sess):
        return sess.post(url, data=payload.encode("utf-8"), headers=headers, timeout=HTTP_TIMEOUT)

    r = _do_post(session)
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}

    # 暂时注释：用 requests 重试会丢失 curl_cffi 指纹，增加排查干扰
    # if data.get("error") and "invalid_state" in str(data.get("error", {})):
    #     req_sess = requests.Session()
    #     ...
    #     pass

    if data.get("error") and "password" in str(data.get("error", {})).lower():
        proxy = get_proxy_url_for_session()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        req_sess = requests.Session()
        if proxies:
            req_sess.proxies.update(proxies)
        req_sess.headers.update(headers)
        if hasattr(session, "cookies"):
            try:
                jar = getattr(session.cookies, "get_dict", None)
                if callable(jar):
                    for name, value in session.cookies.get_dict().items():
                        req_sess.cookies.set(name, value, domain=".openai.com")
            except Exception:
                pass
        r2 = req_sess.post(url, json=body, timeout=HTTP_TIMEOUT)
        try:
            data = r2.json() if r2.text else {}
        except ValueError:
            data = {}
        if not data.get("error") and (data.get("continue_url") or data.get("url")):
            for c in r2.cookies:
                try:
                    session.cookies.set(c.name, c.value, domain=getattr(c, "domain", None) or ".openai.com")
                except Exception:
                    pass
            r = r2

    next_url = data.get("continue_url") or data.get("url") or ""
    return next_url, data


def _post_email_otp_send(session, email, continue_url_or_state=None):
    """Step 6: 发送邮箱验证码。抓包为 GET；POST 时勿传 callback_url（接口报 unknown_parameter）。"""
    url = f"{AUTH_ORIGIN}/api/accounts/email-otp/send"
    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Origin"] = AUTH_ORIGIN
    r = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
    try:
        data = r.json() if r.text and r.text.strip() and (r.headers.get("content-type") or "").strip().startswith("application/json") else {}
    except ValueError:
        data = {}
    if r.status_code >= 400:
        data["error"] = data.get("error") or f"HTTP {r.status_code}"
    next_url = data.get("continue_url") or data.get("url") or ""
    return next_url, data


def _post_email_otp_validate(session, code, continue_url_or_state=None):
    """Step 7: 校验邮箱验证码。抓包确认 body 为 code。"""
    url = f"{AUTH_ORIGIN}/api/accounts/email-otp/validate"
    session.headers["Referer"] = AUTH_ORIGIN + "/"
    session.headers["Origin"] = AUTH_ORIGIN
    body = {"code": code}
    if continue_url_or_state and continue_url_or_state.startswith("http"):
        body["callback_url"] = continue_url_or_state
    r = session.post(url, json=body, timeout=HTTP_TIMEOUT)
    data = r.json() if r.text else {}
    next_url = data.get("continue_url") or data.get("url") or ""
    return next_url, data


def _post_create_account(session, email, password, name, year, month, day, continue_url_or_state=None, referer_url=None):
    """Step 8: 完成账号创建。HAR 确认 body 仅 name + birthdate。referer_url 为 validate 后 follow 的落地页时更接近浏览器。"""
    url = f"{AUTH_ORIGIN}/api/accounts/create_account"
    if referer_url and AUTH_ORIGIN in referer_url:
        session.headers["Referer"] = referer_url
    elif "about-you" not in (session.headers.get("Referer") or ""):
        session.headers["Referer"] = AUTH_ORIGIN + "/about-you"
    session.headers["Origin"] = AUTH_ORIGIN
    birthdate = f"{year}-{month.zfill(2) if len(month) < 2 else month}-{day.zfill(2) if len(day) < 2 else day}"
    # 全名只支持字母和空格，提交前做一次清洗
    name_clean = re.sub(r"[^A-Za-z ]", "", str(name)).strip() or "User"
    body = {"name": name_clean, "birthdate": birthdate}
    r = session.post(url, json=body, timeout=HTTP_TIMEOUT)
    data = r.json() if r.text else {}
    return r.status_code, data


def _follow_continue_url(session, url):
    """
    访问 continue_url；若某次 302 的 Location 指向带 code= 的 callback，则不再跟随该跳转，以便保留 code 用于换 token。
    返回 (final_url, callback_url_with_code)：final_url 为最终落地 URL（若未跟到 callback 则为最后一次请求的 URL）；callback_url_with_code 为未请求的 callback URL（含 code），供解析后换 token。
    """
    if not url or not url.startswith("http"):
        return None, None
    if AUTH_ORIGIN not in url and CHATGPT_ORIGIN not in url:
        return None, None
    try:
        next_url = url
        callback_with_code = None
        for _ in range(20):
            r = session.get(next_url, timeout=HTTP_TIMEOUT, allow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = (r.headers.get("Location") or "").strip()
                if not loc:
                    break
                if loc.startswith("/"):
                    base = CHATGPT_ORIGIN if "auth/callback" in loc or "code=" in loc else (AUTH_ORIGIN if AUTH_ORIGIN in next_url else CHATGPT_ORIGIN)
                    loc = base + loc
                if "code=" in loc:
                    callback_with_code = loc
                    break
                next_url = loc
            else:
                next_url = r.url
                break
        return next_url, callback_with_code
    except Exception:
        return None, None


def _parse_callback_code(callback_url: str):
    """从 callback URL（含 code=）解析 code 与 state。返回 (code, state) 或 (None, None)。"""
    if not callback_url or "code=" not in callback_url:
        return None, None
    try:
        parsed = urlparse(callback_url)
        qs = parse_qs(parsed.query, keep_blank_values=False)
        code_list = qs.get("code")
        state_list = qs.get("state")
        code = (code_list[0] or "").strip() if code_list else ""
        state = (state_list[0] or "").strip() if state_list else ""
        return (code, state) if code else (None, None)
    except Exception:
        return None, None


def _exchange_code_for_token(session, code: str, redirect_uri: str, client_id: str):
    """
    用 OAuth authorization code 向 auth.openai.com 换 access_token / refresh_token。
    返回 dict: {"refresh_token": "...", "access_token": "...", "expires_in": ...} 或 失败时 {}。
    """
    if not code or not redirect_uri or not client_id:
        return {}
    url = OPENAI_TOKEN_URL
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    try:
        # 标准 OAuth2 token 请求多为 application/x-www-form-urlencoded
        session.headers["Content-Type"] = "application/x-www-form-urlencoded"
        session.headers["Referer"] = CHATGPT_ORIGIN + "/"
        session.headers["Origin"] = AUTH_ORIGIN
        r = session.post(url, data=body, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            print(f"[*] Token exchange status={r.status_code} body={r.text[:200]}", flush=True)
            return {}
        data = r.json() if r.text else {}
        out = {}
        if data.get("refresh_token"):
            out["refresh_token"] = data["refresh_token"]
        if data.get("access_token"):
            out["access_token"] = data["access_token"]
        if "expires_in" in data:
            out["expires_in"] = data["expires_in"]
        return out
    except Exception as e:
        print(f"[*] Token exchange error: {e}", flush=True)
        return {}
    finally:
        session.headers["Content-Type"] = "application/json"


def _parse_authorize_params(auth_url: str):
    """从 Step2 返回的 authorize URL 解析 client_id、redirect_uri。返回 (client_id, redirect_uri)。"""
    if not auth_url:
        return OPENAI_OAUTH_CLIENT_ID_DEFAULT, OPENAI_OAUTH_REDIRECT_URI
    try:
        parsed = urlparse(auth_url)
        qs = parse_qs(parsed.query, keep_blank_values=False)
        cid = (qs.get("client_id") or [None])[0]
        ru = (qs.get("redirect_uri") or [None])[0]
        if cid:
            cid = cid.strip()
        if ru:
            ru = ru.strip()
        return (cid or OPENAI_OAUTH_CLIENT_ID_DEFAULT, ru or OPENAI_OAUTH_REDIRECT_URI)
    except Exception:
        return OPENAI_OAUTH_CLIENT_ID_DEFAULT, OPENAI_OAUTH_REDIRECT_URI


def _refresh_access_token(session, refresh_token: str, client_id: str):
    """用 refresh_token 向 auth.openai.com 换新的 access_token。返回 access_token 或 None。"""
    if not refresh_token or not client_id:
        return None
    try:
        session.headers["Content-Type"] = "application/x-www-form-urlencoded"
        session.headers["Referer"] = CHATGPT_ORIGIN + "/"
        session.headers["Origin"] = AUTH_ORIGIN
        r = session.post(
            OPENAI_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json() if r.text else {}
        return data.get("access_token") or None
    except Exception:
        return None
    finally:
        session.headers["Content-Type"] = "application/json"


def _sora_set_username(session, access_token: str, username: str):
    """POST sora.chatgpt.com 设置 username，完成 Sora 激活/onboarding。返回是否成功。"""
    if not access_token or not username:
        return False
    url = f"{SORA_ORIGIN}/backend/project_y/profile/username/set"
    session.headers["Authorization"] = f"Bearer {access_token}"
    session.headers["Content-Type"] = "application/json"
    session.headers["Referer"] = f"{SORA_ORIGIN}/onboarding?redirect=/explore"
    session.headers["Origin"] = SORA_ORIGIN
    try:
        r = session.post(url, json={"username": username}, timeout=HTTP_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def _username_from_email(email: str):
    """从邮箱生成 Sora 用户名：本地部分仅保留字母数字 + 6 位随机数，总长不超过 20。"""
    local = (email or "").split("@")[0].lower()
    local = re.sub(r"[^a-z0-9]", "", local)[:14]
    if not local:
        local = "u"
    suffix = "".join(str(random.randint(0, 9)) for _ in range(6))
    return (local + suffix)[:20]


def activate_sora(tokens: dict, email: str):
    """
    用注册得到的 token 激活 Sora（设置 username）。
    若 tokens 含 access_token 则直接用；否则用 refresh_token 换 access_token 再请求。
    返回 True 表示设置成功。
    """
    if not tokens or not isinstance(tokens, dict):
        return False
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token and not refresh_token:
        return False
    session = _make_session()
    try:
        if not access_token and refresh_token:
            access_token = _refresh_access_token(session, refresh_token, OPENAI_OAUTH_CLIENT_ID_DEFAULT)
        if not access_token:
            return False
        username = _username_from_email(email)
        ok = _sora_set_username(session, access_token, username)
        if ok:
            print("[ok] Sora activated (username set)", flush=True)
        return ok
    finally:
        try:
            session.close()
        except Exception:
            pass


def register_one_protocol(email: str, password: str, jwt_token: str, get_otp_fn, user_info: dict):
    """
    协议版注册单账号。Step 3 易 403/TLS，失败时自动用新 session 重试整轮一次。

    参数:
        email: 临时邮箱地址
        password: 账号密码
        jwt_token: 临时邮箱 Worker 的 JWT，用于 get_otp_fn 内部拉邮件
        get_otp_fn: 无参可调用，返回 6 位验证码字符串，超时返回 None
        user_info: 至少包含 name, year, month, day（字符串）

    返回:
        tuple: (email, password, success: bool, status_extra=None)。status_extra 为 "finish_setup" 表示 Step8 报错但可能已发完成邮件。
    """
    name = user_info.get("name", "User")
    year = user_info.get("year", "1990")
    month = user_info.get("month", "01")
    day = user_info.get("day", "01")

    def _step(msg: str, ok: str = "") -> None:
        print(msg, flush=True)
        if ok:
            print(ok, flush=True)

    for round_no in range(2):
        if round_no > 0:
            print("[*] Retry with new session...", flush=True)
        session = _make_session()
        try:
            # Step 1
            _step("[*] [1/8] Getting CSRF...")
            csrf = _get_csrf(session)
            _step("", "[ok] [1/8] CSRF done")

            # Step 2: POST signin。不带 login_hint 时更易落到 create-account/password（HAR 流程）；带 login_hint 易落 email-verification
            _step("[*] [2/8] signin/openai...")
            auth_url = _signin_openai(session, csrf, login_hint=None)
            _step("", f"[ok] [2/8] Got authorize URL")
            oauth_client_id, oauth_redirect_uri = _parse_authorize_params(auth_url)

            # state 从 auth_url 解析（跟重定向后 final_url 可能无 state，3b/4 必须用）
            state = ""
            if "state=" in auth_url:
                m = re.search(r"state=([^&]+)", auth_url)
                if m:
                    state = m.group(1)
            # 保持代理不中断，避免 Step1/2 代理 IP 与 Step3+ 直连 IP 不一致导致 Auth0 会话被标污染
            # if getattr(session, "proxies", None):
            #     session.proxies = {"http": None, "https": None}
            #     print("[*] No proxy for auth.openai.com (Step 3+)", flush=True)
            # Step 3: GET authorize 跟到底；可带 screen_hint=signup 争取 302 直接到 create-account
            _step("[*] [3/8] GET authorize (follow)...")
            auth_url_to_use = auth_url
            if "screen_hint=signup" not in auth_url_to_use:
                if "screen_hint=" in auth_url_to_use:
                    auth_url_to_use = re.sub(r"screen_hint=[^&]+", "screen_hint=signup", auth_url_to_use)
                else:
                    auth_url_to_use = auth_url_to_use + ("&" if "?" in auth_url_to_use else "?") + "screen_hint=signup"
            state_from_redirect, final_url = _get_authorize_page(session, auth_url_to_use, follow_redirects=True)
            if state_from_redirect:
                state = state_from_redirect
            if "state=" in final_url:
                m = re.search(r"state=([^&]+)", final_url)
                if m:
                    state = m.group(1)
            _step("", f"[ok] [3/8] Landed: {final_url[:55]}...")
            if state and "log-in" in final_url and "email-verification" not in final_url:
                _step("[*] [3b] GET create-account/password...")
                _ensure_create_account_flow(session, state, None)
                _step("", "[ok] [3b] Done")
            # HAR 真理：AUTHORIZE_STEP4 数量为 0，浏览器落地后直接 Step5。调用 Step4 会消费 state 导致 invalid_state
            # _step("[*] [4/8] authorize/continue...")
            # next_url, _ = _post_authorize_continue(session, state)
            # if next_url and "email-verification" in final_url:
            #     _follow_continue_url(session, next_url)
            # elif next_url and "create-account" in final_url and state:
            #     pass
            # elif next_url:
            #     _follow_continue_url(session, next_url)
            # _step("", "[ok] [4/8] Done")
            next_url_after_4 = None
            time.sleep(0.5)
            # 原汤化原食：落地页 URL 作为 Referer，与 Next.js 路由状态一致
            session.headers["Referer"] = final_url if "state=" in final_url else (f"{final_url}?state={state}" if state else final_url)

            if "email-verification" in final_url:
                # 落地邮箱验证页：先发码、验码，再 user/register、create_account
                _step("[*] [6/8] Sending OTP (email-verification flow first)...")
                next_url, d6 = _post_email_otp_send(session, email, next_url_after_4 or state)
                if d6.get("error") or (hasattr(d6.get("error"), "__len__") and len(d6.get("error", "")) > 0):
                    print(f"[x] [6/8] Send failed: {d6}", flush=True)
                    return email, password, False
                _step("", "[ok] [6/8] OTP sent")
                print("[*] Waiting for email OTP...", flush=True)
                otp = get_otp_fn()
                if not otp or len(otp) < 4:
                    print("[x] No OTP received", flush=True)
                    return email, password, False
                print("[ok] OTP received", flush=True)
                _step("[*] [7/8] Validating OTP...")
                next_url, d7 = _post_email_otp_validate(session, otp.strip(), next_url or state)
                if next_url:
                    final_url, callback_url = _follow_continue_url(session, next_url)
                    url_for_code = callback_url or final_url
                    if final_url and ("chatgpt.com" in final_url or "code=" in (url_for_code or "")):
                        _step("", "[ok] [7/8] OTP OK")
                        print("[ok] [8/8] Callback reached (registration complete)", flush=True)
                        code, _ = _parse_callback_code(url_for_code or final_url)
                        tokens = _exchange_code_for_token(session, code, oauth_redirect_uri, oauth_client_id) if code else {}
                        return email, password, True, None, tokens if tokens else None
                _step("", "[ok] [7/8] OTP OK")
                state_after_otp = state
                if next_url and "state=" in next_url:
                    m = re.search(r"state=([^&]+)", next_url)
                    if m:
                        state_after_otp = m.group(1)
                _step("[*] [4b] authorize/continue again (after OTP)...")
                next_url_4b, _ = _post_authorize_continue(session, state_after_otp)
                referer_8 = None
                if next_url_4b:
                    referer_8, _ = _follow_continue_url(session, next_url_4b)
                time.sleep(0.3)
                session.headers["Referer"] = f"{AUTH_ORIGIN}/create-account/password"
                # email-verification 流：OTP 后直接 create_account，用 4b follow 落地页作 Referer
                _step("[*] [8/8] Creating account (skip user/register in email-verification flow)...")
                status, d8 = _post_create_account(session, email, password, name, year, month, day, None, referer_url=referer_8)
                if status in (200, 201, 204):
                    print("[ok] [8/8] Protocol registration success", flush=True)
                    return email, password, True, None, None
                print(f"[x] [8/8] Failed status={status} body={d8}", flush=True)
                return email, password, False

            # 常规流程：极简复现 HAR —— Step 3 落地后直接 Step 5（Referer 带 state + 纯净 body）
            # _step("[*] [4.5] Load email then password pages...")
            # _load_email_then_password_pages(session, state)
            # _step("", "[ok] [4.5] Done")
            _step("[*] [5/8] user/register...")
            next_url, d5 = _post_user_register(session, state=state, email=email, password=password, continue_url_or_state=None)
            if d5.get("error"):
                print(f"[x] [5/8] user/register failed: {d5}", flush=True)
                return email, password, False
            if next_url:
                _, _ = _follow_continue_url(session, next_url)
            _step("", "[ok] [5/8] Done")

            _step("[*] [6/8] Sending OTP to email...")
            next_url, d6 = _post_email_otp_send(session, email, next_url or state)
            if d6.get("error") or (hasattr(d6.get("error"), "__len__") and len(d6.get("error", "")) > 0):
                print(f"[x] [6/8] Send failed: {d6}", flush=True)
                return email, password, False
            _step("", "[ok] [6/8] OTP sent")
            print("[*] Waiting for email OTP...", flush=True)
            otp = get_otp_fn()
            if not otp or len(otp) < 4:
                print("[x] No OTP received", flush=True)
                return email, password, False
            print("[ok] OTP received", flush=True)
            _step("[*] [7/8] Validating OTP...")
            next_url, d7 = _post_email_otp_validate(session, otp.strip(), next_url or state)
            callback_for_8 = next_url or state
            final_url = None
            if next_url:
                final_url, callback_url = _follow_continue_url(session, next_url)
                if final_url:
                    callback_for_8 = final_url
                    url_for_code = callback_url or final_url
                    if "chatgpt.com" in final_url or "code=" in (url_for_code or ""):
                        _step("", "[ok] [7/8] OTP OK")
                        print("[ok] [8/8] Callback reached (registration complete)", flush=True)
                        code, _ = _parse_callback_code(url_for_code or final_url)
                        tokens = _exchange_code_for_token(session, code, oauth_redirect_uri, oauth_client_id) if code else {}
                        return email, password, True, None, tokens if tokens else None
            if callback_for_8 and not callback_for_8.startswith("http"):
                callback_for_8 = None
            _step("", "[ok] [7/8] OTP OK")
            # 浏览器成功提交 create_account 时 Referer 为 about-you（抓包 200 OK 确认）
            referer_8 = final_url if (final_url and "about-you" in final_url) else (f"{AUTH_ORIGIN}/about-you?state={state}" if state else f"{AUTH_ORIGIN}/about-you")
            session.headers["Referer"] = referer_8
            _step("[*] [8/8] Creating account...")
            status, d8 = _post_create_account(session, email, password, name, year, month, day, callback_for_8, referer_url=referer_8)
            if status in (200, 201, 204):
                print("[ok] [8/8] Protocol registration success", flush=True)
                return email, password, True, None, None
            # 409 invalid_state 时后端常已保存进度并发了 "Finish account setup" 邮件，仍保存账号供用户查邮件或尝试登录
            if status == 409 and d8.get("error", {}).get("code") == "invalid_state":
                print("[*] [8/8] Step8 invalid_state but progress may be saved (check email for 'Finish account setup')", flush=True)
                return email, password, False, "finish_setup"
            print(f"[x] [8/8] Failed status={status} body={d8}", flush=True)
            return email, password, False

        except requests.RequestException as e:
            print(f"[x] Request error: {e}", flush=True)
            return email, password, False
        except ValueError as e:
            err_msg = str(e)
            if round_no == 0 and ("Step3" in err_msg or "Step1" in err_msg or "csrf" in err_msg.lower() or "403" in err_msg):
                continue  # 整轮重试一次（Step1/3 常 403 偶发）
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
    # 两轮都失败
    return email, password, False
