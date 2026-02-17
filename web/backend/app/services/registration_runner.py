"""
单条注册任务运行器：从 DB 取未注册邮箱与配置，调 protocol_register，落库 accounts/run_logs，更新 last_run_success/fail。
不实现 8 步协议，仅「取配置 → 调 register_one_protocol / activate_sora → 落库」。
"""
import random
from datetime import datetime
from typing import Optional, Tuple

from app.database import get_db, init_db
from app.registration_env import inject_registration_modules, set_task_config, clear_task_config
from app.registration_state import is_stop_requested
from app.services.otp_resolver import get_otp_for_email

# 首次执行注册前注入 config/utils，再懒加载 protocol_register，避免未注入时导入
_injected = False


def _ensure_injected():
    global _injected
    if not _injected:
        inject_registration_modules()
        _injected = True


def _get_registration_settings() -> dict:
    """从 system_settings 读取注册所需配置。"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT key, value FROM system_settings WHERE key IN (
            'thread_count', 'retry_count', 'proxy_url', 'email_api_url', 'email_api_key'
            )"""
        )
        rows = c.fetchall()
    out = {}
    for k, v in rows:
        out[k] = (v or "").strip()
    out.setdefault("retry_count", "2")
    out.setdefault("thread_count", "1")
    return out


def fetch_one_unregistered_email(conn) -> Optional[Tuple]:
    """取一条未注册邮箱。返回 (id, email, password, uuid, token) 或 None。"""
    c = conn.cursor()
    c.execute(
        """SELECT e.id, e.email, e.password, e.uuid, e.token
           FROM emails e
           LEFT JOIN accounts a ON LOWER(TRIM(e.email)) = LOWER(TRIM(a.email))
           WHERE a.email IS NULL AND e.email IS NOT NULL AND TRIM(e.email) != ''
           LIMIT 1"""
    )
    row = c.fetchone()
    return tuple(row) if row else None


def fetch_unregistered_emails(limit: int = 10):
    """取最多 limit 条未注册邮箱，用于多线程分配。返回 [(id, email, password, uuid, token), ...]。"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT e.id, e.email, e.password, e.uuid, e.token
               FROM emails e
               LEFT JOIN accounts a ON LOWER(TRIM(e.email)) = LOWER(TRIM(a.email))
               WHERE a.email IS NULL AND e.email IS NOT NULL AND TRIM(e.email) != ''
               LIMIT ?""",
            (max(1, limit),),
        )
        rows = c.fetchall()
    return [tuple(r) for r in rows]


def _default_user_info() -> dict:
    """协议需要的 name, year, month, day（字符串）。"""
    y = random.randint(1985, 2000)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return {
        "name": "User",
        "year": str(y),
        "month": str(m).zfill(2),
        "day": str(d).zfill(2),
    }


def _run_one_registration(
    email_id: int,
    email: str,
    password: str,
    uuid_val: str,
    token: str,
    settings: dict,
    task_id: str,
) -> Tuple[bool, Optional[str], Optional[dict]]:
    """
    执行单条注册（不重试）。返回 (success, status_extra, tokens)。
    """
    _ensure_injected()
    import protocol_register as pr  # 注入后导入

    base = (settings.get("email_api_url") or "https://gapi.hotmail007.com").rstrip("/")
    key = settings.get("email_api_key") or ""
    # 配置了代理则必须使用该代理，不覆盖为 None
    configured_proxy = (settings.get("proxy_url") or "").strip()
    proxy_url = configured_proxy if configured_proxy else None

    account_str = f"{email}:{password or ''}:{token or ''}:{uuid_val or ''}"
    otp_timeout = 120
    otp_interval = 5

    def get_otp_fn():
        return get_otp_for_email(
            base, key, account_str,
            timeout_sec=otp_timeout, interval_sec=otp_interval,
            stop_check=is_stop_requested,
        )

    def _step_log(msg: str) -> None:
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
                    (task_id, "info", (msg or "")[:500]),
                )
        except Exception:
            pass

    set_task_config(proxy_url=proxy_url, timeout=60, http_max_retries=5)
    try:
        result = pr.register_one_protocol(
            email,
            password,
            token or "",
            get_otp_fn,
            _default_user_info(),
            proxy_url=proxy_url,
            step_log_fn=_step_log,
            stop_check=is_stop_requested,
        )
    except Exception as e:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
                (task_id, "error", f"注册异常 {email}: {e!s}"),
            )
        return False, str(e), None
    finally:
        clear_task_config()

    # result: (email, password, success[, status_extra[, tokens]])
    success = bool(result[2]) if len(result) > 2 else False
    status_extra = result[3] if len(result) > 3 else None
    tokens = result[4] if len(result) > 4 else None
    if isinstance(tokens, dict):
        pass
    else:
        tokens = None
    return success, status_extra, tokens


def _random_password() -> str:
    """生成简单随机密码，满足协议要求。"""
    import string
    pool = string.ascii_letters + string.digits + "!@#$"
    return "".join(random.choices(pool, k=14))


def run_one_with_retry(
    email_id: int,
    email: str,
    password: str,
    uuid_val: str,
    token: str,
    settings: dict,
    task_id: str,
) -> bool:
    """
    单条任务带重试（1～5 次），成功写 accounts、run_logs、last_run_success，失败写 run_logs、last_run_fail。
    返回是否最终成功。
    """
    pwd = (password or "").strip() or _random_password()
    if len(pwd) < 12:
        pwd = _random_password()
    retry_count = max(1, min(5, int(settings.get("retry_count") or "2")))
    last_error = None
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
            (task_id, "info", f"正在注册账号 {email}"),
        )
    if is_stop_requested():
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
                (task_id, "info", f"任务已停止，跳过 {email}"),
            )
        return False
    for attempt in range(retry_count):
        if is_stop_requested():
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
                    (task_id, "info", f"任务已停止，跳过 {email}"),
                )
            return False
        success, status_extra, tokens = _run_one_registration(
            email_id, email, pwd, uuid_val, token, settings, task_id
        )
        if success:
            _ensure_injected()
            import protocol_register as pr
            # 注册与开通 Sora 必须使用同一代理，一步到位
            same_proxy = (settings.get("proxy_url") or "").strip() or None
            if tokens:
                try:
                    pr.activate_sora(tokens, email, proxy_url=same_proxy)
                except Exception:
                    pass
            try:
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        """INSERT INTO accounts (email, password, status, registered_at, has_sora, has_plus, phone_bound, proxy, refresh_token)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            email,
                            pwd,
                            "Registered+Sora" if tokens else "Registered",
                            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                            1 if tokens else 0,
                            0,
                            0,
                            (settings.get("proxy_url") or "").strip() or None,
                            (tokens.get("refresh_token") or "") if isinstance(tokens, dict) else None,
                        ),
                    )
                    c.execute(
                        "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
                        (task_id, "info", f"注册成功 {email}"),
                    )
                    c.execute("SELECT value FROM system_settings WHERE key = 'last_run_success'")
                    r2 = c.fetchone()
                    prev_ok = int((r2[0] or "0")) if r2 else 0
                    c.execute(
                        "INSERT OR REPLACE INTO system_settings (key, value) VALUES ('last_run_success', ?)",
                        (str(prev_ok + 1),),
                    )
            except Exception as e:
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
                        (task_id, "error", f"注册成功但写入账号列表失败 {email}: {e!s}"),
                    )
                return False
            return True
        last_error = status_extra or "注册失败"
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
                (task_id, "error", f"尝试 {attempt + 1}/{retry_count} 失败 {email}: {last_error}"),
            )

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO run_logs (task_id, level, message) VALUES (?, ?, ?)",
            (task_id, "error", f"注册失败 {email} (已重试 {retry_count} 次)"),
        )
        c.execute("SELECT value FROM system_settings WHERE key = 'last_run_fail'")
        row = c.fetchone()
        prev = int((row[0] or "0")) if row else 0
        c.execute(
            "INSERT OR REPLACE INTO system_settings (key, value) VALUES ('last_run_fail', ?)",
            (str(prev + 1),),
        )
    return False


def run_one_task(
    task_id: str,
    settings: Optional[dict] = None,
    email_row: Optional[Tuple] = None,
) -> bool:
    """
    执行单条注册任务。若传 email_row 则用该行；否则从 DB 取一条未注册邮箱。
    返回是否执行并成功（无任务可执行时返回 False）。
    """
    init_db()
    if settings is None:
        settings = _get_registration_settings()
    if email_row is not None:
        row = email_row
    else:
        with get_db() as conn:
            row = fetch_one_unregistered_email(conn)
    if not row:
        return False
    if is_stop_requested():
        return False
    email_id, email, password, uuid_val, token = row
    return run_one_with_retry(email_id, email, password or "", uuid_val or "", token or "", settings, task_id)
