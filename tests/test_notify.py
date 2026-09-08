"""Webhook 通知器的离线单测：各渠道请求形状、配置读写、切换渠道校验、挂钩条件。

不发任何上游请求 —— HTTP 用假 Session 替身驱动。
    .venv/bin/python -m pytest tests/test_notify.py -q
"""

import asyncio
import json

import pytest

import balance_server as bs


class FakeNotifySession:
	"""记录 POST 调用的会话替身，可选吐自定义状态码。"""

	def __init__(self):
		self.calls = []
		self.status_code = 200

	def post(self, url, json=None, data=None, timeout=None):
		self.calls.append({'url': url, 'json': json, 'data': data, 'timeout': timeout})
		return self

	def __call__(self):
		return self


@pytest.fixture
def config_file(tmp_path, monkeypatch):
	"""隔离 saved_config.json。"""
	cfg = tmp_path / 'saved_config.json'
	monkeypatch.setattr(bs, 'CONFIG_FILE', cfg)
	return cfg


@pytest.fixture
def capture(monkeypatch):
	"""替换 _get_cffi_session 为通知专用的假会话，返回 (会话, 调用列表)。"""
	sess = FakeNotifySession()
	monkeypatch.setattr(bs, '_get_cffi_session', lambda key, proxies=None: sess)
	calls = sess.calls
	sess.calls = calls
	return sess, calls


def write_notify(config_file, **notify):
	config_file.write_text(json.dumps({'notify': notify}, ensure_ascii=False), encoding='utf-8')


# ===== 各渠道请求形状 =====


def test_telegram_发json带chat_id(capture, config_file):
	write_notify(config_file, type='telegram', url='https://api.telegram.org/botTK/sendMessage', chat_id='42')
	_, calls = capture
	r = asyncio.run(bs.send_webhook_notify('标题', '正文'))
	assert r['sent'] and r.get('error') is None
	assert calls[0]['url'] == 'https://api.telegram.org/botTK/sendMessage'
	assert calls[0]['json'] == {'chat_id': '42', 'text': '标题\n\n正文'}
	assert calls[0]['data'] is None


def test_serverchan_发表单编码且自动补send后缀(capture, config_file):
	write_notify(config_file, type='serverchan', url='https://sctapi.ftqq.com/KEY123')
	_, calls = capture
	asyncio.run(bs.send_webhook_notify('标题', '正文'))
	assert calls[0]['url'] == 'https://sctapi.ftqq.com/KEY123.send', 'Server酱只认 *.send'
	assert calls[0]['data'] == {'title': '标题', 'desp': '正文'}, 'Server酱只认表单编码'
	assert calls[0]['json'] is None


def test_serverchan_url已带send后缀不重复(capture, config_file):
	write_notify(config_file, type='serverchan', url='https://sctapi.ftqq.com/KEY.send')
	_, calls = capture
	asyncio.run(bs.send_webhook_notify('t', 'b'))
	assert calls[0]['url'] == 'https://sctapi.ftqq.com/KEY.send'


def test_bark_从url提取key转push端点(capture, config_file):
	write_notify(config_file, type='bark', url='https://api.day.app/DEVKEY999')
	_, calls = capture
	asyncio.run(bs.send_webhook_notify('标题', '正文'))
	assert calls[0]['url'] == 'https://api.day.app/push'
	assert calls[0]['json']['device_key'] == 'DEVKEY999'
	assert calls[0]['json']['title'] == '标题'
	assert calls[0]['json']['group'] == 'newapi-checkin'


def test_bark_自定义服务器带子路径(capture, config_file):
	write_notify(config_file, type='bark', url='https://bark.example.com/sub/DEVKEY1')
	_, calls = capture
	asyncio.run(bs.send_webhook_notify('t', 'b'))
	assert calls[0]['url'] == 'https://bark.example.com/push'
	assert calls[0]['json']['device_key'] == 'DEVKEY1'


def test_bark_url缺key时报错不发(capture, config_file):
	write_notify(config_file, type='bark', url='https://api.day.app')
	_, calls = capture
	r = asyncio.run(bs.send_webhook_notify('t', 'b'))
	assert not r['sent'] and 'device key' in r['error']
	assert calls == []


def test_generic_发json_title_message(capture, config_file):
	write_notify(config_file, type='generic', url='https://gw.example.com/hook')
	_, calls = capture
	asyncio.run(bs.send_webhook_notify('t', 'b'))
	assert calls[0]['json'] == {'title': 't', 'message': 'b'}


def test_http非200算发送失败(capture, config_file):
	write_notify(config_file, type='generic', url='https://gw.example.com/hook')
	sess, calls = capture
	sess.status_code = 500
	r = asyncio.run(bs.send_webhook_notify('t', 'b'))
	assert not r['sent'] and 'HTTP 500' in r['error']


def test_未配置直接拒绝不发请求(capture, config_file):
	_, calls = capture
	r = asyncio.run(bs.send_webhook_notify('t', 'b'))
	assert not r['sent'] and '未配置' in r['error']
	assert calls == []


# ===== 配置读写与校验 =====


def test_保存与读回(config_file):
	r = asyncio.run(bs.save_notify(bs.NotifyRequest(type='telegram', url='https://t.me/botX/sendMessage', chat_id='7')))
	assert r['success']
	n = bs.get_notify_config()
	assert n['type'] == 'telegram' and n['chat_id'] == '7' and n['on_alert'] is True and n['on_checkin_failed'] is False


def test_url留空保留旧值(config_file):
	write_notify(config_file, type='generic', url='https://old.example.com/hook')
	asyncio.run(bs.save_notify(bs.NotifyRequest(type='generic', url='')))
	assert bs.get_notify_config()['url'] == 'https://old.example.com/hook'


def test_换渠道不重填url被拒绝(config_file):
	write_notify(config_file, type='telegram', url='https://api.telegram.org/botTK/sendMessage')
	r = asyncio.run(bs.save_notify(bs.NotifyRequest(type='serverchan', url='')))
	assert not r['success'] and '重新填写' in r['error']
	# 同渠道留空仍允许（改开关场景）
	r2 = asyncio.run(bs.save_notify(bs.NotifyRequest(type='telegram', url='', chat_id='9')))
	assert r2['success']


def test_非法渠道被拒绝(config_file):
	r = asyncio.run(bs.save_notify(bs.NotifyRequest(type='wechat', url='x')))
	assert not r['success'] and '未知通知类型' in r['error']


def test_mask_secret_短key只露8字符():
	assert bs._mask_secret('A' * 20) == 'AAAAAAAA…'
	assert bs._mask_secret('') == ''
	long = 'https://api.telegram.org/bot' + 'X' * 40
	m = bs._mask_secret(long)
	assert m.startswith('https://api.telegram.org/bot') and m.endswith('…' + 'X' * 8)
