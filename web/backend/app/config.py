# 界面版配置：从环境变量或默认值加载，登录账号密码在此设置
import os
from pathlib import Path

def _str(key: str, default: str) -> str:
    return os.environ.get(key, default).strip() or default

# 默认 data 目录在 protocol/data（即 web 的上级的 data）
_default_data_dir = str(Path(__file__).resolve().parent.parent.parent.parent / "data")

class Settings:
    admin_username: str = _str("ADMIN_USERNAME", "admin")
    admin_password: str = _str("ADMIN_PASSWORD", "admin123")
    secret_key: str = _str("SECRET_KEY", "change-me-in-production")
    data_dir: str = _str("DATA_DIR", _default_data_dir)
    cors_origins: str = _str("CORS_ORIGINS", "*")

settings = Settings()
