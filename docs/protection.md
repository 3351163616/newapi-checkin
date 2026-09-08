# 人机校验与防护突破指南

本项目在服务器端即可应对 new-api 站点常见的三类防护，无需浏览器手动签到。本文说明各防护的工作原理、需要的配置与自托管组件的部署方法。

## 防护矩阵

| 防护 | 触发位置 | 服务器端对策 | 需要配置 |
|---|---|---|---|
| Cloudflare Turnstile | 签到/登录等 POST（new-api 的 `middleware.TurnstileCheck()`） | 打码平台代解 token，逐账号带 `?turnstile=` 签到 | 打码平台 API Key |
| CF 边缘质询（cf-mitigated: challenge） | 全站任意请求 | FlareSolverr 解出 cf_clearance，自动带上重试 | 自托管 FlareSolverr |
| 阿里云 WAF（acw_sc__v2 挑战） | 全站任意请求 | 纯算法求解 arg1 → acw_sc__v2，零成本 | 无，自动 |

请求层统一由 `newapi_request` 自动过验：撞到防护 → 按域名缓存 5 分钟 + singleflight 解一次 cookies → 原地重试（挑战页由防护层返回，请求未到源站，重试不会重复签到）；带缓存仍撞质询则判定缓存失效并强制重解；求解失败有 60 秒负缓存，避免求解器故障时整批请求反复撞它。

## Turnstile 打码平台

设置页「人机校验与防护」区块选择平台并填 API Key。支持：

- **2Captcha**（`https://api.2captcha.com`）
- **YesCaptcha**（`https://api.yescaptcha.com`）
- **CapSolver**（`https://api.capsolver.com`）
- **自定义网关**：任何兼容 2Captcha createTask 协议的服务，填 base_url 即可

三家共用 createTask + getTaskResult 协议，差异只在域名与 task.type，服务端已抹平。计费按次（Turnstile 约 $0.001~0.002/次），每个账号每天签到各解一个 token（token 一次性）。

配置后点「测试过验」会真实解一个 token 验证链路（消耗一次费用，不用于签到）；「检测站点防护」对站点做防护层探测并现场验证突破手段。

## FlareSolverr 部署

CF 边缘质询无浏览器解不了，借开源项目 [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) 代过：

```bash
docker run -d --name flaresolverr \
  -p 8191:8191 \
  -e LOG_LEVEL=info \
  --restart unless-stopped \
  ghcr.io/flaresolverr/flaresolverr:latest
```

然后在设置页把 FlareSolverr 地址填为 `http://<服务器IP>:8191`。

**关键约束：FlareSolverr 必须与本服务同一出口 IP。** cf_clearance 同时绑定出口 IP 与 User-Agent——FlareSolverr 用它自己的出口解出 cookie，本服务再用这个 cookie 请求时必须来自同一 IP、且带 FlareSolverr 返回的同一个 UA（服务端已自动处理 UA）。因此：

- 本服务与 FlareSolverr 部署在同一台机器/同一容器网络 → 直接可用；
- 本服务走 mihomo 等代理出口时，FlareSolverr 也必须经同一代理出口访问目标站（否则解出的 cookie 对本服务的出口无效）。

验证：设置页「检测站点防护」会对站点探测并对撞到的 CF 质询现场求解一次，结果里 `solved.cf_challenge` 为 `true` 即链路可用。

## 配置存储

以上配置都存在 `saved_config.json`（已在 .gitignore，仅服务端持有）：

```json
{
  "turnstile_solver": {"provider": "yescaptcha", "api_key": "...", "base_url": "", "flaresolverr_url": "http://127.0.0.1:8191"},
  "notify": {"type": "telegram", "url": "https://api.telegram.org/bot<TOKEN>/sendMessage", "chat_id": "...", "on_alert": true, "on_checkin_failed": false}
}
```

API 不回显密钥；更新时留空表示保留旧值。
