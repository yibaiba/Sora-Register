from fastapi import APIRouter, Depends, Query
from app.routers.auth import get_current_user
from app.database import get_db, init_db

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def list_logs(
    username: str = Depends(get_current_user),
    task_id: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        if task_id:
            c.execute(
                "SELECT COUNT(*) FROM run_logs WHERE task_id = ?", (task_id,)
            )
        else:
            c.execute("SELECT COUNT(*) FROM run_logs")
        total = c.fetchone()[0]
        offset = (page - 1) * page_size
        if task_id:
            c.execute(
                "SELECT id, task_id, level, message, created_at FROM run_logs WHERE task_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (task_id, page_size, offset)
            )
        else:
            c.execute(
                "SELECT id, task_id, level, message, created_at FROM run_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, offset)
            )
        rows = c.fetchall()
    items = [
        {"id": r[0], "task_id": r[1], "level": r[2], "message": r[3], "created_at": r[4]}
        for r in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}
