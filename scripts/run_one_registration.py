#!/usr/bin/env python3
"""
分步测试执行：在 protocol 仓库内跑单条注册（使用 Web 的 config 注入与 DB）。
用法（在 protocol 根目录执行）:
  python scripts/run_one_registration.py
  set KEEP_TRYING_UNTIL_SUCCESS=1 时持续尝试、每次随机换未注册邮箱，直到有一笔注册成功（以写入账号管理为准）。
  set PRINT_STEP_LOGS=1 时步骤日志同时打印到终端，便于排查 Step3/Step5 等。
  set PROXY_URL=... 时使用该代理，并自动将 retry_count 设为 5。
需要：protocol/data 目录存在（或设置 DATA_DIR）；邮箱管理中有至少一条未注册邮箱；
      系统设置中可配置 proxy_url、email_api_url、email_api_key 等（无则用默认）。
"""
import os
import sys
from pathlib import Path

KEEP_TRYING = os.environ.get("KEEP_TRYING_UNTIL_SUCCESS", "").strip().lower() in ("1", "true", "yes")

# 协议仓库根目录
PROTOCOL_ROOT = Path(__file__).resolve().parent.parent
WEB_BACKEND = PROTOCOL_ROOT / "web" / "backend"

def _setup_path():
    backend_str = str(WEB_BACKEND)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)
    root_str = str(PROTOCOL_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

_setup_path()

# 确保 data 目录与 DATA_DIR（app.config 用）
DATA_DIR = os.environ.get("DATA_DIR") or str(PROTOCOL_ROOT / "data")
os.environ["DATA_DIR"] = DATA_DIR
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

def main():
    from app.registration_env import inject_registration_modules
    from app.database import init_db, get_db
    from app.services.registration_runner import (
        _get_registration_settings,
        fetch_one_unregistered_email,
        run_one_with_retry,
    )

    inject_registration_modules()
    init_db()

    attempt = 0
    while True:
        attempt += 1
        with get_db() as conn:
            row = fetch_one_unregistered_email(conn, order_random=KEEP_TRYING)
        if not row:
            print("[x] 无未注册邮箱，请先在「邮箱管理」中添加并保存。", flush=True)
            sys.exit(1)

        email_id, email, password, uuid_val, token = row
        settings = _get_registration_settings()
        # 单条测试时至少重试 2 次，以触发 409 后的「无代理 + 直连 auth」路径
        try:
            n = max(2, int(settings.get("retry_count") or "1"))
            settings["retry_count"] = str(n)
        except (TypeError, ValueError):
            settings["retry_count"] = "2"
        if os.environ.get("NO_PROXY", "").strip().lower() in ("1", "true", "yes"):
            settings["proxy_url"] = ""
            if attempt == 1:
                print("[*] NO_PROXY=1，本次不使用代理", flush=True)
        elif os.environ.get("PROXY_URL"):
            settings["proxy_url"] = os.environ.get("PROXY_URL").strip()
            if attempt == 1:
                print(f"[*] 使用环境变量代理: {settings['proxy_url'][:50]}...", flush=True)
        if os.environ.get("PROXY_URL"):
            settings["retry_count"] = "5"
        task_id = f"step_test_{attempt}"
        print(f"\n[*] 第 {attempt} 轮 邮箱: {email}", flush=True)
        if KEEP_TRYING:
            print("[*] 持续尝试直到注册成功（以写入账号管理为准）\n", flush=True)

        success = run_one_with_retry(
            email_id, email, password or "", uuid_val or "", token or "",
            settings, task_id,
        )
        if success:
            try:
                from app.config import settings
                db_path = str(Path(settings.data_dir) / "admin.db")
                print("\n[ok] 注册成功，已写入账号管理。", flush=True)
                print(f"[*] 本脚本写入的数据文件: {db_path}", flush=True)
                print("[*] 若在 Web「账号管理」看不到新账号，请确认 Web 后端用相同 DATA_DIR 启动（账号管理页顶部会显示后端当前数据文件路径）。", flush=True)
            except Exception:
                print("\n[ok] 注册成功，已写入账号管理。", flush=True)
            sys.exit(0)
        if not KEEP_TRYING:
            print("\n[x] 注册未成功，请查看上方日志与 spec/REGISTER_DEV_RULE.md 排查。", flush=True)
            sys.exit(1)
        print(f"[x] 本轮未成功，更换邮箱继续尝试...", flush=True)

if __name__ == "__main__":
    main()
