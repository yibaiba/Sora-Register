"""
邮箱 API（Hotmail007）对接：余额、库存、拉取并可选导入到邮箱表
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.routers.auth import get_current_user
from app.database import get_db, init_db
from app.services.hotmail007 import get_balance, get_stock, get_mail, get_first_mail, MAIL_TYPES

router = APIRouter(prefix="/api/email-api", tags=["email-api"])


def _get_email_api_settings():
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT key, value FROM system_settings WHERE key IN ('email_api_url', 'email_api_key', 'email_api_default_type')")
        rows = c.fetchall()
    out = {}
    for k, v in rows:
        out[k] = (v or "").strip()
    base = out.get("email_api_url") or "https://gapi.hotmail007.com"
    key = out.get("email_api_key")
    default_type = out.get("email_api_default_type") or "outlook"
    if default_type not in MAIL_TYPES:
        default_type = "outlook"
    return base, key, default_type


@router.get("/balance")
def api_balance(username: str = Depends(get_current_user)):
    """查询 Hotmail007 余额"""
    base, key, _ = _get_email_api_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先在系统设置中配置邮箱 API KEY (clientKey)")
    balance = get_balance(base, key)
    if balance is None:
        raise HTTPException(status_code=502, detail="请求余额失败，请检查 API 地址与 KEY")
    return {"balance": balance}


@router.get("/stock")
def api_stock(
    mail_type: str = Query(None, description="outlook / hotmail / hotmail Trusted / outlook Trusted"),
    username: str = Depends(get_current_user),
):
    """查询邮箱库存（不要求 KEY）"""
    base, _, _ = _get_email_api_settings()
    stock = get_stock(base, mail_type if mail_type in MAIL_TYPES else None)
    if stock is None:
        raise HTTPException(status_code=502, detail="请求库存失败")
    return {"stock": stock, "mail_type": mail_type or "全部"}


class FetchMailBody(BaseModel):
    mail_type: str = "outlook"
    quantity: int = 1
    import_to_emails: bool = True


@router.post("/fetch-mail")
def api_fetch_mail(body: FetchMailBody, username: str = Depends(get_current_user)):
    """从 Hotmail007 拉取邮箱，可选导入到邮箱管理表"""
    base, key, default_type = _get_email_api_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先在系统设置中配置邮箱 API KEY (clientKey)")
    mail_type = body.mail_type if body.mail_type in MAIL_TYPES else default_type
    quantity = max(1, min(body.quantity, 100))
    items = get_mail(base, key, quantity, mail_type)
    if not items:
        return {"count": 0, "imported": 0, "message": "未拉取到数据或请求失败"}
    imported = 0
    if body.import_to_emails:
        with get_db() as conn:
            c = conn.cursor()
            for row in items:
                c.execute(
                    "INSERT INTO emails (email, password, uuid, token, remark) VALUES (?, ?, ?, ?, ?)",
                    (
                        row["email"],
                        row["password"],
                        row.get("client_id") or "",
                        row.get("refresh_token") or "",
                        "Hotmail007",
                    ),
                )
                imported += 1
    return {"count": len(items), "imported": imported, "items": items}


@router.get("/first-mail")
def api_first_mail(
    email_id: int = Query(..., description="邮箱表主键 id"),
    folder: str = Query("inbox", description="inbox / junkemail"),
    username: str = Depends(get_current_user),
):
    """通过 Hotmail007 API 获取该邮箱最新一封邮件（收件箱）"""
    base, key, _ = _get_email_api_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先在系统设置中配置邮箱 API KEY (clientKey)")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT email, password, token, uuid FROM emails WHERE id = ?", (email_id,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="邮箱不存在")
    email, password, token, uuid = row
    account = f"{email}:{password or ''}:{token or ''}:{uuid or ''}"
    data = get_first_mail(base, key, account, folder=folder)
    if data is None:
        raise HTTPException(status_code=502, detail="未收到邮件或 API 请求失败")
    return {"mail": data}


@router.get("/mail-list")
def api_mail_list(
    email_id: int = Query(..., description="邮箱表主键 id"),
    folder: str = Query("inbox", description="inbox / junkemail"),
    username: str = Depends(get_current_user),
):
    """获取该邮箱收件箱邮件列表（当前 Hotmail007 仅支持最新一封，返回 list 长度 0 或 1）"""
    base, key, _ = _get_email_api_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先在系统设置中配置邮箱 API KEY (clientKey)")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT email, password, token, uuid FROM emails WHERE id = ?", (email_id,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="邮箱不存在")
    email, password, token, uuid = row
    account = f"{email}:{password or ''}:{token or ''}:{uuid or ''}"
    data = get_first_mail(base, key, account, folder=folder)
    list_ = [data] if (data and isinstance(data, dict) and len(data) > 0) else []
    return {"list": list_}
