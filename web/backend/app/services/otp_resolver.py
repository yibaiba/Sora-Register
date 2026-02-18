"""
注册 OTP：通过 Hotmail007 拉信，从邮件正文/标题解析 6 位验证码。
与参考 chatgpt_register.py 对齐：多模式优先匹配（code:、verify、>xxx<、纯 6 位），只返回 6 位数字。
"""
import re
import time
from typing import Optional

from app.services.hotmail007 import get_first_mail

# 与参考一致：优先匹配 OpenAI 邮件里常见格式，再回退到任意 6 位
_OTP_PATTERNS = [
    r">\s*(\d{6})\s*<",       # HTML 中 >123456<
    r"(\d{6})\s*\n",          # 行末 6 位
    r"code[:\s]+(\d{6})",     # code: 123456 / code 123456
    r"verify.*?(\d{6})",      # verify...123456
    r"\b(\d{6})\b",           # 独立 6 位数字
    r"(\d{6})",               # 任意 6 位（最后回退）
]


def _extract_otp_from_mail(data: Optional[dict]) -> Optional[str]:
    """从 get_first_mail 返回的 data 中解析 6 位验证码，与参考提取顺序一致。"""
    if not data or not isinstance(data, dict):
        return None
    texts = []
    for key in ("Subject", "subject", "Text", "text", "Body", "body", "Html", "html", "Content", "content"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            texts.append(v)
    combined = " ".join(texts)
    for pattern in _OTP_PATTERNS:
        m = re.search(pattern, combined, re.IGNORECASE | re.DOTALL)
        if m:
            raw = m.group(1)
            digits = re.sub(r"\D", "", raw)
            if len(digits) >= 6:
                return digits[:6]
    return None


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
