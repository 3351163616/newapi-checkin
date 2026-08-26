# New API Balance Manager

批量管理 [new-api](https://github.com/QuantumNous/new-api) 系站点的账号：余额查询、每日签到、用量记录、API 密钥管理，自带 Web UI。

单文件 FastAPI 后端 + 单文件前端，零构建步骤，离线测试全假数据驱动。

## 功能

- **余额查询**：批量并发查询各站点账号余额与已用量，Chrome TLS 指纹（curl_cffi）访问
- **每日签到**：定时快照、自动签到、签到状态面板；对开启了 Turnstile 的站点提供浏览器脚本签到
- **用量记录**：每日 0 点快照写入 `daily_usage.json`，保留 90 天，前端展示今日用量与历史
- **API 密钥管理**：列出 / 新建 / 删除各账号的 API Key（new-api「令牌」），完整密钥展示与批量复制，结果落缓存
- **监控告警**：可配置间隔检查余额，低于阈值 SMTP 邮件告警（去重）
- **站点管理**：跑 new-api 的站点在 Web UI 填个域名即可接入，后端零改动

### 支持的账号类型

| 类型 | 认证方式 | 说明 |
|---|---|---|
| 通用 new-api 站点 | access_token | 填域名即接入（GoRouter / TaBiAI 等同构站点） |
| AgentRouter | 用户名 + 密码 | 仅支持账密登录的站点；登录即签到 |
| 带阿里云 WAF 的站点 | access_token / session cookie | 自动求解 `acw_sc__v2` 挑战（需本地代理） |

## 快速开始

```bash
# 依赖
uv sync

# 配置（可选：不配置则首次启动自动生成随机密码并写入 .env）
cp .env.example .env

# 启动
python balance_server.py
# 或
uvicorn balance_server:app --host 0.0.0.0 --port 8003
```

打开 `http://127.0.0.1:8003`，用 `.env` 里的账号登录，在「账号管理」里添加账号即可。

## 配置（.env / 环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_USERNAME` | `admin` | Web UI 登录用户名 |
| `AUTH_PASSWORD` | 自动生成 | Web UI 登录密码；未设置时首次启动生成随机密码写回 `.env` 并打印 |
| `HTTPS_PROXY` / `HTTP_PROXY` | `http://127.0.0.1:7890` | 访问需代理站点的出口 |
| `MIHOMO_GROUP` | 空 | mihomo 代理分组名，设置后启用出口轮换（避开按出口 IP 的限流） |
| `MIHOMO_CONFIG` | `~/mihomo/config.yaml` | mihomo 配置路径（读 controller 地址与 secret） |

环境变量优先于 `.env`。

## 运行时数据（都在本地，不入库）

| 文件 | 内容 |
|---|---|
| `new_accounts_config.json` | 账号列表（access_token 方式） |
| `saved_config.json` | 账号列表（cookie 方式）+ 邮箱 + 监控配置 |
| `<站点>_accounts.json` | 各 new-api 站点账号 |
| `agentrouter_sessions.json` | 登录 session 缓存 |
| `daily_usage.json` | 每日用量快照（90 天） |
| `keys_cache.json` | API 密钥列表缓存 |
| `newapi_sites.json` / `checkin_settings.json` / `*_checkin_state.json` | 站点清单 / 签到设置 / 签到状态 |

## 测试

```bash
.venv/bin/python -m pytest -q        # 后端，全假数据驱动，不发上游请求
node tests/test_site_frontend.mjs    # 前端
```

## 说明

- 接口行为参考 [docs/API_REFERENCE.md](docs/API_REFERENCE.md)（基于 new-api `quota-currency-unit` 分支整理）
- 本项目与上述任何站点无关，仅供个人学习研究。请遵守目标站点的服务条款，滥用（批量注册、绕过风控牟利等）产生的后果由使用者自负

## License

[MIT](LICENSE)
