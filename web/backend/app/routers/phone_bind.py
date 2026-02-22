# -*- coding: utf-8 -*-
"""
开始绑定手机：POST /api/phone-bind/start 启动任务，GET /api/phone-bind/status 查状态，POST /api/phone-bind/stop 停止。
"""
import threading
from datetime import datetime

from fastapi import APIRouter, Depends

from app.routers.auth import get_current_user
from app.services.phone_bind_runner import (
    set_phone_bind_stop,
    set_phone_bind_task_started,
    get_phone_bind_status,
    run_phone_bind_loop,
    _log,
)

router = APIRouter(prefix="/api/phone-bind", tags=["phone-bind"])


def _run_bind_task(task_id: str, max_count: int = None):
    try:
        run_phone_bind_loop(task_id, max_count=max_count)
    except Exception as e:
        _log(task_id, "error", f"绑定任务异常: {e}")


@router.post("/start")
def start_phone_bind(
    max_count: int = None,
    username: str = Depends(get_current_user),
):
    """启动绑定手机任务：从账号管理取未绑账号，从手机号管理取可用号码，逐个绑定。max_count 可选，不传则处理到无数据为止。"""
    task_id = f"phone_bind_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    if not set_phone_bind_task_started(task_id):
        st = get_phone_bind_status()
        return {"ok": False, "message": "绑定任务已在运行", "task_id": st.get("task_id")}
    t = threading.Thread(target=_run_bind_task, args=(task_id,), kwargs={"max_count": max_count}, daemon=True)
    t.start()
    return {"ok": True, "message": "绑定任务已启动", "task_id": task_id}


@router.get("/status")
def phone_bind_status(username: str = Depends(get_current_user)):
    """查询绑定任务状态。"""
    return get_phone_bind_status()


@router.post("/stop")
def stop_phone_bind(username: str = Depends(get_current_user)):
    """请求停止绑定任务。"""
    set_phone_bind_stop(True)
    return {"ok": True, "message": "已请求停止，当前条完成后退出"}
