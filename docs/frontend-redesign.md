# 前端重构方案

> 本文档记录 newapi-checkin 前端从「单文件零构建」重构为「Vite + React + Tailwind v4」的
> 全部决策、依据与执行计划。所有结论均基于实测或源码行号，不含记忆推断。
>
> 立项日期：2026-08-26

---

## 一、现状勘察（实测证据）

### 1.1 环境基线

| 项目 | 实测结果 |
|---|---|
| 仓库 | `https://github.com/3351163616/newapi-checkin`，5 commits，最新 `73905c5` |
| 后端 | `balance_server.py`，4335 行单文件 FastAPI |
| 前端 | `templates/index.html`，3855 行单文件（HTML 70–904，主 JS 905–3853） |
| 样式 | Tailwind **CDN**（`cdn.tailwindcss.com`），无构建步骤 |
| 依赖管理 | `uv`（`uv.lock`），Python 3.13 虚拟环境 |
| 后端测试 | `uv run pytest -q` → **134 passed, 1 skipped** |
| 前端测试 | `node tests/test_site_frontend.mjs` → **30 项通过**，全假数据驱动 |
| 服务 | `127.0.0.1:8003`，HTTP 200 |

### 1.2 前端热更新特性

`balance_server.py:2005` 每次 `GET /` 都重新 `open('templates/index.html')` 读盘：

```python
@app.get('/', response_class=HTMLResponse)
async def index():
    with open('templates/index.html', encoding='utf-8') as f:
```

→ **改前端只需刷新浏览器，无需重启服务**。重构后需保持等价的开发体验（靠 Vite HMR）。

### 1.3 实地发现的硬伤

以下问题经浏览器实机截图 + 源码双重验证：

| # | 问题 | 证据 |
|---|---|---|
| 1 | 按钮文字被挤换行 | 截图可见「AnyRouter 签/到」「AgentRouter/签到」在 2 列 grid 内断行 |
| 2 | 5 个按钮撞色平铺 | 紫/绿/青/亮青/蓝全高饱和，无主次层级 |
| 3 | 左右栏严重失衡 | 右侧「查询结果」空态下方近半屏空白 |
| 4 | **Footer 文案与实现不符** | `index.html:901` 写 `Powered by Playwright`，但 `balance_server.py:542` 注释明确说 Playwright 因 JA3 指纹被 WAF 拦截**已弃用**，现用 `curl_cffi impersonate='chrome131'`（`:247` `:560` `:1086`）；`pyproject.toml` 依赖中根本没有 playwright |
| 5 | 标题写死 "AnyRouter" | `index.html:6` `:75` `:112` 三处；但顶部有 4 个站点且支持任意 new-api 站点接入 |
| 6 | 密码框不在 `<form>` 内 | 浏览器 console 实测告警：回车无法提交、密码管理器不识别、缺 `autocomplete` |

### 1.4 根本病因

以上是表征。**根本问题是信息架构**：

现有设计**强制先切站点、才能看该站点的账号**。但本工具的核心价值恰恰是「**批量**管理多站点」——
四个站点的余额要点四次、看四次、在脑子里加总。那个站点切换器不是功能，是**信息架构的补丁**。

所有功能（账号 / 签到 / 密钥 / 站点 / 监控）被硬塞进一个 `grid md:grid-cols-2` 双栏里，
这是堆砌，不是设计。**本次重构必须重做 IA，而不只是换皮。**

---

## 二、技术选型

### 2.1 选型结论

对标 **grok2api v3.1.5**（`https://github.com/chenyme/grok2api`）的前端技术栈。

> 已验证：本地 `/mnt/d/CliProxyApi/tools/grok2api/frontend/` 那份与 upstream v3.1.5
> **逐项零差异**（依赖、`index.css`、features 目录、shadcn 组件全部一致），可安全作为权威参考。

| 层 | 选型 |
|---|---|
| 构建 | Vite 8 |
| 框架 | React 19 + TypeScript 6 |
| 样式 | Tailwind CSS v4（`@tailwindcss/vite`，CSS-first，**无 `tailwind.config.js`**） |
| 组件 | shadcn/ui（`style=new-york`, `baseColor=neutral`, `cssVariables=true`） |
| 图标 | lucide-react |
| 数据 | TanStack Query v5 |
| 表单 | react-hook-form + zod |
| 图表 | recharts（经 shadcn `components/ui/chart.tsx` 封装） |
| 通知 | sonner |
| 路由 | react-router-dom v7 |
| 动画 | tw-animate-css |
| 包管理 | pnpm 11.5.2（`corepack enable pnpm` 实测可用） |

### 2.2 registry 配置（实测依据）

| 源 | 耗时 | 速度 |
|---|---|---|
| `registry.npmjs.org` | **13.31s** | 519 KB/s |
| `registry.npmmirror.com` | **0.43s** | 15.7 MB/s |

→ **31 倍耗时差**。在 `frontend/.npmrc` 配置镜像（**项目级，不动全局配置**，避免污染其他项目）。

### 2.3 风格选型过程（含一次方向修正）

初期候选自 StyleKit（`https://www.stylekit.top`，146 个风格，API 实拉非记忆）：

- 曾选定 `linear-style`，但发现三处与本项目性质的冲突：
  - Linear 的 `forbidden` 列表含 **`font-mono`**，而密钥管理工具的 `sk-xxx` / token / ID
    必须等宽（对齐、防 `0/O`、`l/1` 混淆）
  - Linear 禁 **`rounded-full`**，而「每日自动签到」是 Switch 开关
  - Linear 的 AI 规则写死 `duration-150` / `No scale transforms`，与「丰富过场」诉求对冲
- **最终决策：放弃 Linear，直接采用 grok2api 的设计语言。**
  理由：① 它是已跑通的成品而非纸面 spec；② token 体系可直接复制；③ 审美已对齐。
  顺带这三个冲突自动消失——grok2api 的按钮本来就是 `rounded-full`。

---

## 三、设计系统

### 3.1 Token 架构（移植自 grok2api `src/index.css`，170 行）

核心手法：

- **oklch 色彩空间**（感知均匀，优于 hsl）
- **UI 部分全走 neutral 灰阶**（`chroma = 0`，纯灰），只有 `destructive` 带色相
- `:root` / `.dark` 双主题 CSS 变量 → `@theme inline` 映射到 Tailwind v4
- 语义分层：`background / foreground / card / popover / primary / secondary / muted / accent / destructive / border / input / ring / sidebar*`

关键洞察——grok2api 用 `--quota-product-0..6` 这组**业务专用彩色 token** 解决了
「UI 要极简、但数据要靠颜色区分」的矛盾：**界面走灰阶，数据可视化用彩色**。

### 3.2 本项目扩展的业务 token

仿照上述做法新增：

```css
--checkin-done      /* 已签到 */
--checkin-pending   /* 待签到 */
--checkin-failed    /* 签到失败 */
--site-0 .. --site-N /* 各站点标识色：AnyRouter / AgentRouter / GoRouter / TaBiAI / 后续接入 */
--balance-low       /* 余额告警 */
```

### 3.3 组件规格（照搬 grok2api）

| 元素 | 规格 |
|---|---|
| 按钮 | `rounded-full` 胶囊形，紧凑 `h-8 px-3 text-xs`（`lg` 为 `h-9 px-5 text-sm`） |
| 主按钮 | dark 下 primary = `oklch(0.96 0 0)` 近白 → **白底黑字** |
| hover | `bg-primary/84`（精调值，非随手的 90） |
| 页头 | `text-xl font-medium`，description 用 `sr-only` |
| 导航项 | `h-8 rounded-md px-2.5 text-xs text-muted-foreground hover:bg-secondary/55 hover:text-foreground` |
| 字体 | Inter，body 14px，`text-rendering: optimizeLegibility` |
| 滚动条 | 5px 细定制 |
| 三态 | `LoadingState`(Spinner) / `EmptyState`(Inbox) / `ErrorState`(AlertCircle + 重试) |

---

## 四、新信息架构

### 4.1 核心决策：全局聚合，站点降为筛选

**站点从「必须先选的模式开关」降级为「可选的筛选器 + 行上的色标签」。**
所有页面默认展示全部站点数据。

```
┌─────────┬──────────────────────────────────┐
│ ■ 总览   │  总览                             │
│   账号   │  ┌────────┐┌────────┐┌────────┐  │
│   签到   │  │ 总余额  ││ 今日用量││ 待签 3 │  │
│   密钥   │  │ $284.10││ $12.44 ││ 已签 9 │  │
│   用量   │  └────────┘└────────┘└────────┘  │
│   站点   │                                   │
│   设置   │  账号  [全部▾] [搜索___]          │
│         │  ● AnyRouter    a@x.com   $88.20  │
│         │  ● AgentRouter  b@x.com   $42.00  │
│         │  ● GoRouter     c@x.com   $18.90  │
│         │  ● TaBiAI       d@x.com  $135.00  │
└─────────┴──────────────────────────────────┘
● = 站点色标签（--site-N token）
```

### 4.2 路由表

对标 grok2api 的 `app/router.tsx`（AppShell + AuthBoundary + 懒加载分包）：

| 路由 | 页面 | 内容 |
|---|---|---|
| `/login` | 登录 | 独立布局，无 AppShell |
| `/dashboard` | 总览 | 跨站点余额汇总、今日用量、签到状态、告警、趋势图 |
| `/accounts` | 账号管理 | 全站点账号表格，站点色标签，可筛选/搜索；可视化 ↔ JSON 双模式；ID 去重 |
| `/checkin` | 签到中心 | 签到状态面板、手动/批量签到、自动签到开关、浏览器脚本、Cookie 续期 |
| `/keys` | API 密钥 | 密钥列表 / 新建 / 删除 / 批量复制 |
| `/usage` | 用量记录 | 90 天历史图表与统计分析 |
| `/sites` | 站点管理 | new-api 站点接入配置 |
| `/settings` | 设置 | 监控告警、SMTP、代理配置 |

### 4.3 目录结构（Feature-Sliced Design）

```
frontend/src/
├── app/          # router / app-shell / providers / auth-boundary / deferred-pages
├── components/ui # shadcn 组件
├── features/     # auth accounts checkin keys usage sites settings dashboard
├── shared/       # api auth components config hooks lib i18n
└── types/
```

feature 内部约定（同 grok2api）：`xxx-api.ts` + `xxx-page.tsx` + 若干组件。

---

## 五、数据分析面板

### 5.1 数据源（源码实证）

**`daily_usage.json`**（`balance_server.py:4066-4092`），保留 90 天：

```json
{
  "2026-08-26": {
    "站点:账号名": { "used": 已用量, "quota": 总额度, "used0": 当天基线 }
  }
}
```

- 今日用量 = `used - used0`
- `used0` 是当天第一次记录的已用量，写入后当天不再改动

**签到状态**（`balance_server.py:361-391`）：

```python
{ 'running', 'date', 'started_at', 'finished_at', 'trigger',
  'total', 'signed', 'already', 'failed',
  'accounts': { name: {status: pending|signed|already|failed, message, time} },
  'logs': [...最近 100 条] }
```

⚠️ **已知限制**：`date` 是单个日期，只保存**当前这一轮**，每次签到覆盖。
→ 签到历史**未持久化**，「连签天数」「热力图」无法从中直接获得。

### 5.2 绕过限制的方案

**签到成功的本质是 quota 增加。** 从 `daily_usage.json` 的 90 天 `quota` 日增量可**反推**签到历史，
零后端改动即可绘制热力图（近似值）。

### 5.3 面板清单

| # | 统计项 | 数据来源 |
|---|---|---|
| 1 | 余额趋势（90 天曲线） | `quota - used` |
| 2 | 每日消耗 | `used - used0` |
| 3 | 消耗速率 7/30 日均值 + 环比 | 同上聚合 |
| 4 | 站点 / 账号占比饼图 | 按 `usage_key` 前缀聚合 |
| 5 | Top 消耗账号排行 | 同上排序 |
| 6 | **余额耗尽预测** | 近期速率 → 推算剩余天数 |
| 7 | **签到收益分析** | quota 日增量 = 签到所得额度 |
| 8 | **签到热力图** | quota 增量反推（近似） |
| 9 | 今日签到成功率 + 失败原因分布 | `checkin_state.accounts` |
| 10 | 账号健康度 | 余额告警 / 长期未签 / Cookie 过期 |

第 6、7、8 项为本项目独有，grok2api 没有。

### 5.4 图表实现参考

grok2api `features/dashboard/`（1077 行）：

| 文件 | 行数 | 图表 |
|---|---|---|
| `dashboard-trend.tsx` | 267 | ComposedChart（Area + Bar + Line 三合一） |
| `dashboard-overview.tsx` | 189 | PieChart |
| `dashboard-provider-distribution.tsx` | 145 | 分布 |
| `dashboard-top-models.tsx` | 110 | Top 排行 |
| `dashboard-activity.tsx` | 110 | 活动流 |
| `components/ui/chart.tsx` | 367 | recharts 的 shadcn 封装 |

---

## 六、动效方案

**定位：动效优先，允许适度破例。**

grok2api 现有动效偏克制（实测统计：`transition-colors` ×36、`animate-in/out` ×11、
`fade/zoom/slide` 系列、`duration-200/300` 为主），本项目在其基础上**加码**：

- 列表 **stagger 错峰入场**
- 页面切换 **View Transitions**
- Modal **弹性开合**
- 数字 **滚动计数**
- 图表 **绘制动画**
- 签到成功的**状态反馈动效**
- 全程尊重 `prefers-reduced-motion`

可调用的本地 skill：`find-animation-opportunities`（找机会点）→ `improve-animations`（出方案）
→ `review-animations`（审查）。

---

## 七、验证策略

**本项目是他人的开源仓库，不依赖真实历史数据。**
功能按完整实现，最终**注入 mock 数据验证显示效果**。

这与项目既有传统一致——`tests/test_site_frontend.mjs` 的 30 项测试本就是**全假数据驱动**，
README 亦写明「离线测试全假数据驱动」。

---

## 八、分阶段交付

每阶段都能真实跑起来预览，方向不对可及早掉头。

| 阶段 | 内容 | 验收 |
|---|---|---|
| **一** | 脚手架 + token 体系 + AppShell 侧边栏骨架 | 能看到新布局骨架 |
| **二** | 核心页面迁移（auth / accounts / checkin / keys / sites / settings） | 功能对齐，无遗漏 |
| **三** | 数据分析面板（10 项统计 + 图表） | mock 数据验证显示效果 |
| **四** | 动效打磨 + 设计审计 | `design-review` 通过 |

---

## 九、环境配置备忘

```bash
# 后端依赖（已完成）
uv sync                      # 注：本机 uv 走清华镜像，会重写 uv.lock 的下载 URL
                             # （版本与 sha256 不变，仅 URL 变）。用 --frozen 可避免
uv run --frozen pytest -q    # 134 passed, 1 skipped

# 启动后端
.venv/bin/python -m uvicorn balance_server:app --host 127.0.0.1 --port 8003
# 登录凭据在 .env（首启自动生成随机密码，已被 .gitignore 排除）

# 前端（阶段一后）
cd frontend && pnpm install && pnpm dev    # Vite dev server，proxy → 127.0.0.1:8003
```

> ⚠️ `pkill -f "uvicorn balance_server"` 会误杀发起命令的 shell 自身
> （`pgrep -f` 匹配的是命令行文本，包含了该字符串）。
> 请改用按端口定位：`ss -lptn 'sport = :8003'`。
