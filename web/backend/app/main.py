# -*- coding: utf-8 -*-
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from fastapi import Depends
from app.config import settings
from app.database import init_db, get_db, DB_PATH
from app.routers import auth, accounts, settings as settings_router, emails, bank_cards, logs, dashboard, email_api, sms_api, phones, register as register_router, phone_bind as phone_bind_router
from app.routers.auth import get_current_user

# 不把轮询接口打进 access 日志，方便调试协议
class SkipPollPathsFilter(logging.Filter):
    _paths = ("/api/register/status", "/api/dashboard", "/api/logs", "/api/phone-bind/status")
    def filter(self, record):
        try:
            # uvicorn AccessFormatter: record.args = (client_addr, method, full_path, http_version, status_code)
            if getattr(record, "args", None) and len(record.args) >= 5:
                full_path = record.args[2]
                status_code = record.args[4]
                if status_code == 200 and isinstance(full_path, str):
                    for p in self._paths:
                        if p in full_path:
                            return False
        except Exception:
            pass
        return True

app = FastAPI(title="Sora 批量注册", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(",") if settings.cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(settings_router.router)
app.include_router(emails.router)
app.include_router(bank_cards.router)
app.include_router(logs.router)
app.include_router(dashboard.router)
app.include_router(email_api.router)
app.include_router(sms_api.router)
app.include_router(phones.router)
app.include_router(register_router.router)
app.include_router(phone_bind_router.router)


@app.on_event("startup")
def startup():
    init_db()
    # 屏蔽 status/dashboard/logs 轮询的 200 访问日志
    skip_filter = SkipPollPathsFilter()
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.addFilter(skip_filter)
    for h in uvicorn_access.handlers:
        h.addFilter(skip_filter)
    print("[Sora 批量注册] 服务已启动 http://0.0.0.0:1989", flush=True)


# 前端：protocol/web/frontend
frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
static_dir = frontend_dir / "static"


@app.get("/api/debug/db-info", tags=["debug"])
def debug_db_info(username: str = Depends(get_current_user)):
    """返回当前后端使用的数据目录与 accounts 条数，用于核对「账号管理」是否与注册写入同库。"""
    init_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM accounts")
        n = c.fetchone()[0]
    return {"data_dir": settings.data_dir, "db_path": DB_PATH, "accounts_count": n}


@app.get("/")
def index():
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Protocol Admin API", "docs": "/docs"}


if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
