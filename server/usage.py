"""每日用量统计域（daily_usage.json：0 点快照、增量记录、key 迁移、历史查询）。

过渡期约定（拆分第二块，同 server/keys.py）：
- 本模块不做模块级 `import balance_server`，由 balance_server 在自身定义序列中
  导入并重导出公开名，避免循环导入；
- 跨实体引用（USAGE_FILE、账号加载器、余额查询通道、_usage_cache 等可变状态）
  一律在函数体内晚绑定 `bs.<名字>` —— 测试对 bs 命名空间的 monkeypatch 原样生效；
  _usage_cache/_usage_cache_path 会被 load/save 重绑，因此**读写必须都走 bs.**，
  保证 bs 命名空间是唯一事实来源；
- 模块内未被测试 patch 的纯助手之间可以直接互调。
"""

import asyncio
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter

usage_router = APIRouter()

# 用量数据内存缓存。record_account_usage 每记一个账号都读写一次文件，
# 29 个账号就是 29 次全量 IO 且随历史变肥；缓存后进程内合并，落盘走写穿。
# 按路径做 key 是为了测试里 monkeypatch USAGE_FILE 指到 tmp_path 时能正确失效。
_usage_cache: dict | None = None
_usage_cache_path = None


def load_usage_data() -> dict:
	"""读取用量历史数据（带内存缓存；本服务是单进程独占该文件的，无外部写入方）"""
	import balance_server as bs

	if bs._usage_cache is not None and bs._usage_cache_path == bs.USAGE_FILE:
		return bs._usage_cache
	bs._usage_cache_path = bs.USAGE_FILE
	if bs.USAGE_FILE.exists():
		try:
			bs._usage_cache = json.loads(bs.USAGE_FILE.read_text(encoding='utf-8'))
		except Exception as e:
			# 绝不能读失败后拿空 dict 继续跑 —— 下一次保存会把 90 天历史一次抹掉。
			# 把坏文件留档再从空开始，历史还在备份里可人工抢救。
			backup = bs.USAGE_FILE.with_name(f'{bs.USAGE_FILE.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}')
			try:
				os.replace(bs.USAGE_FILE, backup)
				print(f'[USAGE] daily_usage.json 损坏，已备份为 {backup.name} 后从空开始: {e}')
			except OSError:
				print(f'[USAGE] daily_usage.json 损坏且备份失败（拒绝覆盖）: {e}')
				bs._usage_cache_path = None
				raise
			bs._usage_cache = {}
	else:
		bs._usage_cache = {}
	return bs._usage_cache


def save_usage_data(data: dict):
	"""保存用量历史数据（原子写 + 同步内存缓存）"""
	import balance_server as bs

	bs._atomic_write_json(bs.USAGE_FILE, data, indent=2)
	bs._usage_cache = data
	bs._usage_cache_path = bs.USAGE_FILE


def usage_key(provider: str, name: str) -> str:
	"""今日用量快照的 key。

	必须带站点前缀：账号名只在站点内唯一，跨站重名很常见（实测 agentrouter 与 gorouter
	有 15 个账号同名 `2,3,5,…,18`，anyrouter 的 cookie 账号还与 gorouter 撞了 `0`/`16`）。
	以前按裸名字存，两个站点的余额就会互相覆盖 —— 页面上 AgentRouter 显示的其实是
	GoRouter 的数字，今日用量也跟着算错。
	"""
	return f'{provider}:{name}'


def _usage_providers_by_name() -> dict[str, list[str]]:
	"""账号名 -> 拥有该名字的站点列表，用于迁移旧数据时判断归属"""
	import balance_server as bs

	owners: dict[str, list[str]] = {}

	def add(provider: str, names):
		for n in names:
			owners.setdefault(n, [])
			if provider not in owners[n]:
				owners[n].append(provider)

	# 顺序即歧义时的优先级：站点与 anyrouter 的余额由 0 点快照每天写入，值可信；
	# agentrouter 只在签到时写，重名条目基本不可能是它留下的。
	for site in bs.load_newapi_sites():
		add(site.id, [a.name for a in bs.load_newapi_accounts(site)])
	add('anyrouter', [a.name for a in bs.load_token_accounts()])
	add('anyrouter', [a.name for a in bs.load_cookie_accounts()])
	add('agentrouter', [a.name for a in bs.load_login_accounts()])
	return owners


def migrate_usage_keys(usage_data: dict) -> tuple[dict, int, int]:
	"""把裸账号名的旧条目改写成 `站点:账号名`，返回 (新数据, 迁移条数, 无法归属条数)。

	归属唯一的直接改写；重名的按 `_usage_providers_by_name()` 的优先级归给第一个站点
	（证据表明那些条目确实是 0 点快照写的），被判给谁，另一方的历史就等于从迁移当天重新开始。
	实在找不到归属的账号（已删除的账号）原样保留，不丢数据也不乱认。
	"""
	owners = _usage_providers_by_name()
	migrated = 0
	orphaned = 0
	out: dict = {}
	for date, day in usage_data.items():
		if not isinstance(day, dict):
			out[date] = day
			continue
		new_day: dict = {}
		for key, value in day.items():
			if ':' in key:  # 已经是新格式
				new_day[key] = value
				continue
			candidates = owners.get(key)
			if not candidates:
				new_day[key] = value  # 认不出来就别动
				orphaned += 1
				continue
			new_key = usage_key(candidates[0], key)
			# 新 key 已存在（同一天两边都写过）时不覆盖，新格式的数据更可信
			if new_key not in new_day:
				new_day[new_key] = value
			migrated += 1
		out[date] = new_day
	return out, migrated, orphaned


def run_usage_key_migration():
	"""启动时跑一次 key 迁移，全是新格式则不写盘"""
	usage_data = load_usage_data()
	if not usage_data:
		return
	migrated_data, migrated, orphaned = migrate_usage_keys(usage_data)
	if migrated == 0:
		return
	save_usage_data(migrated_data)
	print(f'[USAGE] 用量 key 已迁移为「站点:账号名」：改写 {migrated} 条，无法归属 {orphaned} 条保持原样')


def _merge_usage_entry(day: dict, key: str, used: float, quota: float):
	"""把一个账号的余额并入某天的快照条目。key 是 `usage_key()` 生成的「站点:账号名」。

	`used`/`quota` 始终是最新值（AgentRouter 的余额展示靠它）；`used0` 是当天第一次记录到的
	已用量，也就是今日用量的基线，写入后当天不再改动 —— 签到成功时会再记一次余额，若让它
	覆盖基线，今日用量就永远算成 0。老数据没有 used0，回退用它的 used 当基线。
	"""
	prev = day.get(key)
	prev = prev if isinstance(prev, dict) else {}
	day[key] = {
		'used': used,
		'quota': quota,
		'used0': prev.get('used0', prev.get('used', used)),
	}


def record_account_usage(provider: str, name: str, used: float, quota: float):
	"""把单个账号的余额写入今日用量快照（Login / 站点账号在签到时增量记录）"""
	today = datetime.now().strftime('%Y-%m-%d')
	usage_data = load_usage_data()
	day = usage_data.get(today, {})
	_merge_usage_entry(day, usage_key(provider, name), used, quota)
	usage_data[today] = day
	# 只保留最近 90 天
	sorted_dates = sorted(usage_data.keys(), reverse=True)[:90]
	usage_data = {d: usage_data[d] for d in sorted_dates}
	save_usage_data(usage_data)


async def take_daily_snapshot():
	"""执行每日 0 点快照"""
	import balance_server as bs

	today = datetime.now().strftime('%Y-%m-%d')
	print(f'[USAGE] 开始执行每日快照: {today}')

	# 获取 WAF cookies（仅 anyrouter 需要；失败则跳过 anyrouter 部分，gorouter 不受影响）
	waf_cookies = await bs._get_waf_cookies_if_needed()
	if not waf_cookies:
		print('[USAGE] WAF cookies 获取失败，跳过 anyrouter 账号快照')

	sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)
	all_results = []

	# 1. 读取旧格式账号配置 (saved_config.json)
	if waf_cookies and bs.CONFIG_FILE.exists():
		try:
			config_data = bs._read_json_cached(bs.CONFIG_FILE)
			accounts_raw = config_data.get('accounts', [])
			if accounts_raw:
				accounts = [bs.AccountItem(**acc) for acc in accounts_raw]

				async def limited_query_old(acc):
					async with sem:
						return await bs.query_balance(acc, waf_cookies)

				tasks = [limited_query_old(acc) for acc in accounts]
				results = await asyncio.gather(*tasks)
				all_results.extend(('anyrouter', r) for r in results)
				print(f'[USAGE] 旧格式账号查询完成: {len(results)} 个')
		except Exception as e:
			print(f'[USAGE] 读取旧格式配置失败: {e}')

	# 2. 读取新格式账号配置 (new_accounts_config.json)
	token_accounts = bs.load_token_accounts() if waf_cookies else []
	if token_accounts:

		async def limited_query_token(acc):
			async with sem:
				return await bs.query_balance_with_token(acc, waf_cookies)

		tasks = [limited_query_token(acc) for acc in token_accounts]
		results = await asyncio.gather(*tasks)
		all_results.extend(('anyrouter', r) for r in results)
		print(f'[USAGE] 新格式账号查询完成: {len(results)} 个')

	# 注：Login（agentrouter.org）账号的余额不在此处查询。
	# 登录接口按 IP 限流，且每日 0 点会启动签到流程，余额在每个账号签到成功时
	# 由 record_account_usage() 增量写入今日快照，避免重复请求登录接口。

	# 3. 通用 new-api 站点账号（不走 WAF/代理，各站点独立并发查询）
	#    auto_checkin=false 的站点视为「服务器不再碰它」：不签到也不查快照（避免风控/封号）
	for site in bs.load_newapi_sites():
		if not site.auto_checkin:
			continue
		site_accounts = bs.load_newapi_accounts(site)
		if not site_accounts:
			continue
		site_sem = asyncio.Semaphore(site.concurrency or bs.NEWAPI_CONCURRENCY)

		async def limited_query_site(acc, s=site, sem_=site_sem):
			async with sem_:
				return await bs.query_balance_newapi(s, acc)

		results = await asyncio.gather(*[limited_query_site(acc) for acc in site_accounts])
		all_results.extend((site.id, r) for r in results)
		print(f'[USAGE] {site.label} 账号查询完成: {len(results)} 个')

	if not all_results:
		print('[USAGE] 没有账号配置，跳过快照')
		return

	# 保存快照。key 必须带站点前缀，否则跨站重名的账号会互相覆盖
	# （anyrouter 的 cookie 账号与 gorouter 就撞了 `0`/`16`）。
	snapshot = {usage_key(provider, r['name']): r for provider, r in all_results if r.get('success')}

	if snapshot:
		usage_data = load_usage_data()
		# 合并而非覆盖：保留 Login 账号在签到时已增量写入的余额与当天已定下的基线
		day = usage_data.get(today, {})
		for key, r in snapshot.items():
			_merge_usage_entry(day, key, r['used'], r['quota'])
		usage_data[today] = day
		# 只保留最近 90 天
		sorted_dates = sorted(usage_data.keys(), reverse=True)[:90]
		usage_data = {d: usage_data[d] for d in sorted_dates}
		save_usage_data(usage_data)
		print(f'[USAGE] 快照完成，记录了 {len(snapshot)} 个账号')
	else:
		print('[USAGE] 所有账号查询失败，未保存快照')


def seconds_until_midnight() -> float:
	"""计算距离下一个 0 点的秒数。

	用 timedelta 跨天，不要手动 day+1 —— 那样每月最后一天必抛 ValueError，
	会把依赖它的两个每日调度器一起带死。
	"""
	now = datetime.now()
	tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
	return (tomorrow - now).total_seconds()


async def daily_snapshot_scheduler():
	"""每日快照调度器"""
	while True:
		try:
			wait_seconds = seconds_until_midnight()
			print(f'[USAGE] 下次快照将在 {wait_seconds:.0f} 秒后执行')
			await asyncio.sleep(wait_seconds + 5)  # 多等 5 秒确保过了 0 点
			await take_daily_snapshot()
		except Exception as e:
			print(f'[USAGE] 快照调度出错（下一轮继续）: {e}')
			await asyncio.sleep(60)


@usage_router.get('/api/usage/today')
async def get_today_usage():
	"""返回今日已用量基线（daily_usage.json 当天条目的 used0），供前端算今日用量。

	今日用量 = 当前 used − 今日基线，由前端拿到余额结果后即时相减得出。基线是当天第一次
	记录到该账号时的已用量（0 点快照，或账号当天首次签到/首次入库的时刻），之后不再变动。

	原先是逐账号打 /api/log/self/stat：81 个 anyrouter 账号就是 81 个上游请求，占一次
	AnyRouter 查询总请求量的一半，且是三个并行请求里最慢的一个（14~17s）。改读本地快照后
	此接口零上游请求、毫秒级返回，代价是数据口径从「实时」变成「以当天基线为准」。

	当天还没有快照时返回空基线，前端显示 "--"。
	"""
	today = datetime.now().strftime('%Y-%m-%d')
	day = load_usage_data().get(today, {})
	baseline = {}
	for key, v in day.items():
		if not isinstance(v, dict):
			continue
		# used0 是基线；老数据没有这个字段时退回 used
		base = v.get('used0', v.get('used'))
		if base is not None:
			# key 是「站点:账号名」，前端按同样的方式拼出来查
			baseline[key] = base
	return {
		'success': True,
		'date': today,
		'baseline': baseline,
	}


@usage_router.get('/api/usage/history')
async def get_usage_history():
	"""获取历史用量数据（最近 30 天）"""
	usage_data = load_usage_data()
	sorted_dates = sorted(usage_data.keys(), reverse=True)[:30]
	history = {d: usage_data[d] for d in sorted_dates}
	return {
		'success': True,
		'history': history,
	}


@usage_router.post('/api/usage/snapshot')
async def manual_snapshot():
	"""手动触发快照（用于测试或补录）"""
	await take_daily_snapshot()
	return {'success': True, 'message': '快照已执行'}
