"""
手机号接码 API（Hero-SMS / SMS-Activate 兼容）
文档: https://hero-sms.com/cn/api
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from jose import JWTError, jwt
from app.config import settings
from app.routers.auth import get_current_user
from app.database import get_db, init_db
from app.services import hero_sms

# 接码平台未返回到期时间时，默认有效期（分钟）
PHONE_DEFAULT_EXPIRE_MINUTES = 20

router = APIRouter(prefix="/api/sms-api", tags=["sms-api"])
OPENAI_SERVICE = "openai"


def _get_sms_settings():
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT key, value FROM system_settings WHERE key IN ('sms_api_url', 'sms_api_key', 'sms_openai_service', 'sms_max_price')"
        )
        rows = c.fetchall()
    out = {}
    for k, v in rows:
        out[k] = (v or "").strip()
    base = out.get("sms_api_url") or "https://hero-sms.com/stubs/handler_api.php"
    key = out.get("sms_api_key")
    openai_service = (out.get("sms_openai_service") or "").strip() or "dr"
    try:
        max_price = float(out.get("sms_max_price") or "0.55")
    except (TypeError, ValueError):
        max_price = 0.55
    return base, key, openai_service, max_price


@router.get("/balance")
def api_balance(username: str = Depends(get_current_user)):
    """查询 Hero-SMS 余额"""
    base, key, _, _ = _get_sms_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先在系统设置中配置手机号接码 API KEY")
    balance = hero_sms.get_balance(base, key)
    if balance is None:
        raise HTTPException(status_code=502, detail="请求余额失败，请检查 API 地址与 KEY")
    return {"balance": balance}


@router.get("/countries")
def api_countries(username: str = Depends(get_current_user)):
    """国家列表"""
    base, key, _, _ = _get_sms_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先配置手机号接码 API KEY")
    data = hero_sms.get_countries(base, key)
    return {"countries": data}


@router.get("/services")
def api_services(
    country: int = Query(0, description="国家 ID"),
    username: str = Depends(get_current_user),
):
    """服务列表（如 openai 等）"""
    base, key, _, _ = _get_sms_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先配置手机号接码 API KEY")
    data = hero_sms.get_services_list(base, key, country=country, lang="cn")
    return {"services": data}


@router.get("/prices")
def api_prices(
    service: str = Query(None),
    country: int = Query(None),
    username: str = Depends(get_current_user),
):
    """价格/库存"""
    base, key, _, _ = _get_sms_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先配置手机号接码 API KEY")
    data = hero_sms.get_prices(base, key, service=service, country=country)
    return data if data is not None else {}


def _collect_service_keys(prices) -> list:
    """从 getPrices 全量返回中收集所有服务代号（用于 service is incorrect 时提示）."""
    keys = []
    if isinstance(prices, dict) and prices.get("status") != "false":
        for country_id, val in prices.items():
            if isinstance(val, dict):
                for k, v in val.items():
                    if isinstance(v, dict) and (v.get("count") is not None or v.get("cost") is not None):
                        keys.append(k)
    return list(dict.fromkeys(keys))


def _parse_prices_to_count(prices, service_name: str) -> tuple:
    """从 getPrices 返回中解析指定服务的数量，兼容 list 或 dict。返回 (total_count, by_country)."""
    total_count = 0
    by_country = []

    def add_info(country_id, info):
        nonlocal total_count
        if not isinstance(info, dict):
            return
        c = info.get("count") or info.get("physicalCount") or 0
        try:
            n = int(c)
        except (TypeError, ValueError):
            return
        total_count += n
        by_country.append({"country": country_id, "count": n, "cost": info.get("cost")})

    if isinstance(prices, dict) and prices and set(prices.keys()) <= {"prices", "data", "result"}:
        inner = prices.get("prices") or prices.get("data") or prices.get("result")
        if inner is not None:
            prices = inner

    if isinstance(prices, list):
        for item in prices:
            if not isinstance(item, dict):
                continue
            for country_id, val in item.items():
                if not isinstance(val, dict):
                    continue
                if service_name in val:
                    add_info(country_id, val[service_name])
                else:
                    add_info(country_id, val)
    elif isinstance(prices, dict):
        if service_name in prices and isinstance(prices[service_name], dict):
            for country_id, info in prices[service_name].items():
                add_info(country_id, info)
        else:
            for country_id, val in prices.items():
                if not isinstance(val, dict):
                    continue
                if service_name in val:
                    add_info(country_id, val[service_name])
                else:
                    add_info(country_id, val)
    return total_count, by_country


def _openai_availability_auth(request: Request):
    """debug=1 时一律放行；否则要求 Authorization: Bearer <token>。"""
    if request.query_params.get("debug") == "1":
        return
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/openai-availability")
def api_openai_availability(
    request: Request,
    debug: int = Query(0, description="1 时返回 getPrices 原始数据，便于排查数量为 0"),
):
    """OpenAI 可用数量汇总：余额 + 各国家库存。debug=1 时免登录访问。"""
    _openai_availability_auth(request)
    base, key, openai_service, _ = _get_sms_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先配置手机号接码 API KEY")
    balance = hero_sms.get_balance(base, key)
    prices = hero_sms.get_prices(base, key, service=openai_service)
    service_hint = []
    if isinstance(prices, dict) and prices.get("status") == "false" and "incorrect" in (prices.get("msg") or ""):
        full_prices = hero_sms.get_prices(base, key)
        if isinstance(full_prices, dict) and full_prices.get("status") != "false":
            service_hint = _collect_service_keys(full_prices)
        elif isinstance(full_prices, list):
            for item in full_prices:
                if isinstance(item, dict):
                    for val in item.values():
                        if isinstance(val, dict):
                            service_hint.extend(k for k in val if isinstance(val.get(k), dict))
            service_hint = list(dict.fromkeys(service_hint))
    total_count, by_country = _parse_prices_to_count(prices, openai_service) if prices and not service_hint else (0, [])
    out = {
        "balance": balance if balance is not None else 0,
        "total_count": total_count,
        "by_country": by_country,
    }
    if service_hint:
        out["service_hint"] = service_hint
    if debug:
        out["prices_raw"] = prices
    return out


class GetNumbersBody(BaseModel):
    service: str = "openai"
    country: int = 0
    quantity: int = 1


@router.post("/get-numbers")
def api_get_numbers(body: GetNumbersBody, username: str = Depends(get_current_user)):
    """从接码平台获取号码并写入手机号管理表（可绑定次数取自系统设置）"""
    base, key, openai_service, max_price = _get_sms_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先配置手机号接码 API KEY")
    quantity = max(1, min(body.quantity, 20))
    service = (body.service or "").strip()
    if not service or service == "openai":
        service = openai_service
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM system_settings WHERE key = ?", ("phone_bind_limit",))
        row = c.fetchone()
        limit = int(row[0]) if row and row[0] else 1
    got = []
    errors = []
    for _ in range(quantity):
        result = hero_sms.get_number(base, key, service, body.country, max_price=max_price)
        if result and result.get("error"):
            result = hero_sms.get_number_v2(base, key, service, body.country, max_price=max_price)
        if not result:
            break
        if result.get("error"):
            errors.append(result["error"])
            break
        expired_at = result.get("expired_at")
        if not (expired_at and str(expired_at).strip()):
            default_end = (datetime.utcnow() + timedelta(minutes=PHONE_DEFAULT_EXPIRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
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
                (result["phone_number"], result["activation_id"], limit, "Hero-SMS", expired_at),
            )
            got.append({"id": c.lastrowid, "phone": result["phone_number"], "activation_id": result["activation_id"]})
    out = {"got": len(got), "items": got}
    if errors:
        out["errors"] = errors
    return out
