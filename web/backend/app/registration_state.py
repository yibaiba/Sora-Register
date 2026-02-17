"""注册任务停止标志，供调度线程与 worker 共享。"""
import threading

_stop_requested = False
_lock = threading.Lock()


def set_stop_requested(value: bool) -> None:
    with _lock:
        global _stop_requested
        _stop_requested = value


def is_stop_requested() -> bool:
    with _lock:
        return _stop_requested
