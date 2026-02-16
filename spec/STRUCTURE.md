# 协议版目录与结构规范

## 1. 目录结构

```
protocol/
├── README.md
├── run.py                    # 命令行入口（批量注册）
├── main_protocol.py          # 批量注册调度逻辑
├── protocol_register.py      # 8 步协议注册核心
├── __init__.py
├── mail.txt                  # Outlook 邮箱列表（可选，由配置指定路径）
│
├── spec/                     # 规范与文档
│   ├── STRUCTURE.md          # 本文件：目录与结构规范
│   └── CONFIG_AND_DEPLOY.md  # 配置与部署说明
│
├── scripts/                  # 辅助脚本；根目录下同名脚本已迁移至此，调用：python -m protocol.scripts.xxx
│   ├── check_inbox.py        # 查询 Outlook 收件
│   ├── get_outlook_refresh_token.py
│   ├── analyze_har.py
│   └── test_fetch_api.py
│
├── web/                      # 界面版（后台管理 + 容器部署）
│   ├── backend/              # 后端 API
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   ├── routers/
│   │   │   ├── models/
│   │   │   └── services/
│   │   └── requirements.txt
│   ├── frontend/             # 前端静态资源与模板
│   │   ├── static/
│   │   └── templates/
│   ├── Dockerfile
│   └── docker-compose.yml
│
└── data/                     # 界面版运行时数据（可挂载为卷）
    ├── admin.db              # SQLite（账号/邮箱/银行卡/设置/日志）
    └── logs/                 # 运行日志文件（可选）
```

- **项目根目录**：指 `gptauto`（即 protocol 的上级）。配置文件 `config.yaml`、依赖安装在根目录；protocol 为子包。
- **界面版**：独立于命令行 `run.py`，通过 `web/` 提供后台管理；可单独容器化部署。

## 2. 界面版功能模块

| 模块       | 说明 |
|------------|------|
| 登录       | 配置项设置初始账号/密码（ADMIN_USERNAME / ADMIN_PASSWORD）；首次部署后可在系统设置中修改。 |
| 运行日志   | 查看运行日志列表，按任务 ID 筛选、分页、刷新。 |
| 账号管理   | 注册结果列表：邮箱、密码、状态、是否开通 SORA/PLUS、是否绑定手机号；筛选、分页、批量导出 CSV。 |
| 邮箱管理   | 邮箱账号列表（如 Outlook）的增删改查、批量导入（格式：邮箱----密码----UUID----Token）。 |
| 银行卡管理 | 银行卡列表；无银行卡 API 时从此处批量导入/删除；每条卡支持“使用次数上限/已用次数”。 |
| 系统设置   | 手机号接码 API、线程数、代理 IP、银行卡 API 地址、每卡使用次数上限；可修改登录账号与密码。 |

## 3. 配置与登录

- 登录界面账号/密码来自 **配置**（如 `config.yaml` 或环境变量），需包含：
  - `admin.username` / `admin.password`（或 `ADMIN_USERNAME` / `ADMIN_PASSWORD`）
- 初始部署时在配置中设置一次，后续可在“系统设置”中修改（若实现该能力）。

## 4. 部署方式

- **本地/服务器**：在项目根目录安装依赖后，可启动 `web/backend` 服务，前端通过同一服务或 Nginx 提供。
- **容器**：使用 `web/Dockerfile` 与 `web/docker-compose.yml`，支持挂载 `data/` 与外部 `config.yaml`，详见 spec/CONFIG_AND_DEPLOY.md。

## 5. 与现有命令行关系

- `run.py`、`main_protocol.py`、`protocol_register.py` 保持原位，供命令行或 CI 调用。
- 界面版通过“任务”触发注册时，可复用 `main_protocol` 的注册逻辑（需将根目录加入 `sys.path` 或通过子进程调用），运行日志写入数据库/文件并在后台展示。
