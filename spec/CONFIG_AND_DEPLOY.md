# 配置与部署规范

## 1. 界面版配置项

界面版除复用项目根目录 `config.yaml` 外，可单独使用以下配置（环境变量或 `web/backend/app/config.yaml`）：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| 登录 | 后台初始账号/密码 | `ADMIN_USERNAME` / `ADMIN_PASSWORD` 或 config `admin.username` / `admin.password` |
| 手机号接码 API | 系统设置中配置的接码 API 地址 | 由系统设置存储 |
| 线程数 | 注册任务并发数 | 系统设置存储 |
| 代理 IP | 单代理 URL 或批量代理配置 | 系统设置存储 |
| 银行卡 API | 若有则调用；无则使用银行卡管理中的列表 | 系统设置存储 |
| 每卡使用次数 | 每张银行卡最多使用次数 | 系统设置存储 |

## 2. 容器部署

- **镜像**：`web/Dockerfile` 基于 Python 3.10+，安装 backend 依赖，暴露端口 1989。
- **编排**：`web/docker-compose.yml` 定义服务、挂载 `data/` 持久化、挂载项目根目录或配置文件以便读取 `config.yaml`。
- **初始账号密码**：通过环境变量 `ADMIN_USERNAME`、`ADMIN_PASSWORD` 传入，或挂载的 config 中设置。

## 3. 数据持久化

- SQLite 数据库文件建议放在 `data/admin.db`，容器内挂载为卷。
- 运行日志可写入 `data/logs/` 或数据库表，供后台“运行日志”页查询。
