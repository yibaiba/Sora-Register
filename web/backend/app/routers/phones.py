from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.routers.auth import get_current_user
from app.database import get_db, init_db

router = APIRouter(prefix="/api/phones", tags=["phones"])


class PhoneCreate(BaseModel):
    phone: str = ""
    max_use_count: int = 1
    remark: str = ""


class BatchImportBody(BaseModel):
    lines: str = ""


class BatchDeleteBody(BaseModel):
    ids: list[int] = []


def _get_sms_settings():
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT key, value FROM system_settings WHERE key IN ('sms_api_url', 'sms_api_key')")
        rows = c.fetchall()
    out = {}
    for k, v in rows:
        out[k] = (v or "").strip()
    base = out.get("sms_api_url") or "https://hero-sms.com/stubs/handler_api.php"
    key = out.get("sms_api_key")
    return base, key


@router.get("")
def list_phones(username: str = Depends(get_current_user)):
    from app.services import hero_sms
    init_db()
    base, key = _get_sms_settings()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, activation_id FROM phone_numbers WHERE expired_at IS NOT NULL AND expired_at < datetime('now')"
        )
        expired_rows = c.fetchall()
        for row in expired_rows:
            aid = row[1]
            if aid is not None and key:
                try:
                    hero_sms.set_status(base, key, aid, 8)
                except Exception:
                    pass
        c.execute(
            "DELETE FROM phone_numbers WHERE expired_at IS NOT NULL AND expired_at < datetime('now')"
        )
        c.execute(
            "SELECT id, phone, activation_id, max_use_count, used_count, remark, expired_at, created_at FROM phone_numbers ORDER BY id DESC"
        )
        rows = c.fetchall()
    return {
        "items": [
            {
                "id": r[0], "phone": r[1], "activation_id": r[2], "max_use_count": r[3],
                "used_count": r[4], "remark": r[5], "expired_at": r[6], "created_at": r[7]
            }
            for r in rows
        ]
    }


@router.post("")
def create_phone(body: PhoneCreate, username: str = Depends(get_current_user)):
    init_db()
    if not (body.phone or "").strip():
        raise HTTPException(status_code=400, detail="手机号不能为空")
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO phone_numbers (phone, max_use_count, remark) VALUES (?, ?, ?)",
            (body.phone.strip(), body.max_use_count, body.remark)
        )
        lid = c.lastrowid
    return {"ok": True, "id": lid}


@router.delete("/{id}")
def delete_phone(id: int, username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM phone_numbers WHERE id = ?", (id,))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@router.get("/{id}/sms-code")
def get_phone_sms_code(id: int, username: str = Depends(get_current_user)):
    """查询该号码的短信验证码（接码平台 getStatusV2）"""
    from app.services import hero_sms
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT activation_id FROM phone_numbers WHERE id = ?", (id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        activation_id = row[0]
    if activation_id is None:
        raise HTTPException(status_code=400, detail="该号码无 activation_id，无法查码")
    base, key = _get_sms_settings()
    if not key:
        raise HTTPException(status_code=400, detail="请先配置手机号接码 API KEY")
    out = hero_sms.get_status_v2(base, key, activation_id)
    if not out:
        return {"status": "error", "code": None, "message": "请求失败"}
    return {"status": out.get("status", "wait"), "code": out.get("code"), "message": "已收到验证码" if out.get("code") else "等待短信中"}


@router.post("/{id}/release")
def release_phone(id: int, username: str = Depends(get_current_user)):
    """销毁：通知接码平台取消该号码（setStatus=8）并从列表删除"""
    from app.services import hero_sms
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT activation_id FROM phone_numbers WHERE id = ?", (id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        activation_id = row[0]
    base, key = _get_sms_settings()
    if activation_id is not None and key:
        hero_sms.set_status(base, key, activation_id, 8)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM phone_numbers WHERE id = ?", (id,))
    return {"ok": True}


@router.post("/batch-import")
def batch_import(body: BatchImportBody, username: str = Depends(get_current_user)):
    """每行一个手机号，max_use_count 从系统设置 phone_bind_limit 读取"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM system_settings WHERE key = ?", ("phone_bind_limit",))
        row = c.fetchone()
        limit = int(row[0]) if row and row[0] else 1
    added = 0
    with get_db() as conn:
        c = conn.cursor()
        for line in body.lines.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            c.execute(
                "INSERT INTO phone_numbers (phone, max_use_count, remark) VALUES (?, ?, ?)",
                (line, limit, "")
            )
            added += 1
    return {"ok": True, "added": added}


@router.post("/batch-delete")
def batch_delete(body: BatchDeleteBody, username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        for id in body.ids:
            c.execute("DELETE FROM phone_numbers WHERE id = ?", (id,))
    return {"ok": True}

