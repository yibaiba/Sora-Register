"""
Hotmail007 邮箱 API 接入
文档: https://hotmail007.com/zh/apiDoc
请求 host: https://gapi.hotmail007.com
"""
import requests
from typing import Optional, List, Dict, Any

BASE_URL = "https://gapi.hotmail007.com"
TIMEOUT = 30

MAIL_TYPES = ("outlook", "hotmail", "hotmail Trusted", "outlook Trusted")


def get_balance(base_url: str, client_key: str) -> Optional[float]:
    """查询余额. GET /api/user/balance?clientKey=xxx"""
    url = (base_url or BASE_URL).rstrip("/") + "/api/user/balance"
    try:
        r = requests.get(url, params={"clientKey": client_key}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("success") and data.get("code") == 0:
            return float(data.get("data", 0))
    except Exception:
        pass
    return None


def get_stock(base_url: str, mail_type: Optional[str] = None) -> Optional[int]:
    """查询库存. GET /api/mail/getStock?mailType=xxx (mailType 可选)"""
    url = (base_url or BASE_URL).rstrip("/") + "/api/mail/getStock"
    params = {}
    if mail_type:
        params["mailType"] = mail_type
    try:
        r = requests.get(url, params=params or None, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("success") and data.get("code") == 0:
            return int(data.get("data", 0))
    except Exception:
        pass
    return None


def get_mail(
    base_url: str,
    client_key: str,
    quantity: int,
    mail_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    拉取邮箱. GET /api/mail/getMail?clientKey=xxx&mailType=xxx&quantity=n
    返回 data 为 ["Account:Password:Refresh_token:Client_id", ...]
    解析为 [{"email","password","refresh_token","client_id"}, ...]
    """
    url = (base_url or BASE_URL).rstrip("/") + "/api/mail/getMail"
    params = {"clientKey": client_key, "quantity": quantity}
    if mail_type:
        params["mailType"] = mail_type
    out = []
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if not data.get("success") or data.get("code") != 0:
            return out
        raw_list = data.get("data") or []
        for raw in raw_list:
            if not isinstance(raw, str):
                continue
            # Account:Password:Refresh_token:Client_id，其中 Refresh_token 可能含冒号
            parts = raw.split(":")
            if len(parts) < 4:
                continue
            email = parts[0].strip()
            password = parts[1].strip()
            client_id = parts[-1].strip()
            refresh_token = ":".join(parts[2:-1]).strip()
            out.append({
                "email": email,
                "password": password,
                "refresh_token": refresh_token,
                "client_id": client_id,
            })
    except Exception:
        pass
    return out


def get_first_mail(
    base_url: str,
    client_key: str,
    account: str,
    folder: str = "inbox",
) -> Optional[Dict[str, Any]]:
    """
    获取该邮箱最新一封邮件. GET /v1/mail/getFirstMail
    account 格式: email:password:refresh_token:client_id
    folder: inbox / junkemail
    """
    url = (base_url or BASE_URL).rstrip("/") + "/v1/mail/getFirstMail"
    params = {"clientKey": client_key, "account": account, "folder": folder}
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if not data.get("success") or data.get("code") != 0:
            return None
        return data.get("data")
    except Exception:
        pass
    return None
