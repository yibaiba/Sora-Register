from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.routers.auth import get_current_user
from app.database import get_db, init_db

router = APIRouter(prefix="/api/bank-cards", tags=["bank_cards"])


class BankCardCreate(BaseModel):
    card_number_masked: str = ""
    card_data: str = ""
    max_use_count: int = 1
    remark: str = ""


class BatchImportBody(BaseModel):
    lines: str = ""  # 每行一条卡信息（可仅后四位或掩码）


@router.get("")
def list_cards(username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, card_number_masked, card_data, max_use_count, used_count, remark, created_at FROM bank_cards ORDER BY id DESC"
        )
        rows = c.fetchall()
    return {
        "items": [
            {
                "id": r[0], "card_number_masked": r[1], "card_data": r[2],
                "max_use_count": r[3], "used_count": r[4], "remark": r[5], "created_at": r[6]
            }
            for r in rows
        ]
    }


@router.post("")
def create_card(body: BankCardCreate, username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO bank_cards (card_number_masked, card_data, max_use_count, remark) VALUES (?, ?, ?, ?)",
            (body.card_number_masked, body.card_data, body.max_use_count, body.remark)
        )
        lid = c.lastrowid
    return {"ok": True, "id": lid}


@router.delete("/{id}")
def delete_card(id: int, username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM bank_cards WHERE id = ?", (id,))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@router.post("/batch-import")
def batch_import(body: BatchImportBody, username: str = Depends(get_current_user)):
    """每行一条卡（如后四位或掩码），max_use_count 从系统设置读取"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM system_settings WHERE key = ?", ("card_use_limit",))
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
                "INSERT INTO bank_cards (card_number_masked, card_data, max_use_count, remark) VALUES (?, ?, ?, ?)",
                (line[:20], line, limit, "")
            )
            added += 1
    return {"ok": True, "added": added}


class BatchDeleteBody(BaseModel):
    ids: list[int] = []


@router.post("/batch-delete")
def batch_delete(body: BatchDeleteBody, username: str = Depends(get_current_user)):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        for id in body.ids:
            c.execute("DELETE FROM bank_cards WHERE id = ?", (id,))
    return {"ok": True}
