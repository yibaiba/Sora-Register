"""
Hero-SMS 接码平台 API（兼容 SMS-Activate 协议）
文档: https://hero-sms.com/cn/api
服务端: https://hero-sms.com/stubs/handler_api.php
"""
import re
import json
import requests
from typing import Optional, List, Dict, Any

BASE_URL = "https://hero-sms.com/stubs/handler_api.php"
TIMEOUT = 30


def _get(base_url: str, api_key: str, action: str, **params) -> Optional[str]:
    url = (base_url or BASE_URL).strip()
    params["action"] = action
    params["api_key"] = api_key
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return (r.text or "").strip()
    except Exception:
        return None


def get_balance(base_url: str, api_key: str) -> Optional[float]:
    """查询余额. action=getBalance → 返回 ACCESS_BALANCE:数字"""
    text = _get(base_url, api_key, "getBalance")
    if not text or not text.startswith("ACCESS_BALANCE"):
        return None
    m = re.search(r"ACCESS_BALANCE[:\s]+([\d.]+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def get_number(
    base_url: str,
    api_key: str,
    service: str,
    country: int = 0,
    operator: Optional[str] = None,
    max_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    申请号码. action=getNumber&service=xxx&country=n
    返回 ACCESS_NUMBER:activationId:phoneNumber 或错误前缀（返回 {"error": "原始响应"}）
    """
    params = {"service": service, "country": country}
    if operator:
        params["operator"] = operator
    if max_price is not None:
        params["maxPrice"] = max_price
    text = _get(base_url, api_key, "getNumber", **params)
    if not text:
        return {"error": "无响应"}
    if not text.startswith("ACCESS_NUMBER"):
        return {"error": text}
    parts = text.split(":")
    if len(parts) >= 3:
        try:
            return {
                "activation_id": int(parts[1]),
                "phone_number": parts[2],
                "raw": text,
            }
        except (ValueError, IndexError):
            pass
    return {"error": text}


def get_number_v2(
    base_url: str,
    api_key: str,
    service: str,
    country: int = 0,
    max_price: Optional[float] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """
    申请号码 V2。文档: action=getNumberV2&service=xxx&country=n&maxPrice=n。
    支持两种返回格式：单条 { activationId, phoneNumber } 或 { data: [ { id, phone, ... } ] }。
    统一返回 { activation_id, phone_number [, raw ] } 或 { error }。
    """
    try:
        url = (base_url or BASE_URL).strip()
        params = {"action": "getNumberV2", "api_key": api_key, "service": service, "country": country, **kwargs}
        if max_price is not None:
            params["maxPrice"] = max_price
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        raw_text = (r.text or "").strip()
        if not raw_text:
            return {"error": "接口返回为空，请检查 API 地址与网络"}
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            if raw_text and len(raw_text) < 200 and "<" not in raw_text:
                return {"error": raw_text}
            return {"error": "接口返回非 JSON: " + (raw_text[:150] if raw_text else "空")}

        item = None
        if isinstance(data, dict):
            if data.get("activationId") is not None:
                item = data
            elif isinstance(data.get("data"), list) and len(data["data"]) > 0:
                item = data["data"][0]

        if not item or not isinstance(item, dict):
            err = (data.get("message") or data.get("error") if isinstance(data, dict) else None) or getattr(r, "text", str(data))
            return {"error": str(err)[:300] if err else "无可用号码或返回格式异常"}

        activation_id = item.get("activationId") or item.get("id")
        phone = item.get("phoneNumber") or item.get("phone") or item.get("number") or ""
        if activation_id is None:
            return {"error": "返回中无 activationId/id"}
        expired_at = item.get("activationEndTime") or item.get("expiredAt") or item.get("expired_at") or None
        out = {
            "activation_id": int(activation_id),
            "phone_number": str(phone),
            "raw": data,
        }
        if expired_at:
            out["expired_at"] = str(expired_at).strip()
        return out
    except Exception as e:
        return {"error": str(e)}


def get_status(base_url: str, api_key: str, activation_id: int) -> Optional[Dict[str, Any]]:
    """
    查询激活状态. action=getStatus&id=xxx
    返回 STATUS_WAIT_CODE | STATUS_OK:code | 其他
    """
    text = _get(base_url, api_key, "getStatus", id=activation_id)
    if not text:
        return None
    if text == "STATUS_WAIT_CODE":
        return {"status": "wait", "code": None}
    if text.startswith("STATUS_OK"):
        code = text.split(":", 1)[-1].strip() if ":" in text else ""
        return {"status": "ok", "code": code or None}
    return {"status": "raw", "raw": text}


def get_status_v2(base_url: str, api_key: str, activation_id: int) -> Optional[Dict[str, Any]]:
    """查询状态 V2，返回 JSON（含 sms.code）。"""
    try:
        url = (base_url or BASE_URL).strip()
        params = {"action": "getStatusV2", "api_key": api_key, "id": activation_id}
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            sms = data.get("sms") or {}
            code = sms.get("code") or (data.get("call") or {}).get("code")
            return {"status": "ok" if code else "wait", "code": code, "data": data}
    except Exception:
        pass
    return None


def set_status(base_url: str, api_key: str, activation_id: int, status: int) -> bool:
    """
    设置激活状态. action=setStatus&id=xxx&status=n
    status: 1=已发短信(准备收码) 3=请求重发 6=完成 8=取消退款
    """
    text = _get(base_url, api_key, "setStatus", id=activation_id, status=status)
    return text is not None and ("ACCESS" in (text or "") or text == "OK")


def get_countries(base_url: str, api_key: str) -> List[Dict[str, Any]]:
    """国家列表。文档: action=getCountries。返回国家列表（含 id 等）。"""
    try:
        url = (base_url or BASE_URL).strip()
        params = {"action": "getCountries", "api_key": api_key}
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def get_services_list(base_url: str, api_key: str, country: int = 0, lang: str = "cn") -> List[Dict[str, Any]]:
    """服务列表。文档: action=getServicesList&country=n&lang=cn。返回 services 数组。"""
    try:
        url = (base_url or BASE_URL).strip()
        params = {"action": "getServicesList", "api_key": api_key, "country": country, "lang": lang}
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "services" in data:
            return data.get("services") or []
    except Exception:
        pass
    return []


def get_prices(base_url: str, api_key: str, service: Optional[str] = None, country: Optional[int] = None) -> Any:
    """价格/库存. action=getPrices&service=xxx&country=n"""
    try:
        url = (base_url or BASE_URL).strip()
        params = {"action": "getPrices", "api_key": api_key}
        if service:
            params["service"] = service
        if country is not None:
            params["country"] = country
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None
