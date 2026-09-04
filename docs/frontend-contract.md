# 前端功能契约（重构验收基准）

> 重构后功能一个都不能丢，本文是验收基准。
> 来源：完整读取 `templates/index.html`(3855行)、`balance_server.py`(4335行)、
> `tests/test_site_frontend.mjs`(445行) 后逐项整理，行号为当前版本实际行号。
>
> 整理日期：2026-08-26

---

## 一、后端 API 全清单

共 **47 个路由** + 1 个鉴权中间件（`:106`）+ 1 个 startup 钩子（`:4210`）。
全部是 `@app.get` / `@app.post`，**无 put/delete**。

图例：✅ 前端有调用 ｜ ⛔ 端点存在但**前端从未调用**

### 认证（中间件白名单内，无需 Bearer）

| 方法 路径 | 行 | 用途 | 请求 | 响应 | |
|---|---|---|---|---|---|
| POST `/api/login` | 75 | 登录换 token（30 天） | `{username,password}` | `{success,token}` | ✅ |
| POST `/api/logout` | 84 | 吊销 token | — | `{success}` | ⛔ |
| GET `/api/check-auth` | 93 | 校验 token | — | `{authenticated}` | ✅ |

中间件（`:106`）：除白名单外所有 `/api/*` 强制 `Authorization: Bearer`，否则 401。

### 配置与预热

| GET/POST `/api/config` | 2009/2021 | 读写 saved_config.json | POST 任意 dict，前端固定发 `{accounts,email,monitor}` | `{success,data}` | ✅ |
| GET `/api/waf/warmup` | 509 | 预热阿里云 WAF cookie | — | `{success,message}` | ✅ |

### AnyRouter · Cookie 方式

| POST `/api/query` | 2031 | 批量查余额 | `{accounts:[{name,cookies:{session},api_user}]}` | `{success,results,summary}` | ✅ |
| POST `/api/checkin` | 2062 | 批量签到 | 同上 | `{success,results,summary}` | ⛔ 被 anyrouter/checkin/start 取代 |

### AnyRouter · Access Token 方式

| GET/POST `/api/token/accounts` | 2095/2105 | 读写 new_accounts_config.json | `{accounts:[{name,access_token,user_id,provider}]}` | — | ✅ |
| POST `/api/token/query` | 2119 | 批量查余额 | `{accounts?}` 不传则读文件 | `{success,results,summary}` | ✅ |
| POST `/api/token/checkin` | 2159 | 批量签到 | 同上 | `{success,results,summary}` | ⛔ **UI 无按钮，见缺口 #2** |

### AgentRouter（账号密码）

| GET/POST `/api/login-accounts/accounts` | 2201/2208 | 读写 agentrouter_accounts.json | `{accounts:[{name,username,password}]}` | — | ✅ |
| POST `/api/login-accounts/query` | 2222 | 旧式顺序查询 | — | — | ⛔ 被 balances 取代 |
| GET `/api/login-accounts/balances` | 2836 | 查余额（出口 IP 轮换绕 WAF）`?live=&names=` | — | `{success,results,summary}` | ✅ |
| POST `/api/login-accounts/checkin/fast` | 2249 | 一键全签（~1-2 分钟，已签跳过） | — | `{success,summary{total,new_signed,already,failed,aborted},status}` | ✅ |
| POST `/api/login-accounts/checkin/start` | 2348 | 缓慢签到（随机顺序+间隔） | — | `{success,message,status}` | ✅ |
| POST `/api/login-accounts/checkin/stop` | 2362 | 停止缓慢签到 | — | `{success,message}` | ✅ |
| GET `/api/login-accounts/checkin/status` | 2410 | 签到进度 | — | `{success,status}` | ✅ |

### 自动签到开关

| GET/POST `/api/checkin/settings` | 2428/2434 | 读写开关（含各站点 `<id>_auto`） | `{anyrouter_auto?,agentrouter_auto?,agentrouter_gap_min?,agentrouter_gap_max?,<site_id>_auto?}` | `{success,settings}` | ✅ |

### AnyRouter 签到 / 续期

| POST `/api/anyrouter/checkin/start` | 2884 | 并发签到 cookie 账号 | — | `{success,message,status}` | ✅ |
| GET `/api/anyrouter/checkin/status` | 2901 | 签到进度 | — | `{success,status}` | ✅ |
| GET `/api/anyrouter/cookie-status` | 2907 | cookie 剩余天数 | — | `{success,accounts}` | ⛔ 前端改本地解码 |
| POST `/api/anyrouter/renew` | 2923 | 续期 session +30 天 | `{names?}` | `{success,results,summary,notice?}` | ✅ |

### 通用 new-api 站点（`{site_id}` 路径参数，加站点零改代码）

| GET/POST `/api/sites` | 3002/3008 | 站点清单读写 | `{sites:[NewapiSite]}` | `{success,sites}` | ✅ |
| POST `/api/sites/probe` | 3032 | 探测域名是否 new-api | `{domain}` | `{success,info{version,system_name,checkin_enabled,turnstile_check,quota_per_unit}}` | ✅ |
| GET/POST `/api/site/{id}/accounts` | 3067/3076 | 站点账号读写 | `{accounts:[{name,access_token,user_id}]}` | — | ✅ |
| POST `/api/site/{id}/query` | 3090 | 并发查余额 | — | `{success,results,summary}` | ✅ |
| POST `/api/site/{id}/checkin/start` | 3120 | 服务端一键签到 | — | `{success,message,status}` | ✅ |
| GET `/api/site/{id}/turnstile` | 3141 | Turnstile 探测（5 分钟缓存） | — | `{success,turnstile{enabled,site_key,probed}}` | ✅ |
| GET `/api/site/{id}/checkin/status` | 3154 | 签到进度 | — | `{success,status}` | ✅ |
| POST `/api/site/{id}/checkin/sync` | 3163 | 脚本跑完后核对状态+查余额 | — | `{success,results,checked_in,total,status}` | ✅ |
| GET `/api/site/{id}/checkin/info` | 3240 | 只读签到状态与奖励区间 | — | `{success,accounts:[{name,enabled,min_reward,max_reward,checked_in_today,total_checkins,total_reward}]}` | ⛔ |

### 人机校验与防护突破（Turnstile 打码 / CF 质询 / 阿里云 WAF）

| GET `/api/turnstile/solver` | 3843 | 打码与 FlareSolverr 配置状态（api_key 只回 configured，不回显） | — | `{success,solver{provider,base_url,configured,flaresolverr_url,presets}}` | ✅ |
| POST `/api/turnstile/solver` | 3859 | 保存打码 / FlareSolverr 配置（合并写 saved_config.json 的 turnstile_solver 段） | `{provider,api_key?,base_url?,flaresolverr_url?}`，api_key 留空保留旧值 | `{success,message?/error?}` | ✅ |
| POST `/api/turnstile/solver/test` | 3926 | 实解一个 token 验证打码配置（消耗一次费用；site_id 缺省自动挑开启 Turnstile 的站点） | `?site_id=` | `{success,tested,site?,elapsed?,token_preview?,message?/error?}` | ✅ |
| POST `/api/protection/test` | 3880 | 探测站点防护层（CF 质询/阿里云 WAF/Turnstile）并现场验证突破；solved 的 null=撞到但没配求解器 | `?site_id=` | `{success,site,domain,page_http_status,protections,solved,flaresolverr_configured}` | ✅ |

- 配置打码后 `run_newapi_checkin` 遇到 Turnstile 站点逐账号求解 token（`?turnstile=`）再签到；未配置则维持「提示浏览器脚本」原行为。
- `newapi_request` 撞上 CF 边缘质询（cf-mitigated: challenge）或阿里云 WAF 挑战页（arg1）时，自动解防护 cookies（按域名缓存 5 分钟 + singleflight）原地重打一次；CF 质询需在设置页配 FlareSolverr（`request.get` 协议，必须与本服务同出口 IP，cf_clearance 绑 UA）。
- `/api/sites/probe` 的 info 现在带 `protections{cf_challenge,aliyun_waf}`（首页裸探测，保守分类）。

### 密钥管理

| POST `/api/keys/list` | 3826 | 批量列出/补取全量密钥 | `{refs:[...],refresh?}` | `{success,accounts:[{ref,name,provider,success,keys,total,truncated,warning?,cached?}]}` | ✅ |
| POST `/api/keys/create` | 3854 | 建密钥 | `{ref,name,unlimited_quota?,remain_quota?,expired_time?,group?}` | `{success,account}` | ✅ |
| POST `/api/keys/delete` | 3897 | 删密钥 | `{ref,id}` | `{success,account}` | ✅ |

### 监控

| POST `/api/monitor/start` | 3924 | 启动余额监控 | `{accounts,email{smtp_server,smtp_port,email_user,email_pass,email_to},interval_hours=6,threshold=10}` | `{success,message}` | ✅ |
| POST `/api/monitor/stop` | 3944 | 停止 | — | `{success,message}` | ✅ |
| GET `/api/monitor/status` | 3959 | 状态 | — | `{running,config,last_check,next_check,alerted_accounts,logs}` ⚠️**不带 success 外层** | ✅ |

### 用量统计

| GET `/api/usage/today` | 4284 | 今日用量基线 | — | `{success,date,baseline{"provider:name":used0}}` | ✅ |
| GET `/api/usage/history` | 4315 | **近 30 天历史** | — | `{success,history}` | ⛔ **统计面板的现成数据源** |
| POST `/api/usage/snapshot` | 4327 | 手动触发快照 | — | `{success,message}` | ⛔ |

---

## 二、`_ref` 寻址协议（前后端必须严格一致）

```
token:<i>            AnyRouter access_token 账号
cookie:<i>           AnyRouter session cookie 账号
login:<i>            AgentRouter 账号密码账号
site:<site_id>:<i>   通用 new-api 站点账号
```

前端 `resolveRef()`(`index.html:1427`) 与后端 `resolve_key_ctx()`(`balance_server.py:3408`)
是两处独立实现，**格式必须严格一致**。

不用全局下标是为了避免「删站点/删账号后引用错位」——账号卡 / 密钥管理 / 编辑表单
三处共享这一个定位协议。测试对此有两条专门断言。

---

## 三、关键交互行为

### 可视化 ↔ JSON 双模式（`switchTab`, `:1205`）

JSON→可视化：先 `syncFromJsonView()` 解析校验，**失败则拒绝切换**。
可视化→JSON：`syncToJsonView()` 序列化四类账号。

JSON 数组按字段特征分流：
- 有 `access_token` 且 `provider` 命中已注册站点 id → 归该站点
- 有 `access_token`（其它）→ token 账号
- 有 `username`+`password` → login 账号
- 其余 → cookie 账号

**站点被删除后其账号不会丢**——`provider` 指向未知站点时退回 AnyRouter token 桶。

### ID 去重（`dedupeAccounts`, `:1100`）

按 `_acctKey()` 分组：同站点同 `user_id` 算重复；token/cookie 共享 `anyrouter:` 命名空间；
login 按 username；站点账号按 `site_id:user_id`。
重复时**优先保留有效期最长的 cookie**，否则保留第一个。confirm 二次确认后持久化。

### 浏览器签到脚本（`buildSiteScript`, `:2606`）— 测试重点覆盖

生成在目标站点 Console 执行的自包含脚本，五条硬约束：

1. **`credentials:'omit'`** — 不能带 session cookie，否则 new-api 鉴权优先信任浏览器登录态，
   导致除当前登录账号外**全部 401**
2. **用站点自己配置的相对路径** — 绝对路径会引入 CORS
3. **sitekey 绝不硬编码** — 来自探测结果，拿不到就自我拒绝，不能拿空值渲染
4. **只用 `createElement`**，不用 innerHTML — 规避 Trusted Types
5. **Turnstile widget 用 `seq`+`settle()` 序号隔离** — 防上一账号超时打断下一账号

另：账号名含引号 / `</script>` 不能破坏脚本（XSS 与语法注入防护）。
`copySiteScript` 会先跑 `checkin/sync` 把今日已签的账号从脚本里剔除（`filterPendingAccounts`）。

### 一键全签 vs 缓慢签到（互斥）

- **一键全签**（`:2533`）：仅 AgentRouter，服务端轮换出口 IP 分批登录，~1-2 分钟，已签跳过
- **缓慢模式**（`:2503`）：随机顺序，账号间隔 30~60 分钟（可配 1~1440），跑后台可关页面
- 任一运行中点另一个都会被拦截提示「正在运行中」

### 其它

- **Cookie 续期**（`:2846`）：仅 cookie 类账号，撞站点限流会显示 notice 提示条
- **自动签到开关**：键名 `<provider>_auto`，乐观更新 UI，失败回滚。
  站点开着 Turnstile 时提示文案变琥珀色警告
- **认证**：唯一 localStorage key 是 `auth_token`；`authFetch()`(`:985`) 统一注入 Bearer，
  401 时清 token + 弹登录层。**无 sessionStorage**

---

## 四、必须保留的纯函数（30 项测试直接覆盖）

测试原理：正则抓最大的 `<script>` 块，按函数名+括号计数截取源码，在带最小 DOM 替身的
沙箱里 `new Function` eval——**不起浏览器、不发请求**，只测纯函数逻辑。

| 函数 | 行 | 职责 |
|---|---|---|
| `esc(s)` | 3364 | HTML 转义 |
| `cssId(ref)` | 3361 | `_ref` → 合法 DOM id |
| `tsDate(ts)` / `tsDateTime(ts)` | 3367/3372 | 秒级时间戳格式化，`ts<0` 或空返回 `-`（永不过期） |
| `maskToken(token)` | 1236 | 前 6…后 4 掩码 |
| `sessionDaysLeft` / `cookieExpiryBadge` | 2454/2466 | 本地解码 gorilla cookie 算剩余天数 |
| `resolveRef(ref)` | 1427 | `_ref` → `{arr,idx,type,siteId,save}` |
| `providerAccounts()` | 1245 | 当前 provider 的账号列表（挂 `_ref`） |
| `accentOf(site)` | 1051 | 站点 → 主题色 token（未知回退 orange） |
| `buildKeyText(accounts,format)` | 3552 | 密钥批量复制三种格式，**都带 `sk-` 前缀** |
| `filterPendingAccounts` | 2667 | 按同步结果剔除今日已签账号 |
| `_acctKey` / `_acctTypeLabel` / `_acctId` | 1079/1085/1095 | 去重分组键 / 类型名 / 按类型取 ID |
| `syncToJsonView` / `syncFromJsonView` | 1161/1170 | 四类账号 ↔ JSON 双向序列化 |
| `todayUsedOf` / `getTodayUsed` / `sumTodayUsage` | 1861/1870/2231 | 今日用量（AgentRouter 快照特判返回 null） |

30 项断言分组：站点识别与主题 4 项 / `_ref` 定位 3 项 / JSON 往返 3 项 /
去重分组键 4 项 / 浏览器脚本生成 7 项 / 密钥管理 9 项。

---

## 五、现状缺口（重构决策点，不能默默处理）

| # | 缺口 | 说明 |
|---|---|---|
| 1 | **无登出功能** | `/api/logout` 端点存在，前端从未调用，UI 无入口 |
| 2 | **token 模式 AnyRouter 账号签不了到** | `/api/token/checkin` 存在但无按钮触发；`anyrouterCheckin()` 只处理 cookie 账号；自动签到调度器同样只签 cookie 账号。这类账号**手动自动都签不了，只能查余额** |
| 3 | **筛选框形同虚设** | `#checkinFilter` 切 all/checked/unchecked 只触发重渲染，但 `hasCheckedIn(r)`(`:1877`) **硬编码返回 `null`**（注释写明「暂无法通过 stat API 判断是否已签到」），UI 在但功能未接通 |
| 4 | **`/api/usage/history` 从未被调用** | 后端已提供近 30 天历史，前端完全没用——**统计面板的现成数据源** |
| 5 | `/api/monitor/status` 响应结构不一致 | 唯一不带 `{success:...}` 外层包装的端点 |
| 6 | `keyManagerModal` 不响应 ESC | `:3707` 只监听 monitorModal / siteManagerModal |
| 7 | `/api/anyrouter/cookie-status` 未使用 | 前端本地解码算剩余天数，两边逻辑理论等价但无一致性测试 |
| 8 | 删站点保留数据 | `removeSite()` 只摘 `newapi_sites.json`，账号与签到状态文件保留，重加同 id 站点即恢复——**有意设计，非 bug** |
