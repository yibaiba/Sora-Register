from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import auth, accounts, settings as settings_router, emails, bank_cards, logs, dashboard, email_api, sms_api, phones

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


@app.on_event("startup")
def startup():
    init_db()


# 前端：protocol/web/frontend
frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
static_dir = frontend_dir / "static"


@app.get("/")
def index():
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Protocol Admin API", "docs": "/docs"}


if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
