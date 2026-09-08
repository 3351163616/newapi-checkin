"""mihomo 代理域：controller 定位与 API 封装、出口 IP 探测、WAF 探针、
出口轮换底座与两个 Rotator（agentrouter 的 WAF 轮换 / 密钥取全量的轻量轮换）。

过渡期约定（拆分块D，同 server/keys.py）：不做模块级 `import balance_server`，跨实体
引用（AGENTROUTER_ORG_CONFIG、agentrouter_block_reason、线程池、_UPSTREAM_POOL、
WAF pacing 常量等可被测试 patch 的名字）在函数体内晚绑定 `bs.<名字>`。

_exit_generation 计数器被测试经 bs 观察与重绑，故家在 balance_server、本模块一律
经 `bs._exit_generation` 读写。
"""

import asyncio
import re
import time
from urllib.parse import quote
from fastapi import APIRouter

mihomo_router = APIRouter()


def _ar_session_key(base: str) -> str:
	"""agentrouter 专用连接池 key，带出口代数。

	mihomo 切换节点**不会杀掉已建立的 keep-alive 隧道**，复用 Session 会继续从旧出口
	出去（2026-08-22 实测：连切三个节点，复用连接的出口 IP 纹丝不动，新建连接才跟随切换）。
	ExitRotator 每次切换出口把代数 +1 逼请求重新建连 —— 不带代数的话轮换形同虚设，
	所有批次都从第一个出口 IP 出去，那个 IP 的 WAF 预算瞬间打满。
	"""
	import balance_server as bs

	return f'{base}:g{bs._exit_generation}'


def _mihomo_controller() -> tuple[str, str] | None:
	"""从 mihomo 配置读 (controller 地址, secret)；读不到返回 None（此时不轮换）。"""
	import balance_server as bs

	try:
		text = bs.MIHOMO_CONFIG_FILE.read_text(encoding='utf-8')
	except OSError:
		return None
	addr = secret = None
	for line in text.splitlines():
		if addr is None and line.startswith('external-controller:'):
			addr = line.split(':', 1)[1].strip()
		elif secret is None and line.startswith('secret:'):
			secret = line.split(':', 1)[1].strip().strip('\'"')
	if not addr:
		return None
	host, _, port = addr.rpartition(':')
	host = host.strip('[]')
	if host in ('0.0.0.0', '::', ''):
		host = '127.0.0.1'
	return f'http://{host}:{port}', secret or ''


def _mihomo_call(method: str, url: str, secret: str, body: dict | None = None):
	"""同步调 mihomo controller；异常返回 None（本地服务，失败就当不可用）。"""
	from curl_cffi import requests as cffi_requests

	try:
		return cffi_requests.request(
			method, url, headers={'Authorization': f'Bearer {secret}'}, json=body, timeout=8,
		)
	except Exception:
		return None


async def _query_egress_ip() -> str | None:
	"""当前代理出口的公网 IP。切完节点必须核对它 —— 节点名不同 ≠ 出口 IP 不同
	（实测原生 03/04 同 IP，专线 01/02 与 IPLC06 同 IP）。"""
	import balance_server as bs

	from curl_cffi import requests as cffi_requests

	proxies = {'https': bs._LOCAL_PROXY, 'http': bs._LOCAL_PROXY}

	def _do():
		for url in ('https://api.ip.sb/ip', 'https://ifconfig.me/ip'):
			try:
				r = cffi_requests.get(url, proxies=proxies, timeout=6)
				if r.status_code == 200 and r.text.strip():
					return r.text.strip()
			except Exception:
				continue
		return None

	return await asyncio.get_running_loop().run_in_executor(bs._UPSTREAM_POOL, _do)


async def _probe_exit_passes_waf() -> bool:
	"""匿名探测当前出口能否过 agentrouter 的 WAF。

	打 /api/user/self 但带一次性假 cookie —— 即使被滑块标记，标记的也是这个假 cookie，
	不伤真账号的 session。拿到 JSON（401 也算）= 放行；滑块页/429/不可达 = 不放行。
	实测（2026-08-22）：原生/家宽/专线 IP 基本都过，廉价数据中心（Vless 系）被拦，
	与地区无关 —— 所以轮换池不挑地区，谁能过用谁。
	"""
	import balance_server as bs

	from curl_cffi import requests as cffi_requests

	def _do():
		try:
			r = cffi_requests.get(
				f'{bs.AGENTROUTER_ORG_CONFIG["domain"]}{bs.AGENTROUTER_ORG_CONFIG["user_info_path"]}',
				headers={
					'User-Agent': bs.USER_AGENT,
					'Accept': 'application/json, text/plain, */*',
					'new-api-user': '1',
					'Authorization': 'Bearer probe',
					'cookie': 'session=probe',
				},
				proxies={'https': bs._AGENTROUTER_PROXY, 'http': bs._AGENTROUTER_PROXY},
				impersonate='chrome131',
				timeout=6,
			)
			return bs.agentrouter_block_reason(r) is None
		except Exception:
			return False

	return await asyncio.get_running_loop().run_in_executor(bs._UPSTREAM_POOL, _do)


class _MihomoGroupSwitcher:
	"""mihomo 代理组切换的公共底座：controller 定位、API 封装、原节点记忆与恢复。

	ExitRotator（余额/全签的 WAF 出口轮换）与 _KeysExitRotator（取密钥撞限流换出口）
	共用这套机制，差别只在「怎么挑下一个节点」的策略（子类实现）。
	"""

	def __init__(self):
		self._base, self._secret = None, None
		self._original = None
		self._nodes: list[str] = []
		self._idx = -1
		self._touched = False

	async def _api(self, method: str, path: str, body: dict | None = None):
		import balance_server as bs

		resp = await asyncio.get_running_loop().run_in_executor(
			bs._UPSTREAM_POOL, bs._mihomo_call, method, f'{self._base}{path}', self._secret, body,
		)
		if resp is None or resp.status_code >= 300:
			return None
		if method == 'GET':
			try:
				return resp.json()
			except Exception:
				return None
		return {}

	async def _select(self, node: str) -> bool:
		import balance_server as bs

		ok = await self._api('PUT', f'/proxies/{quote(bs.MIHOMO_GROUP)}', {'name': node})
		if ok is not None:
			self._touched = True
		return ok is not None

	async def restore(self) -> None:
		import balance_server as bs

		if self._touched and self._original:
			if await self._select(self._original):
				bs._exit_generation += 1  # 恢复原节点后同样要重新建连
			self._touched = False


class ExitRotator(_MihomoGroupSwitcher):
	"""查询期间轮换 mihomo 出口 IP，结束后恢复原节点。

	候选是组里**全部真实节点**（不限地区）：按实际出口 IP 去重后逐个用匿名探针实测能否过
	agentrouter 的 WAF，能过的都进轮换池（2026-08-22 实测约 10 个独立 IP：香港原生/新加坡/
	日本专线/韩国家宽/美国原生与专线）。探测结果缓存 WAF_PASS_CACHE_TTL —— 探测要 1~2 分钟，
	而且探测本身也在消耗各 IP 的 WAF 配额。

	controller 读不到 / 凑不出能过的节点时 start() 返回 False，查询退化为「不换 IP 只分批」。
	探测要切节点（期间影响用户经 7890 的其它流量），限时 150 秒。
	"""

	def __init__(self):
		super().__init__()
		self._ips: list[str] = []

	@property
	def ip_count(self) -> int:
		return len(self._ips)

	async def start(self) -> bool:
		"""发现能过 WAF 的出口节点，记住原节点。优先用缓存。"""
		import balance_server as bs

		ctl = bs._mihomo_controller()
		if not ctl:
			return False
		self._base, self._secret = ctl
		info = await self._api('GET', f'/proxies/{quote(bs.MIHOMO_GROUP)}')
		if not info or not info.get('all'):
			return False
		self._original = info.get('now')
		if _waf_pass_cache['nodes'] and time.time() - _waf_pass_cache['ts'] < bs.WAF_PASS_CACHE_TTL:
			pairs = [(n, ip) for n, ip in zip(_waf_pass_cache['nodes'], _waf_pass_cache['ips']) if n in info['all'] and n != self._original]
			if pairs:
				# 缓存路径不再核对出口 IP（节点 IP 会漂移）；漂移导致撞上热 IP 由运行时的预算/热度记账兜底
				self._nodes = [n for n, _ in pairs]
				self._ips = [ip for _, ip in pairs]
				self._idx = -1
				return True
		used = set()
		if self._original:
			used.add(self._original)
		orig_ip = await bs._query_egress_ip()
		if orig_ip:
			used.add(orig_ip)  # 用户刚被拦过的 IP，别再拿它查询
		t0 = time.time()
		for name in info['all']:
			if time.time() - t0 > 150:
				break
			if bs.MIHOMO_NODE_SKIP.search(name) or name in used:
				continue
			if not await self._select(name):
				continue
			ip = await bs._query_egress_ip()
			if not ip or ip in used:
				continue  # 不可达，或与已试过的节点同 IP（同 IP 的 WAF 行为相同）
			used.add(ip)
			if await bs._probe_exit_passes_waf():
				self._nodes.append(name)
				self._ips.append(ip)
		if not self._nodes:
			await self.restore()
			return False
		_waf_pass_cache.update(ts=time.time(), nodes=list(self._nodes), ips=list(self._ips))
		print(f'[AGENTROUTER] 出口探测完成：{len(self._ips)} 个能过 WAF 的独立 IP')
		self._idx = -1
		return True

	async def next_ip(self) -> None:
		"""切到下一个出口 IP；池子小就轮着用（轮与轮的间隔由调用方控制）。"""
		import balance_server as bs

		if not self._nodes:
			return
		self._idx = (self._idx + 1) % len(self._nodes)
		if await self._select(self._nodes[self._idx]):
			bs._exit_generation += 1  # 逼后续请求重新建连，否则旧隧道还钉在上一个出口上

	@property
	def current(self) -> int:
		"""当前用的是第几个出口 IP（-1 表示还没切过）"""
		return self._idx


# 「哪些节点能过 WAF」的探测结果缓存（探测要 1~2 分钟，别每次点都来）
WAF_PASS_CACHE_TTL = 30 * 60
_waf_pass_cache: dict = {'ts': 0.0, 'nodes': [], 'ips': []}
_balances_query_lock = asyncio.Lock()  # 轮换出口是全局动作，同时只能跑一轮查询


class _KeysExitRotator(_MihomoGroupSwitcher):
	"""取密钥撞限流时的轻量出口轮换。

	与 ExitRotator（agentrouter 专用，要探 WAF、能过的才进池）不同：这里撞的是站点自己的
	频控而不是 WAF，不挑节点，只要实际出口 IP 没用过就行。所有请求都走一次性新建连接，
	不存在 keep-alive 隧道钉死旧出口的问题（agentrouter 轮换踩过的坑）。
	"""

	def __init__(self):
		super().__init__()
		self.tried_ips: set[str] = set()

	async def prepare(self) -> bool:
		"""读节点列表、记住原节点。mihomo 不可用时返回 False（调用方就不轮换）。"""
		import balance_server as bs

		ctl = bs._mihomo_controller()
		if not ctl:
			return False
		self._base, self._secret = ctl
		info = await self._api('GET', f'/proxies/{quote(bs.MIHOMO_GROUP)}')
		if not info or not info.get('all'):
			return False
		self._original = info.get('now')
		self._nodes = [n for n in info['all'] if n != self._original and not bs.MIHOMO_NODE_SKIP.search(n)]
		return True

	async def next_exit(self) -> bool:
		"""切到下一个没用过的节点（按实际出口 IP 去重 —— 多个节点共用出口很常见）。"""
		import balance_server as bs

		while self._idx + 1 < len(self._nodes):
			self._idx += 1
			if not await self._select(self._nodes[self._idx]):
				continue
			ip = await bs._query_egress_ip()
			if ip:
				if ip in self.tried_ips:
					continue
				self.tried_ips.add(ip)
			return True
		return False


# ===== 端点（块E 自 balance_server.py 迁入，晚绑定 bs.<名字>）=====

@mihomo_router.get('/api/system/proxy-info')
async def get_proxy_info(probe: bool = False):
	import balance_server as bs
	"""只读展示代理相关配置，供前端「设置」页做诊断。

	这些值来自环境变量 / .env，前端改不了（改完要重启服务），所以纯只读。
	之所以值得暴露：访问 anyrouter / agentrouter 必须走本地代理，代理没起来时
	表现为一句藏在服务端日志里的 `[WAF] Failed to connect to 127.0.0.1:7890`，
	用户在界面上完全看不到，只会觉得"查询莫名其妙失败"。

	带 ?probe=true 时会实际连一下代理端口确认是否在跑。
	"""
	source = 'default'
	if os.environ.get('HTTPS_PROXY'):
		source = 'HTTPS_PROXY'
	elif os.environ.get('HTTP_PROXY'):
		source = 'HTTP_PROXY'

	reachable = None
	if probe:
		parsed = urlparse(_PROXY)
		if parsed.hostname and parsed.port:
			reachable = await probe_tcp(parsed.hostname, parsed.port)

	return {
		'success': True,
		'proxy': {
			'url': mask_proxy_url(_PROXY),
			'source': source,
			'has_credentials': '@' in _PROXY.split('://', 1)[-1].split('/', 1)[0],
			'reachable': reachable,
		},
		'mihomo': {
			'group': MIHOMO_GROUP,
			# 组名为空时不做出口轮换，是有意的安全降级而非故障
			'rotation_enabled': bool(MIHOMO_GROUP),
			'config_path': str(MIHOMO_CONFIG_FILE),
			'config_exists': await asyncio.to_thread(MIHOMO_CONFIG_FILE.is_file),
		},
	}


