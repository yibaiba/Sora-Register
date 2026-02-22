# -*- coding: utf-8 -*-
"""
开始绑定手机：从账号管理取 phone_bound=0 且有 RT/AT 的账号，从手机号管理取可用号码，
执行 Sora 激活 + enroll/start -> 轮询验证码 -> enroll/finish，更新 accounts.phone_bound、phone_numbers.used_count。
"""
import re
import random
import threading
from datetime import datetime, timedelta

from app.database import get_db, init_db
from app.services import hero_sms

# 绑定任务状态（与 register 类似，独立 stop 标志）
_phone_bind_running = False
_phone_bind_task_id = None
_phone_bind_heartbeat = None
_phone_bind_stop = False
_phone_bind_lock = threading.Lock()

PHONE_CODE_POLL_INTERVAL = 5
PHONE_CODE_MAX_RETRIES = 60


def is_phone_bind_stop_requested() -> bool:
    with _phone_bind_lock:
        return _phone_bind_stop


def set_phone_bind_stop(value: bool) -> None:
    with _phone_bind_lock:
        global _phone_bind_stop
        _phone_bind_stop = value


def set_phone_bind_task_started(task_id: str) -> bool:
    """返回 False 表示已在运行。"""
    with _phone_bind_lock:
        global _phone_bind_running, _phone_bind_task_id, _phone_bind_heartbeat, _phone_bind_stop
        if _phone_bind_running:
            return False
        _phone_bind_running = True
        _phone_bind_task_id = task_id
        _phone_bind_heartbeat = datetime.utcnow().isoformat() + "Z"
        _phone_bind_stop = False
        return True


def get_phone_bind_status() -> dict:
    with _phone_bind_lock:
        return {
            "running": _phone_bind_running,
            "task_id": _phone_bind_task_id,
            "heartbeat": _phone_bind_heartbeat,
        }


# 接码平台未返回到期时间时，默认有效期（分钟），与 sms_api 一致
_PHONE_DEFAULT_EXPIRE_MINUTES = 20


def _get_settings():
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT key, value FROM system_settings WHERE key IN ("
            "'sms_api_url', 'sms_api_key', 'proxy_url', 'sms_openai_service', 'sms_max_price', 'phone_bind_limit')"
        )
        rows = c.fetchall()
    out = {}
    for k, v in rows:
        out[k] = (v or "").strip()
    out.setdefault("sms_api_url", "https://hero-sms.com/stubs/handler_api.php")
    return out


def _log(task_id: str, level: str, message: str):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO run_logs (task_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (task_id, level, (message or "")[:500], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
    except Exception:
        pass


def fetch_accounts_to_bind(limit: int = 50):
    """账号管理：phone_bound=0 且 (refresh_token 或 access_token) 非空。"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT id, email, refresh_token, access_token, proxy FROM accounts
               WHERE phone_bound = 0 AND (refresh_token IS NOT NULL AND refresh_token != '' OR access_token IS NOT NULL AND access_token != '')
               ORDER BY id ASC LIMIT ?""",
            (limit,),
        )
        return c.fetchall()


def fetch_phones_available(limit: int = 50):
    """手机号管理：used_count < max_use_count 且 activation_id 非空。"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT id, phone, activation_id, max_use_count, used_count FROM phone_numbers
               WHERE activation_id IS NOT NULL AND used_count < max_use_count
               ORDER BY id ASC LIMIT ?""",
            (limit,),
        )
        return c.fetchall()


def _fetch_numbers_from_api(task_id: str, max_try: int = 3) -> int:
    """无可用手机号时从接码 API 拉取并写入 phone_numbers，返回成功写入条数。"""
    settings = _get_settings()
    base = settings.get("sms_api_url") or "https://hero-sms.com/stubs/handler_api.php"
    key = settings.get("sms_api_key") or ""
    if not key:
        _log(task_id, "warning", "未配置接码 API KEY，无法自动拉取手机号")
        return 0
    service = (settings.get("sms_openai_service") or "openai").strip() or "openai"
    try:
        max_price = float(settings.get("sms_max_price") or "0")
    except (TypeError, ValueError):
        max_price = 0
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM system_settings WHERE key = ?", ("phone_bind_limit",))
        row = c.fetchone()
        limit = int(row[0]) if row and row[0] else 1
    country = 0
    inserted = 0
    for _ in range(max_try):
        result = hero_sms.get_number(base, key, service, country, max_price=max_price)
        if result and result.get("error"):
            result = hero_sms.get_number_v2(base, key, service, country, max_price=max_price)
        if not result:
            break
        if result.get("error"):
            _log(task_id, "warning", "自动拉取手机号失败: " + str(result["error"])[:200])
            break
        expired_at = result.get("expired_at")
        if not (expired_at and str(expired_at).strip()):
            default_end = (datetime.utcnow() + timedelta(minutes=_PHONE_DEFAULT_EXPIRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
            expired_at = default_end
        else:
            raw = str(expired_at).strip()
            if "T" in raw:
                raw = raw.replace("Z", "").split(".")[0].replace("T", " ")
            expired_at = raw
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO phone_numbers (phone, activation_id, max_use_count, remark, expired_at) VALUES (?, ?, ?, ?, ?)",
                (result["phone_number"], result["activation_id"], limit, "Hero-SMS(自动)", expired_at),
            )
            inserted += 1
            _log(task_id, "info", "自动拉取手机号: " + str(result["phone_number"]))
    return inserted


def run_one_phone_bind(task_id: str, account_id: int, email: str, refresh_token: str, access_token: str, account_proxy: str,
                       phone_id: int, phone: str, activation_id: int,
                       sms_base: str, sms_key: str, proxy_url: str) -> bool:
    """
    单条绑定：拿 AT -> Sora 激活 -> enroll/start -> 轮询验证码 -> enroll/finish -> 更新 DB。
    返回 True 表示成功。
    """
    from app.registration_env import inject_registration_modules
    inject_registration_modules()

    import protocol_sora_phone as sora_phone

    def log(msg):
        _log(task_id, "info", msg)

    at = (access_token or "").strip()
    if not at and (refresh_token or "").strip():
        log(f"[绑定] {email} RT 换 AT...")
        out = sora_phone.rt_to_at_mobile(refresh_token.strip(), proxy_url=proxy_url or account_proxy, log_fn=log)
        at = (out.get("access_token") or "").strip()
        new_rt = out.get("refresh_token")
        if new_rt and isinstance(new_rt, str):
            try:
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE accounts SET refresh_token = ? WHERE id = ?", (new_rt.strip(), account_id))
            except Exception:
                pass
    if not at:
        log(f"[绑定] {email} 无 AT，跳过")
        return False

    if is_phone_bind_stop_requested():
        return False

    log(f"[绑定] {email} Sora 激活...")
    if not sora_phone.sora_ensure_activated(at, proxy_url=proxy_url or account_proxy, log_fn=log):
        log(f"[绑定] {email} Sora 激活失败")
        return False

    if is_phone_bind_stop_requested():
        return False

    log(f"[绑定] {email} 发送验证码 -> {phone}")
    ok, err = sora_phone.sora_phone_enroll_start(at, phone, proxy_url=proxy_url or account_proxy, log_fn=log)
    if not ok:
        if err == "phone_used":
            log(f"[绑定] 手机号已被使用: {phone}")
        return False

    code = None
    for i in range(PHONE_CODE_MAX_RETRIES):
        if is_phone_bind_stop_requested():
            return False
        out = hero_sms.get_status_v2(sms_base, sms_key, activation_id)
        if out and out.get("code"):
            raw = out.get("code")
            m = re.search(r"\d{6}", str(raw))
            if m:
                code = m.group()
                break
        import time
        time.sleep(PHONE_CODE_POLL_INTERVAL)

    if not code:
        log(f"[绑定] {email} 获取验证码超时")
        return False

    log(f"[绑定] {email} 提交验证码...")
    if not sora_phone.sora_phone_enroll_finish(at, phone, code, proxy_url=proxy_url or account_proxy, log_fn=log):
        log(f"[绑定] {email} 验证码提交失败")
        return False

    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE accounts SET phone_bound = 1 WHERE id = ?", (account_id,))
        c.execute("UPDATE phone_numbers SET used_count = used_count + 1 WHERE id = ?", (phone_id,))
    log(f"[绑定] 成功 {email} -> {phone}")
    return True


def run_phone_bind_loop(task_id: str, max_count: int = None):
    """后台循环：取待绑定账号与可用手机号，逐条执行绑定直到无数据或达到 max_count 或停止。"""
    global _phone_bind_running, _phone_bind_heartbeat, _phone_bind_stop
    settings = _get_settings()
    sms_base = settings.get("sms_api_url") or "https://hero-sms.com/stubs/handler_api.php"
    sms_key = settings.get("sms_api_key") or ""
    proxy_url = settings.get("proxy_url") or ""
    if not sms_key:
        _log(task_id, "error", "请先在系统设置中配置手机号接码 API KEY")
        with _phone_bind_lock:
            _phone_bind_running = False
        return

    processed = 0
    success_count = 0
    try:
        while True:
            if is_phone_bind_stop_requested():
                _log(task_id, "info", "已请求停止绑定")
                break
            if max_count is not None and processed >= max_count:
                break

            accounts = fetch_accounts_to_bind(limit=10)
            phones = fetch_phones_available(limit=10)
            if not accounts:
                _log(task_id, "info", "无待绑定账号（phone_bound=0 且有 RT/AT）")
                break
            if not phones:
                _log(task_id, "info", "无可用手机号，尝试从接码 API 自动拉取")
                _fetch_numbers_from_api(task_id, max_try=3)
                phones = fetch_phones_available(limit=10)
            if not phones:
                _log(task_id, "info", "无可用手机号（已尝试自动拉取仍无）")
                break

            with _phone_bind_lock:
                _phone_bind_heartbeat = datetime.utcnow().isoformat() + "Z"

            # 取一对
            acc = accounts[0]
            ph = phones[0]
            account_id, email, rt, at, account_proxy = acc[0], acc[1], acc[2], acc[3], acc[4] or ""
            phone_id, phone, act_id = ph[0], ph[1], ph[2]

            ok = run_one_phone_bind(
                task_id,
                account_id, email, rt or "", at or "", account_proxy,
                phone_id, phone, act_id,
                sms_base, sms_key, proxy_url,
            )
            processed += 1
            if ok:
                success_count += 1
    finally:
        with _phone_bind_lock:
            global _phone_bind_running
            _phone_bind_running = False
        _log(task_id, "info", f"绑定任务结束 处理 {processed} 条 成功 {success_count} 条")
