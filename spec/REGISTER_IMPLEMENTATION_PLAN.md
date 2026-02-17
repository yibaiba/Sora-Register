# 开启注册 - 实现流程

本文档约定实现步骤、模块边界与注意事项，避免重复代码与影响现有功能。

---

## 1. 原则

- **不重复**：注册 8 步与 Sora 激活逻辑只存在于 `protocol_register.py`，Web 端只做「取数、配置、调度、落库」。
- **不影响**：命令行 `run.py` / `main_protocol.py` 行为不变；`protocol_register.py` 仅做**可选**入参扩展（未传时行为与现有一致）。
- **可回滚**：新增代码集中在 `web/backend/app/` 下新模块与路由，必要时可关掉路由或开关禁用。

---

## 2. 依赖关系

- `protocol_register.py` 依赖：`config`（get_proxy_url_random、get_proxy_url_for_session、cfg、HTTP_TIMEOUT）、`utils`（get_user_agent）。
- 命令行场景：由 `run.py` 将上级目录加入 path，使用 gptauto 的 config/utils。
- Web 场景：无 gptauto 目录时，需在调用前注入「可被 import 的 config/utils」，供 `protocol_register` 使用。

---

## 3. 实现步骤（按顺序）

### 步骤 1：protocol_register 支持可选 proxy（不破坏现有逻辑）

- **文件**：`protocol/protocol_register.py`
- **改动**：
  - 增加 `threading.local()` 的上下文，用于当前线程的 `proxy_url`。
  - `register_one_protocol(..., proxy_url=None)` 增加可选关键字参数；若传入则写入线程上下文，并在 `finally` 中清空。
  - `_make_session()` 中：优先使用线程上下文中的 `proxy_url`，若无再调用 `get_proxy_url_random()`。
  - 内部使用 `get_proxy_url_for_session()` 的一处（password 重试）：同样优先线程上下文再调原函数。
- **校验**：未传 `proxy_url` 时行为与当前一致；命令行调用不传该参数，不受影响。

### 步骤 2：Web 用 config/utils 注入层

- **目的**：在 Web 进程内调用 `protocol_register` 时，不依赖 gptauto 的 config/utils，由 Web 提供实现。
- **新增**：`web/backend/app/registration_env.py`（或拆成 config/utils 两个桩模块）。
  - 提供与 `protocol_register` 所需同名的接口：`get_proxy_url_random`、`get_proxy_url_for_session`、`HTTP_TIMEOUT`、`cfg`（含 retry 等最小属性）、`get_user_agent`。
  - 通过线程局部变量或模块级变量保存「当前任务」的 proxy、timeout 等，由 runner 在每任务开始前写入。
- **注入时机**：仅在「执行注册任务」的线程/流程中，在 `import protocol_register` 之前，将上述实现写入 `sys.modules["config"]` 与 `sys.modules["utils"]`，再动态 import `protocol_register`（或通过单独子进程 + 注入，见下）。

- **若采用子进程**：可改为由 Web 启动子进程，子进程入口脚本内先注入 config/utils（从 Web 传入的 JSON 或 env 读配置），再 import 并执行注册逻辑；Web 与子进程通过 DB 共享数据。子进程方案可避免与 Web 主进程的 path/import 冲突，但需传 data_dir、proxy、邮箱列表等。

- **推荐**：同一进程内用「注册专用线程 + 注入」：在注册线程内先设置 `registration_env` 的 proxy 等，再 `sys.modules` 注入并 import `protocol_register`，然后调用。注意首次 import 后会被缓存，故注入需在**首次** import `protocol_register` 之前完成（例如在启动时或首次执行注册前，在单次「注册 run」开始时注入并 import）。

### 步骤 3：OTP 获取（Hotmail007 拉信）

- **不重复**：复用现有 `app.services.hotmail007.get_first_mail`（或已有拉信接口）。
- **新增**：在 `app.services` 下实现 `get_otp_for_email(base_url, client_key, account_str, timeout_sec, interval_sec)`：轮询 get_first_mail，从邮件正文/标题中解析 6 位数字验证码，超时返回 None。解析逻辑集中在此，不在 protocol_register 内写。
- **入参**：account_str 格式为 `email:password:token:uuid`，与现有 Hotmail007 约定一致。

### 步骤 4：单条任务运行器（含重试）

- **新增**：`web/backend/app/services/registration_runner.py`（或 `app/tasks/registration_job.py`）。
  - **职责**：从 DB 读一条未注册邮箱、读 system_settings（proxy、retry_count、邮箱 API 等）；构造 `get_otp_fn`（调用步骤 3）；注入 config/utils 并调用 `protocol_register.register_one_protocol(..., proxy_url=...)`；根据返回值决定是否调用 `activate_sora`；将结果写入 `accounts`、`run_logs`，更新 `last_run_success`/`last_run_fail`。
  - **重试**：单条失败时按 `retry_count`（1～5）重试，全部失败再记失败并写日志。
  - **不重复**：不实现 8 步协议，只做「取配置 → 调 protocol_register → 落库」。

### 步骤 5：任务队列与调度（定时 + 心跳）

- **队列**：总数量 = 当前未注册邮箱数（每次调度前查一次 DB：emails 中不在 accounts 的条数）。
- **调度**：使用 `ThreadPoolExecutor(max_workers=thread_count)`，从队列取未注册邮箱提交给「单条任务运行器」；取完即止，不人为指定总数。
- **定时**：可选「轮询间隔」或「任务结束后再拉一次未注册数」决定是否继续下一轮，实现无人值守。
- **心跳**：在循环或每完成一条任务时更新 `system_settings` 中某 key（如 `last_registration_heartbeat`）为当前时间；前端或监控可据此判断是否卡死。

### 步骤 6：API 与前端

- **新增路由**：例如 `POST /api/register/start`（启动一次注册任务/调度）、`GET /api/register/status`（返回 running、last_heartbeat、成功/失败数等）。路由只做鉴权与参数校验，具体逻辑调用 registration_runner。
- **前端**：「开启注册」按钮调 `POST /api/register/start`；可轮询 status 或仅 toast「已启动」并刷新仪表盘/日志；不破坏现有其他页面逻辑。

### 步骤 7：日志与统计

- **复用**：`run_logs` 表与现有写入方式；成功/失败数写入 `last_run_success`、`last_run_fail`，与仪表盘现有读取一致。
- **不新增**：不重复造日志格式，仅在新流程中调用现有落库方式。

---

## 4. 文件清单（新增/修改）

| 文件 | 操作 |
|------|------|
| `protocol/protocol_register.py` | 修改：可选 `proxy_url` + 线程上下文，行为兼容现有调用 |
| `web/backend/app/registration_env.py` | 新增：Web 用 config/utils 桩 |
| `web/backend/app/services/registration_runner.py` | 新增：单条任务 + 重试 + 落库 |
| `web/backend/app/services/otp_resolver.py` | 新增（或合并在 runner）：Hotmail007 拉信解析 OTP |
| `web/backend/app/routers/register.py` | 新增：POST start、GET status |
| `web/backend/app/main.py` | 修改：include register 路由 |
| 前端「开启注册」按钮 | 修改：请求 POST /api/register/start，可选轮询 status |

---

## 5. 测试与回滚

- **命令行**：在 protocol 目录执行 `python run.py --count 1`，确认行为与改动前一致。
- **Web 其他功能**：登录、邮箱管理、账号管理、设置等不变；仅新增「开启注册」相关接口与按钮逻辑。
- **回滚**：若出问题，可注释掉 `main.py` 中 register 路由的 include，并恢复「开启注册」按钮为 toast「功能开发中」。

---

以上为实现流程，按步骤执行并保证「不重复、不影响」即可。
