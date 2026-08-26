# New API 接口文档

本文档详细列出所有需要用户认证（access_token）的 API 接口。

> 生成时间: 2026-02-05
> 基于分支: quota-currency-unit

---

## 目录

- [认证方式](#认证方式)
- [权限层级](#权限层级)
- [公开接口（无需认证）](#公开接口无需认证)
- [UserAuth 接口（普通用户）](#userauth-接口普通用户)
- [AdminAuth 接口（管理员）](#adminauth-接口管理员)
- [RootAuth 接口（超级管理员）](#rootauth-接口超级管理员)
- [TokenAuth 接口（API Key 认证）](#tokenauth-接口api-key-认证)
- [错误码说明](#错误码说明)

---

## 认证方式

### Session 认证（Web 端）
通过浏览器登录后自动获取 Session Cookie。

### Access Token 认证（API 调用）

| Header | 说明 | 示例 |
|--------|------|------|
| `Authorization` | Bearer Token | `Bearer your_access_token` |
| `New-Api-User` | 用户 ID（**必须**与 Token 所属用户匹配） | `123` |

**获取 Access Token**: `GET /api/user/token`

### 认证逻辑
1. 优先检查 Session 登录状态
2. 如果未登录，检查 `Authorization` Header 中的 access_token
3. 验证 `New-Api-User` Header 是否与认证用户 ID 匹配
4. 检查用户状态（是否被封禁）
5. 检查用户权限等级

---

## 权限层级

| 角色 | role 值 | 中间件 | 说明 |
|------|---------|--------|------|
| 普通用户 | `>= 1` | `UserAuth()` | 可管理自己的资源 |
| 管理员 | `>= 2` | `AdminAuth()` | 可管理所有用户资源 |
| 超级管理员 | `>= 3` | `RootAuth()` | 可修改系统配置 |

---

## 公开接口（无需认证）

### 系统状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/setup` | 获取初始化状态 |
| POST | `/api/setup` | 初始化系统 |
| GET | `/api/status` | 获取系统状态 |
| GET | `/api/uptime/status` | UptimeKuma 状态检查 |
| GET | `/api/notice` | 获取公告 |
| GET | `/api/user-agreement` | 获取用户协议 |
| GET | `/api/privacy-policy` | 获取隐私政策 |
| GET | `/api/about` | 获取关于信息 |
| GET | `/api/home_page_content` | 获取首页内容 |
| GET | `/api/ratio_config` | 获取比率配置 |

### 用户认证

| 方法 | 路径 | 说明 | 限流 |
|------|------|------|------|
| POST | `/api/user/register` | 用户注册 | CriticalRateLimit + TurnstileCheck |
| POST | `/api/user/login` | 用户登录 | CriticalRateLimit + TurnstileCheck |
| POST | `/api/user/login/2fa` | 2FA 登录验证 | CriticalRateLimit |
| POST | `/api/user/passkey/login/begin` | Passkey 登录开始 | CriticalRateLimit |
| POST | `/api/user/passkey/login/finish` | Passkey 登录完成 | CriticalRateLimit |
| GET | `/api/user/logout` | 用户登出 | - |
| GET | `/api/user/groups` | 获取用户分组列表 | - |

### OAuth 登录

| 方法 | 路径 | 说明 | 限流 |
|------|------|------|------|
| GET | `/api/oauth/github` | GitHub OAuth | CriticalRateLimit |
| GET | `/api/oauth/discord` | Discord OAuth | CriticalRateLimit |
| GET | `/api/oauth/oidc` | OIDC 认证 | CriticalRateLimit |
| GET | `/api/oauth/linuxdo` | LinuxDo OAuth | CriticalRateLimit |
| GET | `/api/oauth/state` | 生成 OAuth 状态码 | CriticalRateLimit |
| GET | `/api/oauth/wechat` | 微信 OAuth | CriticalRateLimit |
| GET | `/api/oauth/wechat/bind` | 微信绑定 | CriticalRateLimit |
| GET | `/api/oauth/email/bind` | 邮箱绑定 | CriticalRateLimit |
| GET | `/api/oauth/telegram/login` | Telegram 登录 | CriticalRateLimit |
| GET | `/api/oauth/telegram/bind` | Telegram 绑定 | CriticalRateLimit |

### 密码重置

| 方法 | 路径 | 说明 | 限流 |
|------|------|------|------|
| GET | `/api/verification` | 发送邮箱验证码 | EmailVerificationRateLimit + TurnstileCheck |
| GET | `/api/reset_password` | 发送密码重置邮件 | CriticalRateLimit + TurnstileCheck |
| POST | `/api/user/reset` | 重置密码 | CriticalRateLimit |

### Webhook 回调

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/user/epay/notify` | 易支付回调 |
| POST | `/api/stripe/webhook` | Stripe Webhook |
| POST | `/api/creem/webhook` | Creem Webhook |

### 定价信息

| 方法 | 路径 | 说明 | 备注 |
|------|------|------|------|
| GET | `/api/pricing` | 获取定价信息 | TryUserAuth（可选认证） |

---

## UserAuth 接口（普通用户）

> 需要 `role >= 1`

### 用户信息管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/self` | `GetSelf` | 获取当前用户信息 |
| PUT | `/api/user/self` | `UpdateSelf` | 更新个人资料 |
| DELETE | `/api/user/self` | `DeleteSelf` | 删除账户 |
| GET | `/api/user/self/groups` | `GetUserGroups` | 获取用户分组 |
| GET | `/api/user/models` | `GetUserModels` | 获取可用模型列表 |
| PUT | `/api/user/setting` | `UpdateUserSetting` | 更新用户设置 |

### Access Token 管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/token` | `GenerateAccessToken` | 生成 Access Token |

### Passkey 管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/passkey` | `PasskeyStatus` | 获取 Passkey 状态 |
| POST | `/api/user/passkey/register/begin` | `PasskeyRegisterBegin` | 开始注册 Passkey |
| POST | `/api/user/passkey/register/finish` | `PasskeyRegisterFinish` | 完成注册 Passkey |
| POST | `/api/user/passkey/verify/begin` | `PasskeyVerifyBegin` | 开始验证 Passkey |
| POST | `/api/user/passkey/verify/finish` | `PasskeyVerifyFinish` | 完成验证 Passkey |
| DELETE | `/api/user/passkey` | `PasskeyDelete` | 删除 Passkey |

### 2FA 双因素认证

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/2fa/status` | `Get2FAStatus` | 获取 2FA 状态 |
| POST | `/api/user/2fa/setup` | `Setup2FA` | 设置 2FA |
| POST | `/api/user/2fa/enable` | `Enable2FA` | 启用 2FA |
| POST | `/api/user/2fa/disable` | `Disable2FA` | 禁用 2FA |
| POST | `/api/user/2fa/backup_codes` | `RegenerateBackupCodes` | 重新生成备份码 |

### 签到功能

| 方法 | 路径 | Controller | 说明 | 限流 |
|------|------|------------|------|------|
| GET | `/api/user/checkin` | `GetCheckinStatus` | 获取签到状态 | - |
| POST | `/api/user/checkin` | `DoCheckin` | 执行签到 | TurnstileCheck |

### 邀请返利

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/aff` | `GetAffCode` | 获取邀请码 |
| POST | `/api/user/aff_transfer` | `TransferAffQuota` | 转移邀请额度 |

### 充值与支付

| 方法 | 路径 | Controller | 说明 | 限流 |
|------|------|------------|------|------|
| GET | `/api/user/topup/info` | `GetTopUpInfo` | 获取充值信息 | - |
| GET | `/api/user/topup/self` | `GetUserTopUps` | 获取充值记录 | - |
| POST | `/api/user/topup` | `TopUp` | 使用兑换码充值 | CriticalRateLimit |
| POST | `/api/user/pay` | `RequestEpay` | 发起易支付 | CriticalRateLimit |
| POST | `/api/user/amount` | `RequestAmount` | 请求支付金额 | - |
| POST | `/api/user/stripe/pay` | `RequestStripePay` | 发起 Stripe 支付 | CriticalRateLimit |
| POST | `/api/user/stripe/amount` | `RequestStripeAmount` | 请求 Stripe 金额 | - |
| POST | `/api/user/creem/pay` | `RequestCreemPay` | 发起 Creem 支付 | CriticalRateLimit |

### 安全验证

| 方法 | 路径 | Controller | 说明 | 限流 |
|------|------|------------|------|------|
| POST | `/api/verify` | `UniversalVerify` | 通用安全验证 | CriticalRateLimit |
| GET | `/api/verify/status` | `GetVerificationStatus` | 获取验证状态 | - |

### API Token 管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/token/` | `GetAllTokens` | 获取所有 Token |
| GET | `/api/token/search` | `SearchTokens` | 搜索 Token |
| GET | `/api/token/:id` | `GetToken` | 获取指定 Token |
| POST | `/api/token/` | `AddToken` | 创建 Token |
| PUT | `/api/token/` | `UpdateToken` | 更新 Token |
| DELETE | `/api/token/:id` | `DeleteToken` | 删除 Token |
| POST | `/api/token/batch` | `DeleteTokenBatch` | 批量删除 Token |

### 日志查询（个人）

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/log/self` | `GetUserLogs` | 获取个人日志 |
| GET | `/api/log/self/stat` | `GetLogsSelfStat` | 获取个人统计 |
| GET | `/api/log/self/search` | `SearchUserLogs` | 搜索个人日志 |

### 数据统计（个人）

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/data/self` | `GetUserQuotaDates` | 获取个人额度统计 |

### 任务查询（个人）

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/mj/self` | `GetUserMidjourney` | 获取个人 Midjourney 任务 |
| GET | `/api/task/self` | `GetUserTask` | 获取个人异步任务 |

### Dashboard 模型

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/models` | `DashboardListModels` | 获取模型列表（Dashboard） |

### Playground

| 方法 | 路径 | Controller | 说明 | 中间件 |
|------|------|------------|------|--------|
| POST | `/pg/chat/completions` | `Playground` | Playground 聊天 | UserAuth + Distribute |

---

## AdminAuth 接口（管理员）

> 需要 `role >= 2`，包含所有 UserAuth 接口权限

### 用户管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/` | `GetAllUsers` | 获取所有用户 |
| GET | `/api/user/search` | `SearchUsers` | 搜索用户 |
| GET | `/api/user/:id` | `GetUser` | 获取指定用户 |
| POST | `/api/user/` | `CreateUser` | 创建用户 |
| POST | `/api/user/manage` | `ManageUser` | 管理用户（封禁/解封等） |
| PUT | `/api/user/` | `UpdateUser` | 更新用户 |
| DELETE | `/api/user/:id` | `DeleteUser` | 删除用户 |
| DELETE | `/api/user/:id/reset_passkey` | `AdminResetPasskey` | 重置用户 Passkey |

### 用户充值管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/topup` | `GetAllTopUps` | 获取所有充值记录 |
| POST | `/api/user/topup/complete` | `AdminCompleteTopUp` | 管理员完成充值 |

### 用户 2FA 管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/user/2fa/stats` | `Admin2FAStats` | 获取 2FA 统计 |
| DELETE | `/api/user/:id/2fa` | `AdminDisable2FA` | 禁用用户 2FA |

### 通道管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/channel/` | `GetAllChannels` | 获取所有通道 |
| GET | `/api/channel/search` | `SearchChannels` | 搜索通道 |
| GET | `/api/channel/:id` | `GetChannel` | 获取指定通道 |
| POST | `/api/channel/` | `AddChannel` | 创建通道 |
| PUT | `/api/channel/` | `UpdateChannel` | 更新通道 |
| DELETE | `/api/channel/:id` | `DeleteChannel` | 删除通道 |
| POST | `/api/channel/batch` | `DeleteChannelBatch` | 批量删除通道 |
| DELETE | `/api/channel/disabled` | `DeleteDisabledChannel` | 删除已禁用通道 |
| POST | `/api/channel/copy/:id` | `CopyChannel` | 复制通道 |

### 通道模型管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/channel/models` | `ChannelListModels` | 列出通道模型 |
| GET | `/api/channel/models_enabled` | `EnabledListModels` | 列出已启用模型 |
| POST | `/api/channel/fix` | `FixChannelsAbilities` | 修复通道能力 |
| GET | `/api/channel/fetch_models/:id` | `FetchUpstreamModels` | 获取上游模型（单通道） |
| POST | `/api/channel/fetch_models` | `FetchModels` | 获取上游模型（批量） |

### 通道测试

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/channel/test` | `TestAllChannels` | 测试所有通道 |
| GET | `/api/channel/test/:id` | `TestChannel` | 测试指定通道 |
| GET | `/api/channel/update_balance` | `UpdateAllChannelsBalance` | 更新所有通道余额 |
| GET | `/api/channel/update_balance/:id` | `UpdateChannelBalance` | 更新指定通道余额 |

### 通道标签管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| POST | `/api/channel/tag/disabled` | `DisableTagChannels` | 禁用标签通道 |
| POST | `/api/channel/tag/enabled` | `EnableTagChannels` | 启用标签通道 |
| PUT | `/api/channel/tag` | `EditTagChannels` | 编辑标签通道 |
| POST | `/api/channel/batch/tag` | `BatchSetChannelTag` | 批量设置通道标签 |
| GET | `/api/channel/tag/models` | `GetTagModels` | 获取标签模型 |

### 通道 Multi-Key 管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| POST | `/api/channel/multi_key/manage` | `ManageMultiKeys` | 管理多 Key |

### 通道密钥获取（需要 RootAuth + 安全验证）

| 方法 | 路径 | Controller | 说明 | 额外限制 |
|------|------|------------|------|----------|
| POST | `/api/channel/:id/key` | `GetChannelKey` | 获取通道密钥 | RootAuth + CriticalRateLimit + DisableCache + SecureVerificationRequired |

### Codex OAuth

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| POST | `/api/channel/codex/oauth/start` | `StartCodexOAuth` | 开始 Codex OAuth |
| POST | `/api/channel/codex/oauth/complete` | `CompleteCodexOAuth` | 完成 Codex OAuth |
| POST | `/api/channel/:id/codex/oauth/start` | `StartCodexOAuthForChannel` | 开始通道 Codex OAuth |
| POST | `/api/channel/:id/codex/oauth/complete` | `CompleteCodexOAuthForChannel` | 完成通道 Codex OAuth |
| POST | `/api/channel/:id/codex/refresh` | `RefreshCodexChannelCredential` | 刷新 Codex 凭证 |
| GET | `/api/channel/:id/codex/usage` | `GetCodexChannelUsage` | 获取 Codex 使用量 |

### Ollama 管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| POST | `/api/channel/ollama/pull` | `OllamaPullModel` | 拉取 Ollama 模型 |
| POST | `/api/channel/ollama/pull/stream` | `OllamaPullModelStream` | 拉取模型（流式） |
| DELETE | `/api/channel/ollama/delete` | `OllamaDeleteModel` | 删除 Ollama 模型 |
| GET | `/api/channel/ollama/version/:id` | `OllamaVersion` | 获取 Ollama 版本 |

### 兑换码管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/redemption/` | `GetAllRedemptions` | 获取所有兑换码 |
| GET | `/api/redemption/search` | `SearchRedemptions` | 搜索兑换码 |
| GET | `/api/redemption/:id` | `GetRedemption` | 获取指定兑换码 |
| POST | `/api/redemption/` | `AddRedemption` | 创建兑换码 |
| PUT | `/api/redemption/` | `UpdateRedemption` | 更新兑换码 |
| DELETE | `/api/redemption/:id` | `DeleteRedemption` | 删除兑换码 |
| DELETE | `/api/redemption/invalid` | `DeleteInvalidRedemption` | 删除无效兑换码 |

### 日志管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/log/` | `GetAllLogs` | 获取所有日志 |
| GET | `/api/log/stat` | `GetLogsStat` | 获取日志统计 |
| GET | `/api/log/search` | `SearchAllLogs` | 搜索所有日志 |
| DELETE | `/api/log/` | `DeleteHistoryLogs` | 删除历史日志 |

### 数据统计

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/data/` | `GetAllQuotaDates` | 获取所有额度统计 |

### 分组管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/group/` | `GetGroups` | 获取分组列表 |

### 预填分组管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/prefill_group/` | `GetPrefillGroups` | 获取预填分组 |
| POST | `/api/prefill_group/` | `CreatePrefillGroup` | 创建预填分组 |
| PUT | `/api/prefill_group/` | `UpdatePrefillGroup` | 更新预填分组 |
| DELETE | `/api/prefill_group/:id` | `DeletePrefillGroup` | 删除预填分组 |

### 任务管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/mj/` | `GetAllMidjourney` | 获取所有 MJ 任务 |
| GET | `/api/task/` | `GetAllTask` | 获取所有异步任务 |

### 供应商管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/vendors/` | `GetAllVendors` | 获取所有供应商 |
| GET | `/api/vendors/search` | `SearchVendors` | 搜索供应商 |
| GET | `/api/vendors/:id` | `GetVendorMeta` | 获取供应商元数据 |
| POST | `/api/vendors/` | `CreateVendorMeta` | 创建供应商 |
| PUT | `/api/vendors/` | `UpdateVendorMeta` | 更新供应商 |
| DELETE | `/api/vendors/:id` | `DeleteVendorMeta` | 删除供应商 |

### 模型元数据管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/models/` | `GetAllModelsMeta` | 获取所有模型元数据 |
| GET | `/api/models/search` | `SearchModelsMeta` | 搜索模型元数据 |
| GET | `/api/models/:id` | `GetModelMeta` | 获取模型元数据 |
| POST | `/api/models/` | `CreateModelMeta` | 创建模型元数据 |
| PUT | `/api/models/` | `UpdateModelMeta` | 更新模型元数据 |
| DELETE | `/api/models/:id` | `DeleteModelMeta` | 删除模型元数据 |
| GET | `/api/models/sync_upstream/preview` | `SyncUpstreamPreview` | 预览上游同步 |
| POST | `/api/models/sync_upstream` | `SyncUpstreamModels` | 同步上游模型 |
| GET | `/api/models/missing` | `GetMissingModels` | 获取缺失模型 |

### 部署管理 (io.net)

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/deployments/settings` | `GetModelDeploymentSettings` | 获取部署设置 |
| POST | `/api/deployments/settings/test-connection` | `TestIoNetConnection` | 测试 IoNet 连接 |
| GET | `/api/deployments/` | `GetAllDeployments` | 获取所有部署 |
| GET | `/api/deployments/search` | `SearchDeployments` | 搜索部署 |
| POST | `/api/deployments/test-connection` | `TestIoNetConnection` | 测试连接 |
| GET | `/api/deployments/hardware-types` | `GetHardwareTypes` | 获取硬件类型 |
| GET | `/api/deployments/locations` | `GetLocations` | 获取部署位置 |
| GET | `/api/deployments/available-replicas` | `GetAvailableReplicas` | 获取可用副本 |
| POST | `/api/deployments/price-estimation` | `GetPriceEstimation` | 价格估算 |
| GET | `/api/deployments/check-name` | `CheckClusterNameAvailability` | 检查集群名称 |
| POST | `/api/deployments/` | `CreateDeployment` | 创建部署 |
| GET | `/api/deployments/:id` | `GetDeployment` | 获取部署详情 |
| GET | `/api/deployments/:id/logs` | `GetDeploymentLogs` | 获取部署日志 |
| GET | `/api/deployments/:id/containers` | `ListDeploymentContainers` | 列出部署容器 |
| GET | `/api/deployments/:id/containers/:container_id` | `GetContainerDetails` | 获取容器详情 |
| PUT | `/api/deployments/:id` | `UpdateDeployment` | 更新部署 |
| PUT | `/api/deployments/:id/name` | `UpdateDeploymentName` | 更新部署名称 |
| POST | `/api/deployments/:id/extend` | `ExtendDeployment` | 延长部署 |
| DELETE | `/api/deployments/:id` | `DeleteDeployment` | 删除部署 |

### 系统状态测试

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/status/test` | `TestStatus` | 测试系统状态 |

---

## RootAuth 接口（超级管理员）

> 需要 `role >= 3`，包含所有 AdminAuth 接口权限

### 系统配置

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/option/` | `GetOptions` | 获取所有配置 |
| PUT | `/api/option/` | `UpdateOption` | 更新配置 |
| GET | `/api/option/channel_affinity_cache` | `GetChannelAffinityCacheStats` | 获取通道亲和缓存统计 |
| DELETE | `/api/option/channel_affinity_cache` | `ClearChannelAffinityCache` | 清除通道亲和缓存 |
| POST | `/api/option/rest_model_ratio` | `ResetModelRatio` | 重置模型比率 |
| POST | `/api/option/migrate_console_setting` | `MigrateConsoleSetting` | 迁移控制台设置 |

### 性能管理

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/performance/stats` | `GetPerformanceStats` | 获取性能统计 |
| DELETE | `/api/performance/disk_cache` | `ClearDiskCache` | 清除磁盘缓存 |
| POST | `/api/performance/reset_stats` | `ResetPerformanceStats` | 重置性能统计 |
| POST | `/api/performance/gc` | `ForceGC` | 强制 GC |

### 比率同步

| 方法 | 路径 | Controller | 说明 |
|------|------|------------|------|
| GET | `/api/ratio_sync/channels` | `GetSyncableChannels` | 获取可同步通道 |
| POST | `/api/ratio_sync/fetch` | `FetchUpstreamRatios` | 获取上游比率 |

---

## TokenAuth 接口（API Key 认证）

> 使用 API Token（`sk-xxx`）认证，用于 AI 模型 API 调用

### 认证方式

支持多种格式的 API Key：

| Header | 格式 | 适用场景 |
|--------|------|----------|
| `Authorization` | `Bearer sk-xxx` | OpenAI 格式 |
| `x-api-key` | `sk-xxx` | Claude 格式 |
| `x-goog-api-key` | `sk-xxx` | Gemini 格式 |
| `?key=xxx` | Query 参数 | Gemini 格式 |
| `mj-api-secret` | `sk-xxx` | Midjourney 格式 |
| `Sec-WebSocket-Protocol` | `openai-insecure-api-key.sk-xxx` | WebSocket Realtime |

### 模型列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/models` | OpenAI 格式模型列表 |
| GET | `/v1/models/:model` | 获取模型详情 |
| GET | `/v1beta/models` | Gemini 格式模型列表 |
| GET | `/v1beta/openai/models` | Gemini 兼容 OpenAI 格式 |

### 聊天补全

| 方法 | 路径 | 格式 | 说明 |
|------|------|------|------|
| POST | `/v1/chat/completions` | OpenAI | 聊天补全 |
| POST | `/v1/completions` | OpenAI | 文本补全（旧版） |
| POST | `/v1/messages` | Claude | Claude Messages API |

### Responses API (OpenAI)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/responses` | OpenAI Responses API |
| POST | `/v1/responses/compact` | OpenAI Responses（压缩格式） |

### Embedding

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/embeddings` | 文本向量化 |
| POST | `/v1/engines/:model/embeddings` | 文本向量化（Gemini 格式） |

### 图像生成

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/images/generations` | 图像生成 |
| POST | `/v1/images/edits` | 图像编辑 |
| POST | `/v1/edits` | 图像编辑（旧版） |
| POST | `/v1/images/variations` | 图像变体（未实现） |

### 音频处理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/audio/transcriptions` | 语音转文字 |
| POST | `/v1/audio/translations` | 语音翻译 |
| POST | `/v1/audio/speech` | 文字转语音 |

### 重排序

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/rerank` | 文档重排序 |

### 审核

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/moderations` | 内容审核 |

### Realtime API (WebSocket)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/realtime` | WebSocket 实时 API |

### Gemini API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1beta/models/*path` | Gemini 模型操作 |
| POST | `/v1/models/*path` | Gemini 模型操作（v1 路径） |

### Dashboard（账单查询）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/dashboard/billing/subscription` | 获取订阅信息 |
| GET | `/v1/dashboard/billing/subscription` | 获取订阅信息（v1 路径） |
| GET | `/dashboard/billing/usage` | 获取使用量 |
| GET | `/v1/dashboard/billing/usage` | 获取使用量（v1 路径） |

### Token 使用量

| 方法 | 路径 | 说明 | 限流 |
|------|------|------|------|
| GET | `/api/usage/token/` | 获取 Token 使用量 | CriticalRateLimit |

### 日志查询（Token 级别）

| 方法 | 路径 | 说明 | 备注 |
|------|------|------|------|
| GET | `/api/log/token` | 按 Key 查询日志 | 支持 CORS |

### Midjourney

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/mj/image/:id` | 获取 MJ 图片（无需认证） |
| POST | `/mj/submit/imagine` | 提交 imagine 任务 |
| POST | `/mj/submit/action` | 提交 action 任务 |
| POST | `/mj/submit/shorten` | 提交 shorten 任务 |
| POST | `/mj/submit/modal` | 提交 modal 任务 |
| POST | `/mj/submit/change` | 提交 change 任务 |
| POST | `/mj/submit/simple-change` | 提交 simple-change 任务 |
| POST | `/mj/submit/describe` | 提交 describe 任务 |
| POST | `/mj/submit/blend` | 提交 blend 任务 |
| POST | `/mj/submit/edits` | 提交 edits 任务 |
| POST | `/mj/submit/video` | 提交 video 任务 |
| POST | `/mj/notify` | MJ 回调通知 |
| GET | `/mj/task/:id/fetch` | 获取任务状态 |
| GET | `/mj/task/:id/image-seed` | 获取图片种子 |
| POST | `/mj/task/list-by-condition` | 条件查询任务 |
| POST | `/mj/insight-face/swap` | 换脸 |
| POST | `/mj/submit/upload-discord-images` | 上传 Discord 图片 |

> 注：以上 MJ 接口也支持 `/:mode/mj/` 前缀格式

### Suno 音乐生成

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/suno/submit/:action` | 提交 Suno 任务 |
| POST | `/suno/fetch` | 获取 Suno 任务 |
| GET | `/suno/fetch/:id` | 获取指定 Suno 任务 |

### 视频生成

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/video/generations` | 创建视频生成任务 |
| GET | `/v1/video/generations/:task_id` | 获取视频任务状态 |
| POST | `/v1/videos` | 创建视频（OpenAI 兼容） |
| GET | `/v1/videos/:task_id` | 获取视频状态 |
| GET | `/v1/videos/:task_id/content` | 获取视频内容 |
| POST | `/v1/videos/:video_id/remix` | 视频混音 |

### Kling 视频生成

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/kling/v1/videos/text2video` | 文生视频 |
| POST | `/kling/v1/videos/image2video` | 图生视频 |
| GET | `/kling/v1/videos/text2video/:task_id` | 获取文生视频任务 |
| GET | `/kling/v1/videos/image2video/:task_id` | 获取图生视频任务 |

### 即梦 (Jimeng) 视频生成

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/jimeng/` | 即梦官方 API（支持各种 Action） |

---

## 错误码说明

### HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 401 | 未认证（未登录或 Token 无效） |
| 403 | 无权限（用户被封禁或权限不足） |
| 429 | 请求过于频繁（限流） |
| 500 | 服务器内部错误 |

### 业务错误响应格式

```json
{
  "success": false,
  "message": "错误信息"
}
```

### 常见错误信息

| 错误信息 | 说明 |
|----------|------|
| `无权进行此操作，未登录且未提供 access token` | 需要认证 |
| `无权进行此操作，access token 无效` | Token 无效或过期 |
| `无权进行此操作，未提供 New-Api-User` | 缺少 New-Api-User Header |
| `无权进行此操作，New-Api-User 格式错误` | New-Api-User 不是有效数字 |
| `无权进行此操作，New-Api-User 与登录用户不匹配` | New-Api-User 与 Token 用户不匹配 |
| `用户已被封禁` | 用户状态为禁用 |
| `无权进行此操作，权限不足` | 权限等级不足 |
| `您的 IP 不在令牌允许访问的列表中` | IP 白名单限制 |

---

## 接口统计

| 权限级别 | 接口数量 |
|----------|----------|
| 公开接口 | ~30 |
| UserAuth | ~45 |
| AdminAuth | ~100 |
| RootAuth | ~15 |
| TokenAuth（API） | ~60 |

**总计**: 约 250+ 个 API 接口

---

## 版本信息

- 文档版本: 1.0.0
- 项目: New API
- 仓库: https://github.com/QuantumNous/new-api
