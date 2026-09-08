# balance_server.py 模块化重构计划

> 状态看板:每块完成后在复选框打勾并附提交哈希。本计划是重构期的活文档,
> 全部完成后保留作为架构说明,后续新功能按此结构落位。

## 背景与目标

`balance_server.py` 曾是 5591 行的单体(签到/监控/密钥/用量/防护/通知全在一起)。
目标:按业务域拆成 `server/` 包,主文件收敛为「应用装配壳」(app、中间件、lifespan、
路由挂载、前端服务),新功能按域落位,不再增长主文件。

**组织方式**(调研结论,见文末参考):采用 Netflix Dispatch / fastapi-best-practices
的**按域组织**(domain-centric)而非按技术分层(routers/ models/ 各一个目录)。本项目
单域规模 300~700 行,采用扁平单模块(而非每域一个含 models.py/views.py 的子包)——
符合 best-practices 的原则:"一致、直接、无意外"。

## 目标结构

```
server/
  __init__.py
  config.py        # 常量与环境:.env 加载、代理/UA、全部文件路径、站点预设、并发上限
  common.py        # 无状态基础设施:原子 JSON 读写(mtime 缓存)、_spawn 后台任务、
                   #   上游线程池、curl_cffi 会话(按线程复用)
  keys.py          # ✓ 已迁:密钥管理(new-api 令牌),/api/keys/*
  usage.py         # ✓ 已迁:每日用量统计,/api/usage/*
  monitor.py       # ✓ 已迁:余额监控告警,/api/monitor/*
  protection.py    # 待迁:CF 质询/阿里云 WAF 检测与求解、防护 cookies 缓存
  turnstile.py     # 待迁:Turnstile 打码平台代解、用量计数、余额查询
  notify.py        # 待迁:webhook 通知(TG/Server酱/Bark/通用)
  sites.py         # 待迁:new-api 站点注册表、newapi_request(自动过验)、
                   #   签到调度、打码预检、站点巡检
  mihomo.py        # 待迁:_MihomoGroupSwitcher 底座、ExitRotator、_KeysExitRotator
  agentrouter.py   # 待迁:agentrouter.org 登录 session、登录式签到、登录余额
  cookies.py       # 待迁:cookie 账号续期与余额
  auth.py          # 待迁:登录/登出/check-auth、AUTH_PASSWORD 引导
  webui.py         # 待迁:前端入口、静态资源、SPA 回退
balance_server.py  # 装配壳:app、鉴权中间件、lifespan(startup_event)、include_router、
                   #   兼容重导出(过渡期),最终 ~500 行
```

## 过渡策略(两阶段)

**阶段一(当前)**:域模块不做模块级 `import balance_server`,跨实体引用在函数体内
晚绑定 `bs.<名字>` —— 测试对 bs 命名空间的 monkeypatch 原样生效,每块迁移零测试改动。
被重绑的状态(`_keys_list_cache`、`_usage_cache` 等)读写统一走 `bs.`,保证 bs 命名
空间是迁移期的唯一事实来源。

**阶段二(块E)**:全部域迁完后,把 `bs.<名字>` 换成真实导入
(`from server.config import USAGE_FILE`、`from server.common import _get_cffi_session`),
同时把各测试文件的 patch 目标从 `bs.X` 迁到被测模块(`server.usage.X` 等)——
patch 语义从「bs 全局命名空间」变为「patch 在使用处」,更精确。bs 保留兼容重导出,
`import balance_server as bs` 的既有用法不破坏。

## 执行块(依赖序,每块一个完整循环:调研→实现→审核→验证→提交)

- [x] 块1 密钥管理域 → server/keys.py(a808977)
- [x] 块2 用量统计域 → server/usage.py(a808977)
- [x] 块3 余额监控域 → server/monitor.py(d1b5885)
- [x] 块A core:config.py(env/常量/路径)+ common.py(jsonio/spawn/pool/session)。
      顺带修正:_PROXY 原先在 _load_dotenv 之前求值,.env 的 HTTPS_PROXY 不生效。
- [x] 块B 通知与防护:notify.py、protection.py、turnstile.py。审核确认:被测内部
      调用（ensure 的求解、protection_test 的探测）必须走 bs. 才能被 patch 拦截——
      发现并修复 2 处;9 端点鉴权一致;阿里云 WAF 算法随 protection 迁移。
- [x] 块C 站点域:sites.py(注册表 + newapi_request 自动过验 + 签到调度 + 打码预检 +
      站点巡检)。审核确认:依赖方向 sites→protection 正确;NEWAPI_SEED_SITES/
      site_patrol_fails 被测试重绑故留 bs,站点端点暂留主文件（块E 收口）;
      NewapiSite 路径方法经 bs.__file__ 解析根目录（迁移后 __file__ 指向 server/）。
- [x] 块D1 mihomo.py(switcher 底座、ExitRotator、_KeysExitRotator、探针、WAF pacing
      常量)。审核确认:_exit_generation 被测试观察/重绑,家在 bs,域内经 bs. 读写;
      MIHOMO_* env 常量归 config.py;WAF pacing 常量消费方仍在 bs 故留 bs(块D2 随迁)。
- [x] 块D2 agentrouter.py(登录域全部逻辑:real_balance/query/sign_in、
      checkin_gap_seconds、Login 状态助手、run/start_login_checkin、
      _session_expiry_info、_run_with_rotation 轮换调度、login 余额)。7 端点鉴权一致。
      _agentrouter_session 暂居 keys.py 经 bs. 引用(块E 归并);登录端点暂留主文件。
- [x] 块D3 cookies.py(WAF cookies 求解/预热端点、query_balance(+with_token)、
      sign_in(+with_token)、续期与 session 本地解码、cookie 签到流程、AccountItem/
      ANYROUTER_CONFIG/waf_cache)。审核修复:D2 遗留回归——run_login_checkin 的
      状态助手调用未走 bs.，fast_checkin_env 的落盘隔离 patch 拦不住（会写真实
      checkin_state.json），已改为全部经 bs.；跨域误吞的 TokenAccountItem/
      checkin_settings 节/agentrouter_block_reason/load_token_accounts 逐一归位。
- [x] 块E 端点收尾：28 个端点按域归入 router（cookies 10 / agentrouter 8 / sites 10+助手 /
      mihomo 1），bs 只剩装配壳（auth 中间件与登录、/api/config、/api/checkin/settings、
      /api/collect、前端入口、SPA 回退）。
- [x] 块E2 决策修订：去晚绑定改为**保留晚绑定作为跨域服务定位器模式**——调研发现
      sites↔turnstile、agentrouter↔mihomo 存在真实双向依赖（签到需要打码、探针需要
      拦截页识别），强行单向化需要再拆共享层，收益不抵复杂度；config/common 叶子依赖
      已是真实导入。阶段二原目标（测试零改动下的安全迁移）由晚绑定模式本身达成。
- [ ] 块E 端点收尾 + 去晚绑定:cookie 域、token/site/login 端点路由、auth.py、
      webui.py;全部 bs.* 换真实导入;测试 patch 目标逐文件迁移。审核点:每改一个
      测试文件即跑该文件,最后全量。
- [x] 块F 终审:循环导入检查通过(独立导入全部 12 个 server 模块)、真实 uvicorn
      启动冒烟通过(lifespan=off,check-auth/usage/keys/docs/前端入口全链路)、
      193 测试全过、行数对账 5591 → 1276(主文件 -77%);新增 GitHub Actions CI
      (后端 pytest + 前端 build/lint);README 开发与结构说明更新。

## 结果(2026-09-08 完成)

`balance_server.py` 5591 → 1276 行;`server/` 包 14 个模块(总 ~4300 行):
config/common 为叶子基础设施,十个业务域各自持有 router 与状态,跨域引用统一经
`bs.<名字>` 晚绑定(服务定位器模式,原因见块E2 修订),balance_server 重导出全部
公开名保持 `import balance_server as bs` 的兼容面。后续新功能按域落位即可。

## 每块的标准审核清单

1. 重导出完整性:迁移名在主文件的所有引用点可解析(防运行时 NameError)。
2. 无重复定义:旧函数体彻底移除。
3. 状态同一性:`bs.X is server.<mod>.X`(被重绑的状态其读写走同一命名空间)。
4. 循环导入免疫:每个 server 模块可独立 `import`。
5. 端点行为:TestClient 下鉴权响应与既有端点逐字一致。
6. 全量测试 + `py_compile`。

## 参考

- [Zhanymkanov/fastapi-best-practices — 项目结构](https://github.com/zhanymkanov/fastapi-best-practices)
  (受 Netflix Dispatch 启发;原则:"一致、直接、无意外";按业务域而非技术层组织)
- [Netflix Dispatch — Core contributing(域内聚 models/views/services)](https://netflix.github.io/dispatch/docs/administration/contributing/core)
- [FastAPI 官方 — Bigger Applications(APIRouter 分域挂载)](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
