"""
协议版批量注册入口
纯 HTTP 注册 + 可选浏览器绑卡体验 Plus。

用法:
  根目录:  python run_protocol.py [--count N] [--workers W] [--plus]
  本目录:  python run.py [--count N] [--workers W] [--plus]
"""

import argparse
import builtins
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def _progress_bar(current: int, total: int, width: int = 30, prefix: str = "") -> str:
    """生成简易进度条字符串，如 [=========>    ] 3/10"""
    if total <= 0:
        filled = 0
    else:
        filled = min(int(width * current / total), width)
    bar = "=" * filled + (">" if filled < width else "") + " " * max(0, width - filled - 1)
    return f"{prefix}[{bar}] {current}/{total}"


def _log(msg: str, flush: bool = True) -> None:
    print(msg, flush=flush)


_print_lock = threading.Lock()
# 主线程加载时保存一次真实 print，多线程里不能再用 builtins.print 赋值给 _orig_print（否则会变成 locked_print 导致死锁）
_orig_print = getattr(builtins, "print")


def _locked_print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    with _print_lock:
        _orig_print(*args, **kwargs)


def _register_one_task(do_plus: bool, index: int):
    """单任务：设置当前账号索引并执行注册，多线程时用锁包装 print 避免输出交错。返回 (index, email, password, success)。"""
    set_current_registration_index(index)
    builtins.print = _locked_print
    try:
        email, password, success = _register_one_with_plus(do_plus)
        return (index, email, password, success)
    finally:
        builtins.print = _orig_print

from config import (
    cfg,
    BATCH_INTERVAL_MIN,
    BATCH_INTERVAL_MAX,
    TOTAL_ACCOUNTS,
    EMAIL_WORKER_URL,
    set_current_registration_index,
    get_proxy_url_for_session,
)
from email_outlook import load_outlook_accounts
from utils import (
    generate_random_password,
    generate_user_info,
    save_to_txt,
    update_account_status,
)
from email_service import create_temp_email, wait_for_verification_email
from .protocol_register import register_one_protocol, activate_sora


def _register_one_with_plus(do_plus: bool):
    """
    单账号：协议注册 + 可选浏览器 Plus 试用。
    返回: (email, password, success)
    """
    email, jwt_token = create_temp_email()
    if not email or not jwt_token:
        print("[x] Failed to create temp email, skip this account")
        return None, None, False

    password = generate_random_password()
    user_info = generate_user_info()

    def get_otp():
        return wait_for_verification_email(jwt_token, email=email)

    result = register_one_protocol(email, password, jwt_token, get_otp, user_info)
    email, password = result[0], result[1]
    success = result[2]
    status_extra = result[3] if len(result) > 3 else None
    tokens = result[4] if len(result) > 4 else None
    refresh_token = (tokens.get("refresh_token") or "") if isinstance(tokens, dict) else None

    if not success:
        if status_extra == "finish_setup":
            proxy_used = get_proxy_url_for_session()
            save_to_txt(email, password, "Finish setup (check email)", proxy=proxy_used)
            print("[*] Account saved: check email for 'Finish account setup' or try login with this email/password", flush=True)
        return email, password, False

    proxy_used = get_proxy_url_for_session()
    save_to_txt(email, password, "Registered", proxy=proxy_used, refresh_token=refresh_token)

    if tokens:
        print("[*] Activating Sora...", flush=True)
        if activate_sora(tokens, email):
            update_account_status(email, "Registered+Sora", password, proxy=proxy_used, refresh_token=refresh_token)

    if do_plus:
        try:
            from browser import create_driver, login, subscribe_plus_trial, cancel_subscription
            driver = create_driver(headless=getattr(cfg.browser, "headless", False))
            try:
                if login(driver, email, password):
                    if subscribe_plus_trial(driver):
                        update_account_status(email, "Plus activated", password, proxy=proxy_used)
                        time.sleep(5)
                        if cancel_subscription(driver):
                            update_account_status(email, "Subscription cancelled", password, proxy=proxy_used)
                        else:
                            update_account_status(email, "Cancel failed", password, proxy=proxy_used)
                    else:
                        update_account_status(email, "Plus failed", password, proxy=proxy_used)
            finally:
                driver.quit()
        except Exception as e:
            print(f"[!] Plus flow error: {e}")
            update_account_status(email, "Plus error", password, proxy=proxy_used)

    return email, password, True


def run_batch_protocol(count: int = None, do_plus: bool = False, workers: int = 1):
    if count is None:
        count = TOTAL_ACCOUNTS
    count = max(1, count)
    workers = max(1, min(workers, count))

    backend = (getattr(cfg.email, "backend", None) or "cloudflare").strip().lower()
    if backend == "outlook":
        accounts = load_outlook_accounts()
        if not accounts:
            _log("[x] backend=outlook but no accounts loaded; set email.outlook_accounts_file with lines: email----password----uuid----token")
            return
    else:
        if not (EMAIL_WORKER_URL or "").strip():
            _log("[x] email.worker_url not set; configure config.yaml and retry")
            return

    _log("\n" + "=" * 60)
    _log(f"[*] Protocol batch registration  total: {count}  workers: {workers}" + (" (with Plus trial)" if do_plus else ""))
    _log("=" * 60 + "\n")
    _log("[!] For learning/research only; do not use for violations.\n")
    time.sleep(2)

    success_count = 0
    fail_count = 0

    if workers <= 1:
        for i in range(count):
            n = i + 1
            bar = _progress_bar(n, count, prefix="")
            _log("\n" + "#" * 60)
            _log(f"[*] Account {n}/{count}  {bar}")
            _log("#" * 60 + "\n")
            set_current_registration_index(i)
            email, password, success = _register_one_with_plus(do_plus)
            if success:
                success_count += 1
                _log(f"[ok] Account done  success: {success_count}  fail: {fail_count}")
            else:
                fail_count += 1
                _log(f"[x] Account failed  success: {success_count}  fail: {fail_count}")
            _log("-" * 40)
            if i < count - 1:
                wait_time = random.randint(BATCH_INTERVAL_MIN, BATCH_INTERVAL_MAX)
                _log(f"\n[*] Wait {wait_time}s before next account...")
                time.sleep(wait_time)
    else:
        results = [None] * count
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_register_one_task, do_plus, i): i for i in range(count)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    _, email, password, success = fut.result()
                    results[idx] = success
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                    with _print_lock:
                        _log(f"[{'ok' if success else 'x'}] Account {idx + 1}/{count}  success: {success_count}  fail: {fail_count}")
                except Exception as e:
                    results[idx] = False
                    fail_count += 1
                    with _print_lock:
                        _log(f"[x] Account {idx + 1}/{count} exception: {e}  success: {success_count}  fail: {fail_count}")

    _log("\n" + "=" * 60)
    _log("[*] Protocol batch registration finished")
    _log(f"    total: {count}  success: {success_count}  fail: {fail_count}")
    _log("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Protocol batch ChatGPT registration (optional Plus trial)")
    parser.add_argument("--count", "-n", type=int, default=None, help="Number of accounts, default from config")
    parser.add_argument("--workers", "-w", type=int, default=1, help="Concurrent workers (threads), default 1")
    parser.add_argument("--plus", "-p", action="store_true", help="After each registration, open browser for Plus trial then cancel")
    args = parser.parse_args()
    run_batch_protocol(count=args.count, do_plus=args.plus, workers=args.workers)


if __name__ == "__main__":
    main()
