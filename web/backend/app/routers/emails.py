from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.routers.auth import get_current_user
from app.database import get_db, init_db

router = APIRouter(prefix="/api/emails", tags=["emails"])


class EmailCreate(BaseModel):
    email: str
    password: str = ""
    uuid: str = ""
    token: str = ""
    remark: str = ""


@router.get("")
def list_emails(username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, email, password, uuid, token, remark, created_at FROM emails ORDER BY id DESC")
        rows = c.fetchall()
        c.execute("SELECT email FROM accounts")
        registered_emails = {row[0].strip().lower() for row in c.fetchall() if row[0]}
    return {
        "items": [
            {
                "id": r[0],
                "email": r[1],
                "password": r[2],
                "uuid": r[3],
                "token": r[4],
                "remark": r[5],
                "created_at": r[6],
                "registered": (r[1] or "").strip().lower() in registered_emails,
            }
            for r in rows
        ]
    }


@router.post("")
def create_email(body: EmailCreate, username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO emails (email, password, uuid, token, remark) VALUES (?, ?, ?, ?, ?)",
            (body.email, body.password, body.uuid, body.token, body.remark)
        )
    return {"ok": True, "id": c.lastrowid}


@router.get("/export")
def export_emails(username: str = Depends(get_current_user)):
    """返回全部邮箱（含密码），用于批量导出，格式与批量导入一致"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT email, password, uuid, token, remark FROM emails ORDER BY id DESC")
        rows = c.fetchall()
    return {
        "items": [
            {"email": r[0], "password": r[1] or "", "uuid": r[2] or "", "token": r[3] or "", "remark": r[4] or ""}
            for r in rows
        ]
    }


@router.get("/{id}")
def get_email(id: int, username: str = Depends(get_current_user)):
    """获取单条邮箱详情（含密码），用于查看邮箱/登录"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, email, password, uuid, token, remark FROM emails WHERE id = ?", (id,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": row[0], "email": row[1], "password": row[2] or "", "uuid": row[3] or "", "token": row[4] or "", "remark": row[5] or ""}


@router.delete("/{id}")
def delete_email(id: int, username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM emails WHERE id = ?", (id,))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


class BatchImportBody(BaseModel):
    lines: str = ""


@router.post("/batch-import")
def batch_import(body: BatchImportBody, username: str = Depends(get_current_user)):
    """Body: {"lines": "邮箱----密码----uuid----token 多行"}"""
    init_db()
    added = 0
    with get_db() as conn:
        c = conn.cursor()
        for line in body.lines.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("----")]
            email = parts[0] if parts else ""
            if not email:
                continue
            password = parts[1] if len(parts) > 1 else ""
            uuid = parts[2] if len(parts) > 2 else ""
            token = parts[3] if len(parts) > 3 else ""
            c.execute(
                "INSERT INTO emails (email, password, uuid, token, remark) VALUES (?, ?, ?, ?, ?)",
                (email, password, uuid, token, "")
            )
            added += 1
    return {"ok": True, "added": added}
