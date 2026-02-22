# Sora 激活与手机号绑定 - 参考 genz27/sora-phone-bind 的实现分析

参考仓库：https://github.com/genz27/sora-phone-bind

## 一、sora-phone-bind 核心接口（来自其 main.py，可直接对照源码）

### 1. RT 转 AT

- **URL**: `POST https://auth.openai.com/oauth/token`
- **Body** (JSON):
  - `client_id`: 默认 `app_LlGpXReQgckcGGUo2JrYvtJK`（iOS/移动端用）
  - `grant_type`: `"refresh_token"`
  - `redirect_uri`: `"com.openai.chat://auth0.openai.com/ios/com.openai.chat/callback"`
  - `refresh_token`: 我们的 RT
- **请求方式**: curl_cffi `AsyncSession`，`impersonate` 使用移动端指纹（如 `safari17_2_ios`、`safari18_0_ios`）
- **响应**: 取 `access_token`、`refresh_token`（若有新 RT 会返回）

### 2. Sora 激活（有用户名才算“激活”）

顺序如下：

| 步骤 | 方法 | URL | 说明 |
|------|------|-----|------|
| 1 | GET | `https://sora.chatgpt.com/backend/m/bootstrap` | 激活 Sora2，必须先调 |
| 2 | GET | `https://sora.chatgpt.com/backend/me` | 获取当前用户信息 |
| 3 | 若 `me` 里已有 `username` | - | 视为已激活，结束 |
| 4 | POST | `https://sora.chatgpt.com/backend/project_y/profile/username/check` | Body: `{"username": "user_xxxxxxxx"}`，检查是否可用 |
| 5 | POST | `https://sora.chatgpt.com/backend/project_y/profile/username/set` | Body: `{"username": "user_xxxxxxxx"}`，设置用户名 |

- **请求头**（与仓库一致）:
  - `Origin: https://sora.chatgpt.com`
  - `Referer: https://sora.chatgpt.com/`
  - `Authorization: Bearer {access_token}`
  - `Content-Type: application/json`
  - 移动端 UA（如 iPhone Safari / Chrome iOS），`Sec-Ch-Ua-Mobile: ?1`，`Sec-Ch-Ua-Platform: "iOS"`
- **请求方式**: 全部用 curl_cffi，`impersonate` 用移动端（如 `safari17_2_ios`），避免 403/404 因桌面端指纹被拦。

### 3. 手机号绑定

| 步骤 | 方法 | URL | Body |
|------|------|-----|------|
| 1 | POST | `https://sora.chatgpt.com/backend/project_y/phone_number/enroll/start` | `{"phone_number": "+1xxxxxxxxxx", "verification_expiry_window_ms": null}` |
| 2 | （轮询接码平台 API 获取短信验证码） | - | - |
| 3 | POST | `https://sora.chatgpt.com/backend/project_y/phone_number/enroll/finish` | `{"phone_number": "+1xxxxxxxxxx", "verification_code": "123456"}` |

- 同一套请求头（Bearer AT + 移动端 UA/指纹）。
- 若响应里含 `"already verified"` / `"phone number already"` 表示该号已被占用，需换号。
- 验证码来源：接码平台提供的 API（配置格式为 `phone----api_url`，轮询 `api_url` 取 `data.code` 中 6 位数字）。

---

## 二、与我们当前实现的差异

| 项目 | 我们当前 | sora-phone-bind |
|------|----------|------------------|
| Sora 用户名设置 URL | 同：`/backend/project_y/profile/username/set` | 同 |
| 请求客户端 | requests / 桌面 Chrome 指纹 | curl_cffi + **移动端**指纹（Safari iOS 等） |
| 是否先调 bootstrap | 否 | **是**，先 GET `/backend/m/bootstrap` |
| 是否先 GET /backend/me | 否 | **是**，用于判断是否已有 username |
| 用户名生成 | 邮箱前缀 | 随机 `user_` + 8 位字母数字，且先 **check** 再 **set** |
| 手机号绑定 | 未做 | 有完整 enroll/start → 接码 → enroll/finish |

我们之前 Sora 返回 403/404 很可能与**未用移动端指纹**、**未先调 bootstrap/me** 有关；路径本身与参考项目一致。

---

## 三、在我们项目中的实现建议

### 阶段 1：对齐 Sora 激活（先能稳定 200）

1. **请求方式**
   - 所有发往 `sora.chatgpt.com` 的请求改为 **curl_cffi**，`impersonate` 使用移动端（如 `safari17_2_ios`），与 sora-phone-bind 一致。

2. **调用顺序**
   - 先 GET `https://sora.chatgpt.com/backend/m/bootstrap`（激活 Sora2）；
   - 再 GET `https://sora.chatgpt.com/backend/me`；
   - 若 `me.username` 已存在，直接视为激活成功；
   - 若无：生成随机 `user_xxxxxxxx`，先 POST `profile/username/check`，可用再 POST `profile/username/set`。

3. **请求头**
   - 使用与 sora-phone-bind 相同的 Origin / Referer / Sec-Ch-Ua-Mobile / Sec-Ch-Ua-Platform 等（见其 `HEADERS` + 移动端 UA）。

4. **配置**
   - 保持当前 `SORA_USERNAME_SET_URL` 为 `/backend/project_y/profile/username/set`，仅改调用顺序与指纹，不猜其它路径。

### 阶段 2：手机号绑定（独立流程，可选）

1. **数据与配置**
   - 接码平台配置：支持“手机号 + 获取验证码的 API URL”（可参考 sora-phone-bind 的 `phone----api_url` 或我们自己的表结构）。
   - 账号表可增加字段：如 `phone_bound`（是否已绑）、`phone`（脱敏存储可选）。

2. **流程**
   - 输入：当前账号的 AT（或从 RT 用上述 client_id + redirect_uri 换 AT）。
   - 从池中取一个手机号，POST `phone_number/enroll/start`；
   - 轮询接码 API 取 6 位验证码（与 sora-phone-bind 的 `get_code` 逻辑类似）；
   - POST `phone_number/enroll/finish` 提交验证码；
   - 若返回“手机号已被使用”，换号重试或标记该号不可用；
   - 成功后可选：再调一次 RT 换 AT 拿新 RT 并落库（若服务端返回新 RT）。

3. **与注册流程的关系**
   - 注册完成后已有 AT/RT，先做**阶段 1 的 Sora 激活**；
   - 手机号绑定可作为**单独任务/接口**（例如“对某账号或某批账号执行绑手机”），不必和注册强绑在同一请求里，便于接码池、重试、限流管理。

---

## 四、建议落地顺序

1. **先改 Sora 激活**：在 `protocol_register.py`（或独立 sora 模块）里，按上面顺序实现 bootstrap → me → check → set，且全部用 curl_cffi 移动端指纹；观察是否仍 403/404。
2. **再做手机号绑定**：新增“绑手机”服务/接口，配置接码源，实现 enroll/start → 轮询 code → enroll/finish；数据库与前端按需加字段和入口。
3. **RT 转 AT**：若我们已有用 web client_id 的换 token 逻辑，可保留；若需要与 sora-phone-bind 完全一致（例如为绑手机专门用移动端 client_id 换 AT），可增加一条分支使用其 `client_id` 与 `redirect_uri`。

以上接口与顺序均来自 [genz27/sora-phone-bind](https://github.com/genz27/sora-phone-bind) 的 main.py，可作为抓包/查资料之外的可靠实现参考。

---

## 五、本项目的「开始绑定手机」功能说明（实现文档补充）

「开始绑定手机」即前端「手机号管理」页的 **开始绑定手机** 按钮所触发的批量任务：从**账号管理**读取待绑定账号，从**手机号管理**读取可用号码，使用**系统设置**里已配置的手机号接码 API 取验证码，依次完成 Sora 激活（若未激活）+ 调用 Sora 的 enroll/start、轮询验证码、enroll/finish，并回写账号与手机号状态。

### 5.1 数据来源

| 来源 | 表/接口 | 筛选与说明 |
|------|---------|------------|
| **账号** | `accounts`（账号管理） | `phone_bound = 0` 且 `(refresh_token IS NOT NULL OR access_token IS NOT NULL)`；按需排序（如 id 或 registered_at）。 |
| **手机号** | `phone_numbers`（手机号管理） | `used_count < max_use_count` 且 `activation_id IS NOT NULL`。来源：① 在「手机号管理」点「获取 OpenAI 号码」调用 `/api/sms-api/get-numbers` 写入；② **绑定任务执行时若表内无可用号码，会自动调接码 API 拉取**（与 get-numbers 同逻辑：hero_sms.get_number/get_number_v2，写入 phone_numbers 后继续绑定）；③ 或手动添加（无 activation_id 则无法自动取码）。 |
| **接码配置** | 系统设置 | 已存在：`sms_api_url`、`sms_api_key`、`sms_openai_service`、`sms_max_price`。取验证码方式：现有接口 `GET /api/phones/{id}/sms-code`（内部调 `hero_sms.get_status_v2(base, key, activation_id)`），或后台任务内直接调 `hero_sms.get_status_v2`。 |

与 sora-phone-bind 的差异：他们用「每行 phone----api_url」配置取码 URL；我们用「系统设置 sms_api_* + phone_numbers.activation_id」+ Hero-SMS 兼容协议（getStatusV2），无需 per-phone 的 api_url。

### 5.2 单条绑定流程（与参考项目对齐）

对「一条账号 + 一个手机号」执行：

1. **拿 AT**  
   若账号无有效 `access_token`：用 `refresh_token` 调 `POST https://auth.openai.com/oauth/token`（body：client_id、grant_type=refresh_token、redirect_uri、refresh_token）；可用 sora-phone-bind 的移动端 client_id/redirect_uri 或现有 web 配置；得到 AT（及可选新 RT 落库）。

2. **Sora 激活**（若尚未激活）  
   顺序：GET `backend/m/bootstrap` → GET `backend/me`；若 `me.username` 已存在则跳过；否则随机 `user_xxxxxxxx`，POST `profile/username/check` → POST `profile/username/set`。请求均用 curl_cffi 移动端指纹发往 sora.chatgpt.com。

3. **发验证码**  
   `POST https://sora.chatgpt.com/backend/project_y/phone_number/enroll/start`，Body：`{"phone_number": "<当前手机号>", "verification_expiry_window_ms": null}`。若响应含 "already verified" / "phone number already"：标记该手机号不可用并换号或跳过。

4. **轮询验证码**  
   循环调用 `hero_sms.get_status_v2(base, key, activation_id)`（或等价地请求 `GET /api/phones/{id}/sms-code`），从返回中解析 6 位数字；超时或任务取消则结束本条。

5. **提交验证码**  
   `POST .../phone_number/enroll/finish`，Body：`{"phone_number": "<当前手机号>", "verification_code": "<6位码>"}`。

6. **落库**  
   - `accounts`：该账号 `phone_bound = 1`，可选写 `phone`（脱敏）；若换 token 返回了新 RT 则更新 `refresh_token`。  
   - `phone_numbers`：该行 `used_count = used_count + 1`。  
   - `run_logs`：写入本条绑定结果（成功/失败原因）。

### 5.3 任务形态建议

- **触发**：前端「开始绑定手机」→ 调用后端 `POST /api/phone-bind/start`（可选参数：最大条数、是否仅未绑账号等），返回 `task_id`。
- **执行**：后台异步任务队列；每次取一条「待绑定账号」+ 一条「可用手机号」，执行上述 5.2 流程；写 run_logs；支持暂停/停止（可复用现有注册任务的 stop 机制或单独 `phone_bind_stop` 状态）。
- **进度与结果**：通过 `run_logs` 按 `task_id` 查询；或提供 `GET /api/phone-bind/status?task_id=xxx` 返回已处理数、成功数、失败数、当前状态。

### 5.4 实现检查清单

- [x] 后端：`POST /api/phone-bind/start` 创建任务，从 accounts 筛 `phone_bound=0` 且有 RT/AT，从 phone_numbers 筛可用且带 activation_id，入队执行。
- [x] 后端：单条逻辑内实现 RT→AT、Sora 激活（bootstrap→me→check→set）、enroll/start、轮询 hero_sms.get_status_v2 取码、enroll/finish、更新 accounts 与 phone_numbers 及 run_logs。
- [x] 后端：Sora 相关请求统一用 curl_cffi 移动端指纹（与阶段 1 一致）。
- [x] 前端：「开始绑定手机」按钮改为请求 `POST /api/phone-bind/start`，并展示任务状态/日志（toast 展示 task_id，刷新仪表盘与日志；「停止绑定」调用 `POST /api/phone-bind/stop`；仪表盘加载时根据 `GET /api/phone-bind/status` 显示/隐藏停止按钮）。
- [x] 配置：接码已用系统设置中的 sms_api_*，无需新增；账号与手机号均从现有「账号管理」「手机号管理」读取。
