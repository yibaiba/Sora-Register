"""
界面版启动入口：将 protocol 与 web/backend 加入路径后启动 uvicorn。
在 protocol 目录执行: python web/run_web.py
或在 protocol/web 目录执行: python run_web.py
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
backend = root / "backend"
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=1989, reload=True)
