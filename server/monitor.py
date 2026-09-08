"""余额监控域（定时查余额、阈值告警、SMTP 邮件 + webhook 通知、三端点）。

过渡期约定（拆分第三块，同 server/keys.py / server/usage.py）：
- 本模块不做模块级 `import balance_server`，由 balance_server 导入并重导出公开名；
- 跨实体引用（monitor_state、账号加载器、余额查询通道、webhook 通知）在函数体内
  晚绑定 `bs.<名字>` —— 未来测试对 bs 命名空间的 monkeypatch 原样生效；
- monitor_state 只做条目级变更（从不整体重绑），经 bs. 统一读写；
- EmailConfig / MonitorStartRequest 随域迁移，注解在定义期求值故必须本地定义。
"""

import asyncio
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from fastapi import APIRouter
from pydantic import BaseModel

monitor_router = APIRouter()

monitor_state: dict = {
	'running': False,
	'task': None,
	'config': None,
	'last_check': None,
	'next_check': None,
	'alerted_accounts': set(),  # 已告警的账号（避免重复发送）
	'logs': [],  # 最近的监控日志
}


class EmailConfig(BaseModel):
	smtp_server: str
	smtp_port: int = 465
	email_user: str
	email_pass: str
	email_to: str


class MonitorStartRequest(BaseModel):
	# accounts 为空时后端自动收集全部账号（token + 站点 + cookie）监控，且循环每轮
	# 重新收集——监控期间增删账号即时生效；传入时为 [{kind: cookie|token|site, ...}]
	# 的宽松字典（兼容旧前端传 CookieAccount[]），固定用这份快照
	accounts: list[dict] = []
	email: EmailConfig
	interval_hours: float = 6
	threshold: float = 10.0


def _collect_monitor_accounts() -> list[dict]:
	"""收集全部可监控账号：token（anyrouter）+ 各站点 + cookie"""
	import balance_server as bs

	accounts: list[dict] = []
	for a in bs.load_token_accounts():
		accounts.append({'kind': 'token', 'name': a.name, 'access_token': a.access_token, 'user_id': a.user_id})
	for site in bs.load_newapi_sites():
		for a in bs.load_newapi_accounts(site):
			accounts.append({
				'kind': 'site', 'site_id': site.id, 'name': a.name,
				'access_token': a.access_token, 'user_id': a.user_id,
			})
	for a in bs.load_cookie_accounts():
		accounts.append({'kind': 'cookie', 'name': a.name, 'cookies': a.cookies, 'api_user': a.api_user})
	return accounts


def add_monitor_log(msg: str):
	"""添加监控日志，最多保留 50 条"""
	import balance_server as bs

	ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	bs.monitor_state['logs'].append({'time': ts, 'message': msg})
	if len(bs.monitor_state['logs']) > 50:
		bs.monitor_state['logs'] = bs.monitor_state['logs'][-50:]
	print(f'[MONITOR {ts}] {msg}')


def send_alert_email(email_cfg: EmailConfig, subject: str, body: str):
	"""发送告警邮件（同步阻塞，必须经线程池调用，别在事件循环里直接 await）"""
	msg = MIMEText(body, 'plain', 'utf-8')
	msg['From'] = f'AnyRouter Monitor <{email_cfg.email_user}>'
	msg['To'] = email_cfg.email_to
	msg['Subject'] = subject

	# timeout 必须给：SMTP 默认无超时，网络挂起时线程会永远等下去
	with smtplib.SMTP_SSL(email_cfg.smtp_server, email_cfg.smtp_port, timeout=30) as server:
		server.login(email_cfg.email_user, email_cfg.email_pass)
		server.send_message(msg)


def _monitor_alert_key(acc: dict) -> str:
	"""告警去重键：不同站点的账号可能同名，只用名字会互相吞掉告警"""
	return f"{acc.get('kind', 'cookie')}:{acc.get('site_id', '')}/{acc.get('name', '?')}"


async def monitor_loop(config: MonitorStartRequest, accounts: list[dict]):
	"""监控主循环

	自动收集模式（请求里 accounts=[]）每轮重新收集账号，监控期间新增/删除账号
	即时生效；显式传入账号列表则固定用这份快照。
	"""
	import balance_server as bs

	auto_accounts = not config.accounts
	interval_seconds = config.interval_hours * 3600
	add_monitor_log(
		f'监控启动：间隔 {config.interval_hours}h，阈值 ${config.threshold}，共 {len(accounts)} 个账号'
	)

	while bs.monitor_state['running']:
		bs.monitor_state['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

		if auto_accounts:
			accounts = _collect_monitor_accounts()
			bs.monitor_state['config']['account_count'] = len(accounts)
		add_monitor_log(f'开始检测余额...（{len(accounts)} 个账号）')

		if not accounts:
			add_monitor_log('没有可监控的账号，本轮跳过')
		else:
			try:
				waf_cookies = await bs._get_waf_cookies_if_needed()
				sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)

				async def limited_query(acc: dict):
					async with sem:
						kind = acc.get('kind', 'cookie')
						if kind == 'site':
							site = bs.get_newapi_site(acc.get('site_id', ''))
							if site is None:
								return {'name': acc.get('name', '?'), 'success': False, 'error': '站点不存在'}
							return await bs.query_balance_newapi(
								site,
								bs.NewapiAccountItem(
									name=acc.get('name', '?'),
									access_token=acc.get('access_token', ''),
									user_id=acc.get('user_id', ''),
								),
							)
						if kind == 'token':
							return await bs.query_balance_with_token(
								bs.TokenAccountItem(
									name=acc.get('name', '?'),
									access_token=acc.get('access_token', ''),
									user_id=acc.get('user_id', ''),
								),
								waf_cookies,
							)
						# cookie 方式（anyrouter）：需要 WAF cookies
						if not waf_cookies:
							return {'name': acc.get('name', '?'), 'success': False, 'error': 'WAF cookies 获取失败'}
						return await bs.query_balance(
							bs.AccountItem(
								name=acc.get('name', '?'),
								cookies=acc.get('cookies', {}) or {},
								api_user=acc.get('api_user', ''),
							),
							waf_cookies,
						)

				tasks = [limited_query(acc) for acc in accounts]
				# return_exceptions=True：单个账号查询抛异常只算它自己失败，
				# 不让 gather 把整轮结果全部作废
				results = await asyncio.gather(*tasks, return_exceptions=True)

				low_balance = []
				all_balances = []  # 所有账号余额
				total_quota = 0
				total_used = 0

				# results 顺序与 accounts 一致，配对取告警键
				for acc, r in zip(accounts, results):
					name = acc.get('name', '?')
					alert_key = _monitor_alert_key(acc)

					if isinstance(r, BaseException):
						add_monitor_log(f'{name}: 查询异常 - {type(r).__name__}: {str(r)[:80]}')
						all_balances.append({'name': name, 'success': False, 'error': str(r)[:120]})
						continue

					if not r.get('success'):
						add_monitor_log(f'{r["name"]}: 查询失败 - {r.get("error", "")}')
						all_balances.append({'name': r['name'], 'success': False, 'error': r.get('error', '')})
						continue

					all_balances.append(r)
					total_quota += r['quota']
					total_used += r['used']

					if r['quota'] < config.threshold:
						if alert_key not in bs.monitor_state['alerted_accounts']:
							low_balance.append(r)
							bs.monitor_state['alerted_accounts'].add(alert_key)
							add_monitor_log(f'{r["name"]}: 余额 ${r["quota"]} 低于阈值 ${config.threshold}')
					else:
						# 余额恢复，移除告警标记，下次再低于阈值会重新告警
						bs.monitor_state['alerted_accounts'].discard(alert_key)

				if low_balance:
					subject = f'⚠️ AnyRouter 余额告警：{len(low_balance)} 个账号余额不足'
					lines = [f'⚠️ 以下账号余额低于 ${config.threshold}：', '']
					for r in low_balance:
						lines.append(f'  ❗ {r["name"]}：${r["quota"]}')
					lines.extend(['', '=' * 40, '', '📊 所有账号余额汇总：', ''])
					for r in all_balances:
						if r.get('success', True) and 'quota' in r:
							marker = '⚠️' if r['quota'] < config.threshold else '✅'
							lines.append(f'  {marker} {r["name"]}：余额 ${r["quota"]}，已用 ${r["used"]}')
						else:
							lines.append(f'  ❌ {r["name"]}：查询失败')
					lines.extend(
						[
							'',
							f'📈 总计：余额 ${round(total_quota, 2)}，已用 ${round(total_used, 2)}',
							f'⏰ 检测时间：{bs.monitor_state["last_check"]}',
						]
					)
					body = '\n'.join(lines)

					try:
						# smtplib 是纯同步 IO，直呼会冻结整个事件循环（所有 API/签到全卡住），
						# 扔进默认线程池执行
						await asyncio.get_running_loop().run_in_executor(
							None, send_alert_email, config.email, subject, body
						)
						add_monitor_log(f'告警邮件已发送：{len(low_balance)} 个账号')
					except Exception as e:
						add_monitor_log(f'邮件发送失败：{str(e)[:80]}')
				else:
					add_monitor_log('所有账号余额正常')

				# webhook 通知独立于邮件：配置了就走，两者可并存（未配置不记日志，免噪音）
				if low_balance and bs.notify_configured() and bs.get_notify_config()['on_alert']:
					wr = await bs.send_webhook_notify(subject, body)
					add_monitor_log(f'webhook 通知：{"已发送" if wr["sent"] else "未发送（" + wr.get("error", "") + "）"}')
			except Exception as e:
				add_monitor_log(f'检测出错：{str(e)[:80]}')

		# 计算下次检测时间
		next_time = datetime.now().timestamp() + interval_seconds
		bs.monitor_state['next_check'] = datetime.fromtimestamp(next_time).strftime('%Y-%m-%d %H:%M:%S')

		# 分段睡眠，便于及时响应停止
		for _ in range(int(interval_seconds)):
			if not bs.monitor_state['running']:
				break
			await asyncio.sleep(1)

	add_monitor_log('监控已停止')


@monitor_router.post('/api/monitor/start')
async def monitor_start(req: MonitorStartRequest):
	"""启动余额监控（accounts 为空时自动收集全部 token/站点/cookie 账号）"""
	import balance_server as bs

	if bs.monitor_state['running']:
		return {'success': False, 'error': '监控已在运行中'}

	accounts = req.accounts or _collect_monitor_accounts()
	if not accounts:
		return {'success': False, 'error': '没有可监控的账号'}

	bs.monitor_state['running'] = True
	bs.monitor_state['alerted_accounts'] = set()
	bs.monitor_state['logs'] = []
	bs.monitor_state['config'] = {
		'interval_hours': req.interval_hours,
		'threshold': req.threshold,
		'account_count': len(accounts),
		'email_to': req.email.email_to,
	}

	bs.monitor_state['task'] = asyncio.create_task(monitor_loop(req, accounts))
	return {'success': True, 'message': f'监控已启动（{len(accounts)} 个账号）'}


@monitor_router.post('/api/monitor/stop')
async def monitor_stop():
	"""停止余额监控"""
	import balance_server as bs

	if not bs.monitor_state['running']:
		return {'success': False, 'error': '监控未在运行'}

	bs.monitor_state['running'] = False
	if bs.monitor_state['task']:
		bs.monitor_state['task'].cancel()
		bs.monitor_state['task'] = None

	add_monitor_log('收到停止指令')
	return {'success': True, 'message': '监控已停止'}


@monitor_router.get('/api/monitor/status')
async def monitor_status():
	"""获取监控状态"""
	import balance_server as bs

	return {
		'running': bs.monitor_state['running'],
		'config': bs.monitor_state['config'],
		'last_check': bs.monitor_state['last_check'],
		'next_check': bs.monitor_state['next_check'],
		'alerted_accounts': list(bs.monitor_state['alerted_accounts']),
		'logs': bs.monitor_state['logs'][-20:],
	}
