"""告警通道与「查询失败 / 登录失效」告警的离线单测。

背景：2026-10 线上两个 anyrouter 账号 session 失效三周，日志里每天都有 401，却没有任何
推送 —— 因为失败分支只写一行日志、从不告警。这里锁住修复后的行为。

    .venv/bin/python -m pytest tests/test_monitor_alerts.py -q
"""

import asyncio
import json

import pytest

import balance_server as bs
import server.monitor as mon


def fresh_state() -> dict:
	return {'running': False, 'failed_counts': {}, 'alerted_failures': set()}


# ===== 连续失败计数与告警节流 =====


def test_登录失效第一轮就告警且同账号不重复(monkeypatch):
	monkeypatch.setattr(bs, 'monitor_state', fresh_state())
	key = 'cookie:/a'
	assert mon.note_query_failure(key, 'HTTP 401') is True
	assert mon.note_query_failure(key, 'HTTP 401') is False, '同账号只告警一次'
	assert bs.monitor_state['failed_counts'][key] == 2


def test_普通失败要连续两轮才告警(monkeypatch):
	monkeypatch.setattr(bs, 'monitor_state', fresh_state())
	key = 'site:gorouter/a'
	assert mon.note_query_failure(key, 'HTTP 502') is False, '单轮抖动不吵人'
	assert mon.note_query_failure(key, 'HTTP 502') is True
	assert mon.note_query_failure(key, 'HTTP 502') is False


def test_查询恢复正常后重新武装(monkeypatch):
	monkeypatch.setattr(bs, 'monitor_state', fresh_state())
	key = 'cookie:/a'
	mon.note_query_failure(key, 'HTTP 401')
	mon.reset_query_failure(key)
	assert bs.monitor_state['failed_counts'] == {}
	assert bs.monitor_state['alerted_failures'] == set()
	assert mon.note_query_failure(key, 'HTTP 401') is True, '好过一轮之后再坏要能再次告警'


def test_限流不是登录失效():
	assert mon._is_auth_failure('被站点安全策略拦截（ESA http_ratelimit）') is False
	assert mon._is_auth_failure('HTTP 502') is False
	assert mon._is_auth_failure('HTTP 401') is True
	assert mon._is_auth_failure('登录已过期') is True


# ===== 统一告警出口 =====


def test_没有任何通道时返回提示不抛异常(monkeypatch, tmp_path):
	cfg = tmp_path / 'saved_config.json'
	cfg.write_text('{}', encoding='utf-8')
	monkeypatch.setattr(bs, 'CONFIG_FILE', cfg)
	assert mon.load_alert_email() is None
	assert asyncio.run(mon.send_alert('t', 'b')) == '未配置任何告警通道'


def test_配了邮箱就发邮件(monkeypatch, tmp_path):
	cfg = tmp_path / 'saved_config.json'
	cfg.write_text(
		json.dumps({'email': {'smtp_server': 's', 'smtp_port': 465, 'email_user': 'u', 'email_pass': 'p', 'email_to': 'to@x'}}),
		encoding='utf-8',
	)
	monkeypatch.setattr(bs, 'CONFIG_FILE', cfg)
	sent = []
	monkeypatch.setattr(mon, 'send_alert_email', lambda c, subject, body: sent.append((c.email_to, subject)))
	assert mon.load_alert_email() is not None
	assert asyncio.run(mon.send_alert('标题', '正文')) == '邮件已发'
	assert sent == [('to@x', '标题')]


def test_邮件发送失败不抛出只回报(monkeypatch, tmp_path):
	cfg = tmp_path / 'saved_config.json'
	cfg.write_text(
		json.dumps({'email': {'smtp_server': 's', 'smtp_port': 465, 'email_user': 'u', 'email_pass': 'p', 'email_to': 'to@x'}}),
		encoding='utf-8',
	)
	monkeypatch.setattr(bs, 'CONFIG_FILE', cfg)

	def boom(*a):
		raise RuntimeError('smtp 挂了')

	monkeypatch.setattr(mon, 'send_alert_email', boom)
	assert '邮件失败' in asyncio.run(mon.send_alert('t', 'b'))


def test_只发邮件时不动webhook(monkeypatch, tmp_path):
	cfg = tmp_path / 'saved_config.json'
	cfg.write_text(
		json.dumps(
			{
				'email': {'smtp_server': 's', 'smtp_port': 465, 'email_user': 'u', 'email_pass': 'p', 'email_to': 'to@x'},
				'notify': {'type': 'bark', 'url': 'https://api.day.app/k', 'on_alert': False},
			}
		),
		encoding='utf-8',
	)
	monkeypatch.setattr(bs, 'CONFIG_FILE', cfg)
	monkeypatch.setattr(mon, 'send_alert_email', lambda *a: None)
	called = []

	async def fake_notify(title, body):
		called.append(title)
		return {'sent': True}

	monkeypatch.setattr(bs, 'send_webhook_notify', fake_notify)
	assert asyncio.run(mon.send_alert('t', 'b', webhook=False)) == '邮件已发'
	assert called == [], 'webhook=False 时不该推 webhook'
	assert asyncio.run(mon.send_alert('t', 'b', webhook=True)) == '邮件已发、webhook 已发'
	assert called == ['t']


# ===== 邮件发送的偶发失败重试 =====


def _email_cfg() -> mon.EmailConfig:
	return mon.EmailConfig(smtp_server='smtp.x', smtp_port=465, email_user='u', email_pass='p', email_to='to@x')


def test_邮件握手超时会重试一次(monkeypatch):
	attempts = []

	class FlakySMTP:
		def __init__(self, *a, **kw):
			attempts.append(1)
			if len(attempts) == 1:
				raise TimeoutError('The handshake operation timed out')

		def __enter__(self):
			return self

		def __exit__(self, *a):
			return False

		def login(self, *a):
			pass

		def send_message(self, *a):
			pass

	monkeypatch.setattr(mon.smtplib, 'SMTP_SSL', FlakySMTP)
	monkeypatch.setattr('time.sleep', lambda s: None)
	mon.send_alert_email(_email_cfg(), '标题', '正文')
	assert len(attempts) == 2, '首次握手超时应重试一次（免费邮箱会节制连接数）'


def test_邮件一直失败最终仍抛出(monkeypatch):
	class DeadSMTP:
		def __init__(self, *a, **kw):
			raise TimeoutError('connection refused')

	monkeypatch.setattr(mon.smtplib, 'SMTP_SSL', DeadSMTP)
	monkeypatch.setattr('time.sleep', lambda s: None)
	with pytest.raises(TimeoutError):
		mon.send_alert_email(_email_cfg(), '标题', '正文')
