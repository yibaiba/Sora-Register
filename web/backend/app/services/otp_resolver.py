"""
注册 OTP：通过 Hotmail007 拉信，从邮件正文/标题解析 6 位验证码。
复用 get_first_mail，不重复拉信逻辑。
"""
import re
import time
from typing import Optional

from app.services.hotmail007 import get_first_mail

# 6 位数字验证码
_OTP_PATTERN = re.compile(r"\b(\d{6})\b")


def _extract_otp_from_mail(data: Optional[dict]) -> Optional[str]:
    """从 get_first_mail 返回的 data 中解析第一个 6 位数字。"""
    if not data or not isinstance(data, dict):
        return None
    texts = []
    for key in ("Subject", "subject", "Text", "text", "Body", "body", "Html", "html", "Content", "content"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            texts.append(v)
    combined = " ".join(texts)
    m = _OTP_PATTERN.search(combined)
    return m.group(1) if m else None


def get_otp_for_email(
    base_url: str,
    client_key: str,
    account_str: str,
    timeout_sec: float = 120,
    interval_sec: float = 5,
    folder: str = "inbox",
    stop_check=None,
) -> Optional[str]:
    """
    轮询该邮箱最新一封邮件，解析 6 位验证码，超时返回 None。
    stop_check: 可调用对象，返回 True 时立即结束并返回 None。
    """
    if not client_key or not account_str:
        return None
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if stop_check and callable(stop_check) and stop_check():
            return None
        data = get_first_mail(base_url, client_key, account_str, folder=folder)
        otp = _extract_otp_from_mail(data)
        if otp:
            return otp
        for _ in range(int(interval_sec)):
            if stop_check and callable(stop_check) and stop_check():
                return None
            time.sleep(1)
    return None
