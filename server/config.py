"""配置常量与环境（.env 加载、代理、UA、全部文件路径、并发上限）。

设计约定：本模块是唯一的常量住所，业务域一律 `from server.config import <名字>`；
测试要改写某个路径常量时 patch **被测模块**的绑定（如 `server.usage.USAGE_FILE`），
与此前 patch `bs.USAGE_FILE` 的语义一致（bs 过渡期重导出同一批名字）。

import 本模块即自动加载 .env（已存在的环境变量优先），保证独立导入 server.* 时
env 派生值依然正确。
"""

import os
import re
from pathlib import Path

# ── .env 加载 ────────────────────────────────────────────────────────────────


def _load_dotenv() -> None:
	"""极简 .env 加载：KEY=VALUE 每行一条，# 开头是注释；已存在的环境变量优先于 .env。"""
	env_file = Path(__file__).parent.parent / '.env'
	try:
		for line in env_file.read_text(encoding='utf-8').splitlines():
			line = line.strip()
			if not line or line.startswith('#') or '=' not in line:
				continue
			key, _, value = line.partition('=')
			key, value = key.strip(), value.strip().strip('\'"')
			if key and key not in os.environ:
				os.environ[key] = value
	except OSError:
		pass  # 没有 .env 就全靠环境变量


_load_dotenv()

# ── 代理与出口 ───────────────────────────────────────────────────────────────
# .env 已在上面加载完毕，env 派生值在此之后计算（原先 _PROXY 在 _load_dotenv 之前
# 求值，.env 里的 HTTPS_PROXY 实际不生效 —— 迁移顺带修正了这个顺序问题）。

_LOCAL_PROXY = 'http://127.0.0.1:7890'
_PROXY = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY') or _LOCAL_PROXY
_AGENTROUTER_PROXY = _LOCAL_PROXY

# 上游请求统一的浏览器 UA
USER_AGENT = (
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
)

# ── 文件路径（都在服务目录下；测试会改写它们指向 tmp_path）──────────────────
_BASE_DIR = Path(__file__).parent.parent

CONFIG_FILE = _BASE_DIR / 'saved_config.json'
NEW_ACCOUNTS_FILE = _BASE_DIR / 'new_accounts_config.json'
USAGE_FILE = _BASE_DIR / 'daily_usage.json'
AGENTROUTER_ACCOUNTS_FILE = _BASE_DIR / 'agentrouter_accounts.json'
CHECKIN_STATE_FILE = _BASE_DIR / 'checkin_state.json'
ANYROUTER_CHECKIN_STATE_FILE = _BASE_DIR / 'anyrouter_checkin_state.json'
CHECKIN_SETTINGS_FILE = _BASE_DIR / 'checkin_settings.json'
NEWAPI_SITES_FILE = _BASE_DIR / 'newapi_sites.json'
KEYS_CACHE_FILE = _BASE_DIR / 'keys_cache.json'
AGENTROUTER_SESSION_FILE = _BASE_DIR / 'agentrouter_sessions.json'

# ── mihomo 出口轮换 ─────────────────────────────────────────────────────────

MIHOMO_CONFIG_FILE = Path(os.environ.get('MIHOMO_CONFIG') or Path.home() / 'mihomo' / 'config.yaml')
MIHOMO_GROUP = os.environ.get('MIHOMO_GROUP', '')  # mihomo 代理组名，出口轮换用；空 = 不轮换（行为安全降级）
# 组里混着信息项（剩余流量/官网）和子分组（自动选择/故障转移），还有全部不可达的 V6 节点，都跳过
MIHOMO_NODE_SKIP = re.compile(r'剩余流量|重置|到期|建议|官网|自动选择|故障转移|V6')

# ── 速率与间隔 ───────────────────────────────────────────────────────────────

# Login 账号签到节奏：每个账号之间随机等待 30~60 分钟，避免登录接口按 IP 限流（429）
CHECKIN_MIN_DELAY = 1800  # 30 分钟（默认；实际间隔以 checkin_settings 的 agentrouter_gap_min/max 为准）
CHECKIN_MAX_DELAY = 3600  # 60 分钟

# 防护 cookies 缓存 TTL（秒）
WAF_CACHE_TTL = 300  # 5 分钟

# 单站并发上限。总并发受线程池 32 约束；AgentRouter 的 token 与 cookie 两类账号会同时
# 查询，因此 12 × 2 = 24 仍在池容量内。Login（agentrouter.org）不在此列——登录接口按 IP 限流。
ANYROUTER_CONCURRENCY = 12
# 通用 new-api 站点的默认并发，可被单个站点配置里的 concurrency 覆盖
NEWAPI_CONCURRENCY = 10

# ── new-api 令牌接口 ─────────────────────────────────────────────────────────

TOKEN_LIST_PATH = '/api/token/'
TOKEN_PAGE_SIZE = 100
KEYS_CACHE_MAX_AGE = 30 * 24 * 3600  # 保存时清掉一个月没碰过的条目，防无限增长
AGENTROUTER_SESSION_TTL = 6 * 3600
