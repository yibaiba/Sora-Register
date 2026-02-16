"""批量生成页仪表盘：统计与 API 配置状态"""
from fastapi import APIRouter, Depends
from app.routers.auth import get_current_user
from app.database import get_db, init_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM accounts")
        total_registered = c.fetchone()[0]
        c.execute(
            "SELECT COUNT(*) FROM accounts WHERE date(created_at) = date('now')"
        )
        today_registered = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM accounts WHERE phone_bound = 1")
        phone_bound_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM accounts WHERE has_plus = 1")
        plus_count = c.fetchone()[0]
        c.execute(
            "SELECT key, value FROM system_settings WHERE key IN ('email_api_key', 'sms_api_key', 'bank_card_api_key', 'captcha_api_key', 'thread_count', 'last_run_success', 'last_run_fail')"
        )
        settings = dict(c.fetchall())
    email_api_set = bool(settings.get("email_api_key") and str(settings.get("email_api_key", "")).strip())
    sms_api_set = bool(settings.get("sms_api_key") and str(settings.get("sms_api_key", "")).strip())
    bank_api_set = bool(settings.get("bank_card_api_key") and str(settings.get("bank_card_api_key", "")).strip())
    captcha_api_set = bool(settings.get("captcha_api_key") and str(settings.get("captcha_api_key", "")).strip())
    thread_count = settings.get("thread_count") or "1"
    try:
        success_count = int(settings.get("last_run_success") or 0)
    except (TypeError, ValueError):
        success_count = 0
    try:
        fail_count = int(settings.get("last_run_fail") or 0)
    except (TypeError, ValueError):
        fail_count = 0
    return {
        "today_registered": today_registered,
        "total_registered": total_registered,
        "phone_bound_count": phone_bound_count,
        "plus_count": plus_count,
        "email_api_set": email_api_set,
        "sms_api_set": sms_api_set,
        "bank_api_set": bank_api_set,
        "captcha_api_set": captcha_api_set,
        "thread_count": thread_count,
        "success_count": success_count,
        "fail_count": fail_count,
    }
