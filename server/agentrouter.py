"""AgentRouter（agentrouter.org）登录域：账号密码登录即签到、登录余额查询、
session 缓存解耦助手、缓慢/快速两套签到调度。

过渡期约定（拆分块D2，同 server/keys.py）：不做模块级 `import balance_server`，跨实体
引用（AGENTROUTER_ORG_CONFIG、代理/UA、_agentrouter_session（暂居 keys，块E 收口时
归并到本域）、record_account_usage、可被测试 patch 的 sign_in_login / save_checkin_state
等）在函数体内晚绑定 `bs.<名字>`。checkin_state（Login 签到状态）被测试整体重绑，
真实住所留在 balance_server，本模块一律经 bs. 读写。
"""

import asyncio
import base64
import random
import time
from collections import deque
from datetime import datetime

from pydantic import BaseModel
from fastapi import APIRouter

agentrouter_router = APIRouter()

_FATAL_LOGIN_MARKS = ('密码', '封禁', '限流', '429')


class LoginAccountItem(BaseModel):
	name: str
	username: str
	password: str


def checkin_gap_seconds() -> int:
	"""缓慢签到模式的账号间隔（秒），范围可由前端设置（分钟）。设置坏了就退回默认 30~60 分钟"""
	import balance_server as bs

	try:
		gmin = max(1, int(bs.checkin_settings.get('agentrouter_gap_min')))
		gmax = max(gmin, int(bs.checkin_settings.get('agentrouter_gap_max')))
	except (TypeError, ValueError):
		return random.randint(bs.CHECKIN_MIN_DELAY, bs.CHECKIN_MAX_DELAY)
	return random.randint(gmin, gmax) * 60


def load_login_accounts() -> list[LoginAccountItem]:
	"""从 agentrouter_accounts.json 加载登录方式账号列表"""
	import balance_server as bs

	return bs._read_json_models(bs.AGENTROUTER_ACCOUNTS_FILE, LoginAccountItem, 'LOGIN')


def add_checkin_log(msg: str):
	import balance_server as bs

	bs._checkin_add_log(bs.checkin_state, 'CHECKIN', msg)


def save_checkin_state():
	"""持久化签到状态到文件"""
	import balance_server as bs

	bs._checkin_save(bs.checkin_state, bs.CHECKIN_STATE_FILE, 'CHECKIN')


def load_checkin_state():
	"""服务启动时从文件恢复签到状态（仅用于前端展示历史进度）"""
	import balance_server as bs

	bs._checkin_load(bs.checkin_state, bs.CHECKIN_STATE_FILE, 'CHECKIN')


def agentrouter_block_reason(resp) -> str | None:
	"""认出 agentrouter 的拦截页，返回人话原因；正常返回 None。

	agentrouter 也在阿里云 WAF 后面。它的拦截页与 anyrouter 的不同：**不是那种可以用
	`_solve_acw_sc_v2()` 算出 cookie 的 `arg1` 挑战，而是滑块验证码页**（正文含
	`aliyun_waf_aa` / `slide` / `captcha`），HTTP 仍是 200，直接 `resp.json()` 会抛
	JSONDecodeError —— 看起来像代码坏了，其实是出口 IP 被 WAF 盯上了。
	程序解不了滑块，唯一的办法是停手等它过去，所以这里只负责把原因说清楚。
	"""
	try:
		body = resp.text or ''
	except Exception:
		return None
	if resp.status_code == 429:
		return '被站点限流（429），请等几分钟再试'
	if 'aliyun_waf' in body or ('slide' in body and 'captcha' in body):
		return '被阿里云 WAF 拦截（滑块验证），出口 IP 请求过多，需等一段时间自行恢复'
	return None


async def agentrouter_real_balance(cookies: dict, user_id: str) -> tuple[dict | None, str | None]:
	"""登录之后再读一次 `/api/user/self` 取真实余额，返回 (余额, 失败原因)。

	**登录响应体里的 `quota`/`used_quota` 不能用** —— 字段在，但实测恒为 0
	（2026-08-17 用 4 个账号验证：登录响应都是 $0，`/api/user/self` 是 $1725~$2415）。
	照着登录响应记账，agentrouter 的余额与用量就会全部变成 0 —— 90 天历史里一次非零都没有，
	就是这么来的。
	"""
	import balance_server as bs

	if not user_id:
		return None, '没有 user id'
	config = bs.AGENTROUTER_ORG_CONFIG
	proxies = {'https': bs._AGENTROUTER_PROXY}

	def _do():
		sess = bs._get_cffi_session(bs._ar_session_key('agentrouter-self'), proxies)
		return sess.get(
			f'{config["domain"]}{config["user_info_path"]}',
			headers={'User-Agent': bs.USER_AGENT, 'Accept': 'application/json', 'new-api-user': str(user_id)},
			cookies=cookies,
			timeout=15,
		)

	try:
		loop = asyncio.get_running_loop()
		resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
		blocked = bs.agentrouter_block_reason(resp)
		if blocked:
			return None, blocked
		if resp.status_code != 200:
			return None, f'HTTP {resp.status_code}'
		try:
			data = resp.json()
		except Exception:
			return None, '响应不是 JSON（可能被拦截）'
		if not data.get('success'):
			return None, data.get('message') or 'Unknown'
		d = data.get('data') or {}
		return {
			'quota': round((d.get('quota') or 0) / 500000, 2),
			'used': round((d.get('used_quota') or 0) / 500000, 2),
			'username': d.get('username', ''),
		}, None
	except Exception as e:
		reason = f'{type(e).__name__}: {e}'[:120]
		print(f'[AGENTROUTER] 读取真实余额失败: {reason}')
		return None, reason


async def query_balance_login(account: LoginAccountItem) -> dict:
	"""通过账号密码登录查询 agentrouter.org 余额（走代理绕过 WAF）"""
	import balance_server as bs

	config = bs.AGENTROUTER_ORG_CONFIG
	login_url = f"{config['domain']}{config['login_path']}"
	body = {'username': account.username, 'password': account.password}
	proxies = {'https': bs._AGENTROUTER_PROXY}
	max_retries = 3

	def _do():
		# key 必须带出口代数：出口轮换后复用旧代数的 Session 会继续从旧 IP 出去，
		# 与 _ar_session_key 注释里描述的轮换机制直接矛盾（其余 agentrouter 调用点都带了）
		sess = bs._get_cffi_session(bs._ar_session_key('agentrouter'), proxies)
		resp = sess.post(login_url, json=body, timeout=15)
		return resp, dict(sess.cookies)

	for attempt in range(max_retries):
		try:
			loop = asyncio.get_running_loop()
			resp, jar = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
			# 429 的响应体是空的，先认出来，否则 resp.json() 抛的 JSONDecodeError 完全看不出是限流
			if resp.status_code == 429:
				return {'name': account.name, 'success': False, 'error': '登录被站点限流（429），请等几分钟再试'}
			if resp.status_code == 200:
				data = resp.json()
				if data.get('success'):
					user_data = data.get('data', {})
					# 余额只认 /api/user/self，登录响应里的 quota 恒为 0（见 agentrouter_real_balance）
					real, why = await agentrouter_real_balance(jar, str(user_data.get('id') or ''))
					if real is None:
						return {
							'name': account.name,
							'success': False,
							'error': f'读取余额失败：{why}',
						}
					return {
						'name': account.name,
						'success': True,
						'quota': real['quota'],
						'used': real['used'],
						'username': real['username'] or user_data.get('username', ''),
					}
				return {
					'name': account.name,
					'success': False,
					'error': f"登录失败: {data.get('message', 'Unknown')}",
				}
			return {
				'name': account.name,
				'success': False,
				'error': f'HTTP {resp.status_code}',
			}
		except Exception as e:
			if attempt < max_retries - 1:
				await asyncio.sleep(1.5 * (attempt + 1))
				continue
			return {
				'name': account.name,
				'success': False,
				'error': f'{type(e).__name__}: {e}'[:150] or f'{type(e).__name__}',
			}


async def sign_in_login(account: LoginAccountItem) -> dict:
	"""通过账号密码登录自动签到（agentrouter.org 登录即签到）"""
	import balance_server as bs

	config = bs.AGENTROUTER_ORG_CONFIG
	login_url = f"{config['domain']}{config['login_path']}"
	body = {'username': account.username, 'password': account.password}
	proxies = {'https': bs._AGENTROUTER_PROXY}
	max_retries = 3

	def _do():
		# 连接池 key 必须带出口代数：mihomo 切节点不杀旧隧道，复用旧 Session 会继续从旧出口出去
		sess = bs._get_cffi_session(bs._ar_session_key('agentrouter'), proxies)
		resp = sess.post(login_url, json=body, timeout=15)
		return resp, dict(sess.cookies)

	for attempt in range(max_retries):
		try:
			loop = asyncio.get_running_loop()
			resp, jar = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
			if resp.status_code == 429:
				return {'name': account.name, 'success': False, 'message': '登录被站点限流（429），请等几分钟再试'}
			blocked = bs.agentrouter_block_reason(resp)
			if blocked:
				# 滑块页是 200 + HTML，不先认出来 resp.json() 会抛 JSONDecodeError，看不出是被拦
				return {'name': account.name, 'success': False, 'message': f'登录被拦: {blocked}'}
			if resp.status_code != 200:
				return {'name': account.name, 'success': False, 'message': f'登录失败: HTTP {resp.status_code}'}
			login_data = resp.json()
			if not login_data.get('success'):
				return {
					'name': account.name,
					'success': False,
					'message': f"登录失败: {login_data.get('message', 'Unknown')}",
				}
			user_data = login_data.get('data', {})
			checked_in = user_data.get('checked_in', False)
			# checked_in 取自登录响应（这个字段是准的）；余额必须另取，
			# 登录响应里的 quota 恒为 0（见 agentrouter_real_balance）。
			# 取不到就把 quota 留成 None —— 调用方据此跳过记账，
			# 免得把 0 写进今日基线，那会让这个账号当天的用量永远算不出来。
			real, why = await agentrouter_real_balance(jar, str(user_data.get('id') or ''))
			if real is None:
				bs.add_checkin_log(f'{account.name}: 签到成功但读取余额失败（{why}），本次不记用量')
			return {
				'name': account.name,
				'success': True,
				'message': '今日已签到' if checked_in else '签到成功',
				'already_signed': checked_in,
				'quota': real['quota'] if real else None,
				'used': real['used'] if real else None,
			}
		except Exception as e:
			if attempt < max_retries - 1:
				await asyncio.sleep(1.5 * (attempt + 1))
				continue
			return {'name': account.name, 'success': False, 'message': f'{type(e).__name__}: {e}'[:100]}


def _session_expiry_info(session: str) -> dict | None:
	"""解码 gorilla securecookie，算出 session 的过期时间与剩余天数。

	cookie 整体是 base64url(时间戳|gob|HMAC)，首段为签名时刻(unix 秒)，有效期 30 天。
	"""
	try:
		raw = base64.urlsafe_b64decode(session + '=' * (-len(session) % 4))
		ts = int(raw.split(b'|')[0])
		if ts < 1_000_000_000:
			return None
		exp = ts + 2592000  # MaxAge 30 天
		days = (exp - datetime.now().timestamp()) / 86400
		return {
			'expires_at': datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M:%S'),
			'days_left': round(days, 1),
		}
	except Exception:
		return None


async def run_login_checkin(trigger: str = 'manual'):
	"""执行 Login 账号签到流程：随机顺序，逐个签到，账号之间随机等待 30~60 分钟。

	所有账号都会被签到，可中途停止（设置 checkin_state['running'] = False）。
	"""
	import balance_server as bs

	accounts = load_login_accounts()
	if not accounts:
		bs.add_checkin_log('没有 Login 账号，签到流程结束')
		bs.checkin_state['running'] = False
		return

	# 随机打乱顺序，但保证每个账号都会被处理
	order = accounts[:]
	random.shuffle(order)

	today = datetime.now().strftime('%Y-%m-%d')
	bs.checkin_state['running'] = True
	bs.checkin_state['date'] = today
	bs.checkin_state['trigger'] = trigger
	bs.checkin_state['started_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	bs.checkin_state['finished_at'] = None
	bs.checkin_state['total'] = len(order)
	bs.checkin_state['done'] = 0
	bs.checkin_state['order'] = [a.name for a in order]
	bs.checkin_state['current'] = None
	bs.checkin_state['next_at'] = None
	bs.checkin_state['accounts'] = {
		a.name: {'status': 'pending', 'message': '等待签到', 'time': None} for a in order
	}
	bs.checkin_state['logs'] = []
	bs.add_checkin_log(f'开始签到流程（{trigger}），共 {len(order)} 个账号，随机顺序')
	bs.save_checkin_state()

	# 队列模型：失败的账号排到队尾稍后重试，确保所有账号都签到完。
	# 每次签到之间（含重试）随机等待 30~60 分钟，那时按 IP 的限流早已恢复。
	MAX_ATTEMPTS = 3
	queue = deque((acc, 1) for acc in order)
	processed_any = False

	async def _wait_between():
		"""签到间随机等待，分段睡眠以便及时响应停止"""
		bs.checkin_state['current'] = None
		delay = bs.checkin_gap_seconds()
		next_time = datetime.now().timestamp() + delay
		bs.checkin_state['next_at'] = datetime.fromtimestamp(next_time).strftime('%Y-%m-%d %H:%M:%S')
		bs.add_checkin_log(f'下一个账号将在 {delay // 60} 分钟后（{bs.checkin_state["next_at"]}）签到')
		bs.save_checkin_state()
		for _ in range(delay):
			if not bs.checkin_state['running']:
				break
			await asyncio.sleep(1)

	while queue:
		if not bs.checkin_state['running']:
			bs.add_checkin_log('收到停止指令，签到流程中断')
			break

		acc, attempt = queue.popleft()

		# 除第一个账号外，每个账号签到前先等待
		if processed_any:
			await _wait_between()
			if not bs.checkin_state['running']:
				bs.add_checkin_log('收到停止指令，签到流程中断')
				break
		processed_any = True

		bs.checkin_state['current'] = acc.name
		bs.checkin_state['next_at'] = None
		bs.save_checkin_state()

		result = await bs.sign_in_login(acc)

		ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		if result.get('success'):
			status = 'already' if result.get('already_signed') else 'signed'
			msg = result.get('message', '签到成功')
			# 登录响应里带余额，顺便记录，供前端展示（无需再单独查登录接口）
			bs.checkin_state['accounts'][acc.name] = {
				'status': status,
				'message': msg,
				'time': ts,
				'quota': result.get('quota'),
				'used': result.get('used'),
			}
			# 把本次拿到的余额写入今日用量快照（Login 账号余额仅在此处获取，不再单独查登录接口）
			if result.get('quota') is not None:
				bs.record_account_usage('agentrouter', acc.name, result.get('used', 0), result.get('quota', 0))
			bs.add_checkin_log(f'{acc.name}: {msg}')
		else:
			msg = result.get('message', '签到失败')
			if attempt < MAX_ATTEMPTS:
				# 标记为待重试，排到队尾
				queue.append((acc, attempt + 1))
				bs.checkin_state['accounts'][acc.name] = {
					'status': 'pending',
					'message': f'{msg}（第 {attempt} 次失败，稍后重试）',
					'time': ts,
				}
				bs.add_checkin_log(f'{acc.name}: {msg} — 已排入重试队列（{attempt}/{MAX_ATTEMPTS}）')
			else:
				bs.checkin_state['accounts'][acc.name] = {'status': 'failed', 'message': msg, 'time': ts}
				bs.add_checkin_log(f'{acc.name}: {msg} — 已达最大重试次数，放弃')
		bs.save_checkin_state()

	bs.checkin_state['running'] = False
	bs.checkin_state['current'] = None
	bs.checkin_state['next_at'] = None
	bs.checkin_state['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	signed = sum(1 for v in bs.checkin_state['accounts'].values() if v['status'] in ('signed', 'already'))
	bs.add_checkin_log(f'签到流程结束：{signed}/{bs.checkin_state["total"]} 个账号已签到')
	bs.save_checkin_state()


def start_login_checkin(trigger: str = 'manual') -> bool:
	"""启动签到任务，若已在运行则返回 False"""
	import balance_server as bs

	if bs.checkin_state['running']:
		return False
	bs.checkin_state['task'] = asyncio.create_task(run_login_checkin(trigger))
	return True


async def _login_balance_one(account: LoginAccountItem, force: bool) -> dict:
	"""查单个账号余额。fatal=True 的失败（密码错/封禁/限流）换 IP 重试也没用。"""
	import balance_server as bs

	def _login_err(e: Exception) -> dict:
		msg = f'{e}'[:120]
		return {'name': account.name, 'success': False, 'error': msg, 'fatal': any(m in msg for m in _FATAL_LOGIN_MARKS)}

	try:
		cookies, user_id = await bs._agentrouter_session(account, force=force)
	except Exception as e:
		return _login_err(e)
	real, why = await bs.agentrouter_real_balance(cookies, user_id)
	# 被 WAF 拦或被限流时当场重登也没用（拦的是 cookie/IP），只有 session 失效才值得马上重来
	if real is None and 'WAF' not in (why or '') and '限流' not in (why or ''):
		bs._agentrouter_key_sessions.pop(account.name, None)
		try:
			cookies, user_id = await bs._agentrouter_session(account)
		except Exception as e:
			return _login_err(e)
		real, why = await bs.agentrouter_real_balance(cookies, user_id)
	if real is not None:
		return {
			'name': account.name,
			'success': True,
			'quota': real['quota'],
			'used': real['used'],
			'username': real['username'],
		}
	return {
		'name': account.name,
		'success': False,
		'error': f'读取余额失败：{why}'[:160],
		'fatal': '限流' in (why or ''),
	}


async def _run_with_rotation(
	accounts: list, work, initial_force: bool = False, on_result=None, should_stop=None
) -> tuple[list[dict], bool]:
	"""分批 + 轮换出口 IP 地跑 work(account, force)，返回 (与 accounts 同序的结果, 是否提前中止)。

	调度规则（都是 2026-08-22 实测出的 WAF 行为）：
	- 每个出口 IP 有 WAF_IP_BUDGET 的请求预算，花光就换下一个；全花光就冷却 WAF_COOLDOWN 秒；
	- 被 WAF 拦/超时的账号换 IP 重试（cookie 被滑块标记后重登是唯一解法），最多 WAF_MAX_ATTEMPTS 次；
	- 连续 WAF_ABORT_STREAK 个被拦零成功就提前中止：再打只是给惩罚窗口续命；
	- 整轮有 WAF_DEADLINE 的时长上限，超时把剩余账号明确报错（别让前端无限等）；
	- 只在真的轮换成功时才重试 —— 没有新 IP，同一 IP 上重试也过不去（实测）。

	work 返回的 dict 用 success / fatal / error(message) 表达结果，fatal=True 的失败换 IP 也没用；
	on_result 在每个账号到达终态时回调，签到流程用它更新进度面板；
	should_stop 每轮开始前询问一次，返回 True 就停 —— fast 全签靠它响应停止按钮。
	"""
	import balance_server as bs

	rotator = bs.ExitRotator()
	rotated = await rotator.start()
	if rotated:
		print(f'[AGENTROUTER] 出口轮换就绪：{rotator.ip_count} 个 IP')
	todo = [(a, initial_force) for a in accounts]
	attempts = {a.name: 0 for a in accounts}
	final: dict[str, dict] = {}
	ip_use = [0] * max(1, rotator.ip_count)
	deadline = time.time() + bs.WAF_DEADLINE
	consec_fail = 0
	aborted = False
	try:
		while todo and time.time() < deadline and not aborted:
			if should_stop and should_stop():
				for a, _ in todo:
					final[a.name] = {'name': a.name, 'success': False, 'error': '已手动停止', 'message': '已手动停止'}
				todo.clear()
				break
			if rotated:
				# 挑一个还有预算的出口 IP；全热就冷却一轮（不消耗账号）
				for _ in range(len(ip_use)):
					await rotator.next_ip()
					if ip_use[rotator.current] < bs.WAF_IP_BUDGET:
						break
				else:
					print(f'[AGENTROUTER] 出口 IP 都到预算，冷却 {bs.WAF_COOLDOWN}s')
					await asyncio.sleep(bs.WAF_COOLDOWN)
					ip_use = [0] * len(ip_use)
					continue
			chunk, todo = todo[:bs.WAF_BATCH_SIZE], todo[bs.WAF_BATCH_SIZE:]
			outcomes = await asyncio.gather(*[work(a, f) for a, f in chunk])
			cur = rotator.current if rotated else 0
			# 被 WAF 拦/连接超时说明这个 IP 已经热了，额外记账让下一轮尽快换掉它
			hot = sum(
				1
				for r in outcomes
				if not r.get('success') and any(
					m in (r.get('error') or r.get('message') or '') for m in ('WAF', 'Failed to perform')
				)
			)
			ip_use[cur] += len(chunk) + hot
			for (account, _), r in zip(chunk, outcomes):
				attempts[account.name] += 1
				consec_fail = 0 if r.get('success') else consec_fail + 1
				if r.get('success') or r.get('fatal') or not rotated or attempts[account.name] >= bs.WAF_MAX_ATTEMPTS:
					final[account.name] = r
					if on_result:
						on_result(account, r)
				else:
					todo.append((account, True))
			if consec_fail >= bs.WAF_ABORT_STREAK:
				aborted = True
				print(f'[AGENTROUTER] 连续 {consec_fail} 个账号被拦且零成功，提前中止（WAF 惩罚期）')
			elif todo:
				await asyncio.sleep(bs.WAF_ROUND_GAP if rotated else bs.WAF_DEGRADED_GAP)
		for a in accounts:
			if a.name not in final:
				msg = (
					'WAF 惩罚期（出口 IP 连续被拦），本次提前中止 —— 等 30~60 分钟再点一次重试即可，已完成的账号不受影响'
					if aborted
					else '超时未完成（出口 IP 都在 WAF 冷却），稍后再试'
				)
				final[a.name] = {'name': a.name, 'success': False, 'error': msg, 'message': msg}
	finally:
		await rotator.restore()
	return [final[a.name] for a in accounts], aborted


async def _query_login_balances(accounts: list, live: bool) -> list[dict]:
	"""余额查询的轮换封装（work 见 _login_balance_one，调度见 _run_with_rotation）"""
	results, _ = await _run_with_rotation(accounts, _login_balance_one, initial_force=live)
	return results


async def _sign_in_one(account: LoginAccountItem, force: bool) -> dict:
	"""一键全签轮换调度里的单个账号。

	agentrouter **登录即签到**，sign_in_login 每次都是全新登录，force 参数没有额外作用，
	保留它是为了与 _run_with_rotation 的 work(account, force) 接口一致。
	密码错/封禁/限流这类失败换 IP 也没用（fatal），WAF/超时交给调度器换 IP 重试。
	"""
	import balance_server as bs

	try:
		r = await bs.sign_in_login(account)
	except Exception as e:
		msg = f'{type(e).__name__}: {e}'[:120]
		return {'name': account.name, 'success': False, 'error': msg, 'message': msg, 'fatal': True}
	msg = str(r.get('message') or '')
	out = {'name': account.name, **r}
	out['fatal'] = any(m in msg for m in _FATAL_LOGIN_MARKS)
	return out


# ===== 端点（块E 自 balance_server.py 迁入，晚绑定 bs.<名字>）=====

@agentrouter_router.get('/api/login-accounts/accounts')
async def get_login_accounts():
	import balance_server as bs
	"""获取 agentrouter_accounts.json 中的账号列表"""
	accounts = bs.load_login_accounts()
	return {'success': True, 'accounts': [acc.model_dump() for acc in accounts]}



@agentrouter_router.post('/api/login-accounts/accounts')
async def save_login_accounts(req: dict):
	import balance_server as bs
	"""保存登录方式账号列表"""
	try:
		raw_accounts = req.get('accounts', [])
		validated = [LoginAccountItem(**acc) for acc in raw_accounts]
		bs._atomic_write_json(AGENTROUTER_ACCOUNTS_FILE, [acc.model_dump() for acc in validated], indent=2)
		return {'success': True}
	except Exception as e:
		return {'success': False, 'error': str(e)}



@agentrouter_router.post('/api/login-accounts/query')
async def query_login_accounts():
	import balance_server as bs
	"""查询所有 agentrouter.org 账号余额"""
	accounts = bs.load_login_accounts()
	if not accounts:
		return {'success': False, 'error': 'agentrouter_accounts.json 不存在或为空'}

	# 登录接口有按 IP 限流，顺序处理并在账号之间加延迟以规避 429
	results = []
	for i, acc in enumerate(accounts):
		if i > 0:
			await asyncio.sleep(1.5)
		results.append(await query_balance_login(acc))
	total_quota = sum(r.get('quota', 0) for r in results if r.get('success'))
	total_used = sum(r.get('used', 0) for r in results if r.get('success'))
	return {
		'success': True,
		'results': results,
		'summary': {
			'total_quota': round(total_quota, 2),
			'total_used': round(total_used, 2),
			'account_count': len(results),
			'success_count': sum(1 for r in results if r.get('success')),
		},
	}



@agentrouter_router.post('/api/login-accounts/checkin/fast')
async def login_checkin_fast():
	import balance_server as bs
	"""一键全签：轮换出口 IP 分批登录（agentrouter 登录即签到），约 1~2 分钟跑完。

	与 /checkin/start 的缓慢模式（账号间隔默认 30~60 分钟，可在设置里改）互补。
	出口轮换是全局动作，与余额查询共用一把锁；今天已签到的账号直接跳过，省 WAF 配额。
	"""
	accounts = bs.load_login_accounts()
	if not accounts:
		return {'success': False, 'error': 'agentrouter_accounts.json 不存在或为空'}
	if bs.checkin_state['running']:
		return {'success': False, 'error': '签到流程正在运行（缓慢模式不可打断），请先停止或等它结束'}
	if bs._balances_query_lock.locked():
		return {'success': False, 'error': '已有一轮出口轮换任务（余额查询/全签到）在进行中，请等它结束再点'}

	# 今天已签到的账号跳过：登录即签到，已签过再登一次既没意义又白耗 WAF 配额
	today = datetime.now().strftime('%Y-%m-%d')
	done_status: dict = {}
	if bs.checkin_state.get('date') == today:
		done_status = {
			n: v
			for n, v in (bs.checkin_state.get('accounts') or {}).items()
			if v.get('status') in ('signed', 'already')
		}
	pending = [a for a in accounts if a.name not in done_status]

	bs.checkin_state.update(
		running=True,
		date=today,
		trigger='fast',
		mode='fast',
		started_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
		finished_at=None,
		total=len(accounts),
		done=len(done_status),
		order=[a.name for a in accounts],
		current=None,
		next_at=None,
		logs=[],
	)
	bs.checkin_state['accounts'] = {
		a.name: done_status.get(a.name) or {'status': 'pending', 'message': '等待签到', 'time': None}
		for a in accounts
	}
	add_checkin_log(f'一键全签开始（轮换出口）：{len(pending)} 个待签 / {len(done_status)} 个今日已签跳过')
	save_checkin_state()

	def _on_result(account, r):
		ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		if r.get('success'):
			bs.checkin_state['accounts'][account.name] = {
				'status': 'already' if r.get('already_signed') else 'signed',
				'message': r.get('message', '签到成功'),
				'time': ts,
				'quota': r.get('quota'),
				'used': r.get('used'),
			}
			# 登录响应顺带拿到的余额写进今日快照（quota 为 None 说明没取到，跳过记账）
			if r.get('quota') is not None:
				bs.record_account_usage('agentrouter', account.name, r.get('used', 0), r.get('quota', 0))
			add_checkin_log(f'{account.name}: {r.get("message", "签到成功")}')
		else:
			msg = r.get('error') or r.get('message') or '签到失败'
			bs.checkin_state['accounts'][account.name] = {'status': 'failed', 'message': msg, 'time': ts}
			add_checkin_log(f'{account.name}: 签到失败 — {msg}')
		bs.checkin_state['done'] = sum(
			1 for v in bs.checkin_state['accounts'].values() if v.get('status') in ('signed', 'already', 'failed')
		)
		save_checkin_state()

	results: list = []
	aborted = False
	if pending:
		async with bs._balances_query_lock:
			# 停止按钮置 running=False，轮换调度每轮开始前检查它 —— fast 模式从此可停
			results, aborted = await bs._run_with_rotation(
				pending, bs._sign_in_one, on_result=_on_result, should_stop=lambda: not bs.checkin_state['running']
			)
	else:
		add_checkin_log('全部账号今日都已签到，无需签到')

	stopped = not bs.checkin_state['running']  # 停止按钮已把 running 置 False
	bs.checkin_state['running'] = False
	bs.checkin_state['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	signed = sum(1 for v in bs.checkin_state['accounts'].values() if v.get('status') in ('signed', 'already'))
	failed = sum(1 for v in bs.checkin_state['accounts'].values() if v.get('status') == 'failed')
	add_checkin_log(
		f'一键全签结束：{signed}/{bs.checkin_state["total"]} 已签到，失败 {failed}'
		+ ('（WAF 惩罚期提前中止）' if aborted else '')
		+ ('（手动停止）' if stopped else '')
	)
	save_checkin_state()
	return {
		'success': True,
		'summary': {
			'total': len(accounts),
			'new_signed': sum(1 for r in results if r.get('success') and not r.get('already_signed')),
			'already': sum(1 for r in results if r.get('already_signed')) + len(done_status),
			'failed': failed,
			'aborted': aborted,
		},
		'status': bs._checkin_status_payload(),
	}



@agentrouter_router.post('/api/login-accounts/checkin/start')
async def login_checkin_start():
	import balance_server as bs
	"""启动 Login 账号签到流程（随机顺序、账号间随机等待 30~60 分钟）"""
	if bs.checkin_state['running']:
		return {'success': False, 'error': '签到流程已在运行中', 'status': bs._checkin_status_payload()}
	accounts = bs.load_login_accounts()
	if not accounts:
		return {'success': False, 'error': 'agentrouter_accounts.json 不存在或为空'}
	bs.start_login_checkin(trigger='manual')
	# 稍等片刻让任务初始化状态
	await asyncio.sleep(0.2)
	return {'success': True, 'message': f'签到流程已启动，共 {len(accounts)} 个账号', 'status': bs._checkin_status_payload()}



@agentrouter_router.post('/api/login-accounts/checkin/stop')
async def login_checkin_stop():
	import balance_server as bs
	"""停止正在运行的 Login 账号签到流程"""
	if not bs.checkin_state['running']:
		return {'success': False, 'error': '当前没有正在运行的签到流程'}
	bs.checkin_state['running'] = False
	add_checkin_log('收到停止指令')
	return {'success': True, 'message': '已发送停止指令'}



def _checkin_status_payload() -> dict:
	"""组装签到状态返回体"""
	import balance_server as bs

	accounts = [
		{
			'name': name,
			'status': info.get('status', 'pending'),
			'message': info.get('message', ''),
			'time': info.get('time'),
			'quota': info.get('quota'),
			'used': info.get('used'),
		}
		for name, info in bs.checkin_state['accounts'].items()
	]
	# 按本轮随机顺序排序，便于前端展示进度
	order_index = {n: i for i, n in enumerate(bs.checkin_state['order'])}
	accounts.sort(key=lambda a: order_index.get(a['name'], 999))
	signed = sum(1 for a in accounts if a['status'] in ('signed', 'already'))
	failed = sum(1 for a in accounts if a['status'] == 'failed')
	# done = 已到达终态（成功/今日已签/最终失败）的账号数；pending（含待重试）不计
	done = signed + failed
	return {
		'running': bs.checkin_state['running'],
		'date': bs.checkin_state['date'],
		'trigger': bs.checkin_state['trigger'],
		'mode': bs.checkin_state.get('mode') or 'slow',
		'started_at': bs.checkin_state['started_at'],
		'finished_at': bs.checkin_state['finished_at'],
		'total': bs.checkin_state['total'],
		'done': done,
		'signed': signed,
		'failed': failed,
		'accounts': accounts,
		'logs': bs.checkin_state['logs'][-30:],
	}


@agentrouter_router.get('/api/login-accounts/checkin/status')
async def login_checkin_status():
	import balance_server as bs
	"""获取 Login 账号签到流程的进度状态"""
	return {'success': True, 'status': bs._checkin_status_payload()}



@agentrouter_router.get('/api/login-accounts/balances')
async def login_accounts_balances(live: bool = False, names: str = ''):
	import balance_server as bs
	"""返回每个 agentrouter（Login）账号的余额。

	复用缓存 session 只打 /api/user/self；`live=true` 强制全部重登；`names=a,b` 只查指定账号
	（按名字精确匹配，逗号分隔）。结果写进今日快照当基线，今日用量从第二次查询起就能算出来。

	因为 WAF 按出口 IP + cookie 拦截（见 ExitRotator 的注释），这里不能全量并发：
	bs._query_login_balances 分批轮换出口 IP，一轮全量约 1~2 分钟。轮换是全局动作，
	同一时刻只允许一轮查询（重复点击会收到「查询进行中」）。
	"""
	accounts = bs.load_login_accounts()
	if names:
		wanted = {n.strip() for n in names.split(',') if n.strip()}
		accounts = [a for a in accounts if a.name in wanted]
	if not accounts:
		return {
			'success': True,
			'results': [],
			'summary': {'total_quota': 0, 'total_used': 0, 'account_count': 0, 'success_count': 0},
		}
	if bs._balances_query_lock.locked():
		return {'success': False, 'error': '已有一轮 AgentRouter 查询在进行中（要轮换出口 IP，必须独占），请等它结束再点'}
	async with bs._balances_query_lock:
		results = await bs._query_login_balances(accounts, live)
	for r in results:
		if r.get('success'):
			bs.record_account_usage('agentrouter', r['name'], r['used'], r['quota'])
	failed = sum(1 for r in results if not r.get('success'))
	if failed:
		print(f'[AGENTROUTER] 余额查询：{failed}/{len(results)} 个账号失败')
	total_quota = sum(r['quota'] for r in results if r.get('success'))
	total_used = sum(r['used'] for r in results if r.get('success'))
	return {
		'success': True,
		'results': results,
		'summary': {
			'total_quota': round(total_quota, 2),
			'total_used': round(total_used, 2),
			'account_count': len(results),
			'success_count': len(results) - failed,
		},
	}


