"""Webhook 通知（Telegram / Server酱 / Bark / 通用）：配置读写、统一发送入口、三端点。

过渡期约定（拆分块B，同 server/protection.py）：不做模块级 `import balance_server`，
跨实体引用（CONFIG_FILE、_read_json_cached、_atomic_write_json、_get_cffi_session）
在函数体内晚绑定 `bs.<名字>` —— 测试对 bs 命名空间的 monkeypatch 原样生效。
"""

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel

notify_router = APIRouter()


def get_notify_config() -> dict:
	"""读通知配置；未配置时返回 on_alert=True / on_checkin_failed=False 的默认值。"""
	import balance_server as bs

	try:
		cfg = bs._read_json_cached(bs.CONFIG_FILE)
	except Exception:
		cfg = {}
	n = cfg.get('notify') if isinstance(cfg, dict) else None
	n = n if isinstance(n, dict) else {}
	return {
		'type': (n.get('type') or '').strip().lower(),
		'url': (n.get('url') or '').strip(),
		'chat_id': (n.get('chat_id') or '').strip(),
		'on_alert': bool(n.get('on_alert', True)),
		'on_checkin_failed': bool(n.get('on_checkin_failed', False)),
	}


def notify_configured() -> bool:
	n = get_notify_config()
	return bool(n['url']) and n['type'] in ('telegram', 'serverchan', 'bark', 'generic')


async def send_webhook_notify(title: str, body: str) -> dict:
	"""按配置发一条通知，返回 {sent: bool, error?}。未配置/发送失败都不抛异常。

	Telegram 用 sendMessage（URL 含 bot token，chat_id 单独存）；Server酱是
	KEY.send 表单；Bark 是 <key>/push 的 JSON；通用网关 POST {title, message}。
	"""
	import balance_server as bs

	n = get_notify_config()
	if not notify_configured():
		return {'sent': False, 'error': '通知未配置'}

	# 各渠道请求形状不同：Telegram/通用收 JSON，Server酱只认表单编码，
	# Bark 官方形式是 POST {origin}/push 且 device_key 放 body
	form_data = None
	if n['type'] == 'telegram':
		url = n['url']
		payload = {'chat_id': n['chat_id'], 'text': f'{title}\n\n{body}'}
	elif n['type'] == 'serverchan':
		url = n['url'] if n['url'].endswith('.send') else n['url'].rstrip('/') + '.send'
		payload = {'title': title, 'desp': body}
		form_data = payload
	elif n['type'] == 'bark':
		parsed = urlparse(n['url'])
		device_key = parsed.path.rstrip('/').rpartition('/')[2]
		if not device_key:
			return {'sent': False, 'error': 'Bark URL 里没有 device key（应为 https://api.day.app/<key>）'}
		url = f'{parsed.scheme}://{parsed.netloc}/push'
		payload = {'device_key': device_key, 'title': title, 'body': body, 'group': 'newapi-checkin'}
	else:
		url = n['url']
		payload = {'title': title, 'message': body}

	def _do():
		sess = bs._get_cffi_session('notify')
		if form_data is not None:
			return sess.post(url, data=form_data, timeout=15)
		return sess.post(url, json=payload, timeout=15)

	loop = asyncio.get_running_loop()
	try:
		resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
		if resp.status_code != 200:
			return {'sent': False, 'error': f'HTTP {resp.status_code}'}
		return {'sent': True}
	except Exception as e:
		return {'sent': False, 'error': f'{type(e).__name__}: {e}'[:120]}


def _mask_secret(value: str) -> str:
	"""URL 里通常嵌着 bot token / sendkey，API 回显只露首尾（短 key 最多露 8 字符）。"""
	if len(value) <= 40:
		return value[:8] + '…' if value else ''
	return value[:28] + '…' + value[-8:]


class NotifyRequest(BaseModel):
	"""通知配置。url 留空表示保留已保存的值（URL 含 token，不回显也就无法重传）。"""

	type: str
	url: str = ''
	chat_id: str = ''
	on_alert: bool = True
	on_checkin_failed: bool = False


@notify_router.get('/api/notify')
async def get_notify():
	"""通知配置状态，URL 打码回显"""
	n = get_notify_config()
	return {
		'success': True,
		'notify': {
			'type': n['type'],
			'chat_id': n['chat_id'],
			'url_masked': _mask_secret(n['url']) if n['url'] else '',
			'configured': notify_configured(),
			'on_alert': n['on_alert'],
			'on_checkin_failed': n['on_checkin_failed'],
		},
	}


@notify_router.post('/api/notify')
async def save_notify(req: NotifyRequest):
	"""保存通知配置（合并写 saved_config.json，不动其他段）"""
	import balance_server as bs

	ntype = req.type.strip().lower()
	if ntype not in ('telegram', 'serverchan', 'bark', 'generic'):
		return {'success': False, 'error': f'未知通知类型: {req.type}（可选 telegram / serverchan / bark / generic）'}
	try:
		data = dict(bs._read_json_cached(bs.CONFIG_FILE) or {}) if bs.CONFIG_FILE.exists() else {}
	except Exception:
		data = {}
	saved = data.get('notify') or {}
	# 换渠道必须重填 URL：不同渠道的 URL 格式互不兼容（Telegram 带路径 token、
	# Server酱是 KEY.send、Bark 是 device key），沿用旧 URL 只会得到必然失败的配置
	if not req.url.strip() and saved.get('url') and (saved.get('type') or ntype) != ntype:
		return {'success': False, 'error': f'从 {saved.get("type")} 切换到 {ntype} 需要重新填写新渠道的 URL'}
	data['notify'] = {
		'type': ntype,
		'url': req.url.strip() or saved.get('url') or '',
		'chat_id': req.chat_id.strip(),
		'on_alert': req.on_alert,
		'on_checkin_failed': req.on_checkin_failed,
	}
	bs._atomic_write_json(bs.CONFIG_FILE, data, indent=2)
	return {'success': True, 'message': '通知配置已保存'}


@notify_router.post('/api/notify/test')
async def test_notify():
	"""发一条测试通知验证配置"""
	if not notify_configured():
		return {'success': False, 'error': '请先保存完整配置（类型 + URL）'}
	r = await send_webhook_notify('✅ 测试通知', 'newapi-checkin 通知通道配置成功，这是一条测试消息。')
	if r['sent']:
		return {'success': True, 'message': '测试通知已发送'}
	return {'success': False, 'error': r.get('error', '发送失败')}
