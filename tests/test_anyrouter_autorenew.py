"""run_anyrouter_checkin 顺带自动续期（server/cookies.py 的 _auto_renew_stale_cookies）的离线单测。

不发任何上游请求：签到与续期都打 monkeypatch 的假实现。
    .venv/bin/python -m pytest tests/test_anyrouter_autorenew.py -q
"""

import asyncio
import time

import pytest

import balance_server as bs
import server.cookies as ck


@pytest.fixture(autouse=True)
def _clear_relogin_alerts():
    """失效告警节流是模块级状态，用例之间必须隔离"""
    ck._relogin_alerted.clear()
    yield
    ck._relogin_alerted.clear()


def fresh_state() -> dict:
    """每测试一新的签到状态字典（run_anyrouter_checkin 会往里写、由 add_anyrouter_checkin_log 追加日志）"""
    return {'running': False, 'accounts': {}, 'logs': []}


def acc(name: str, session: str = 'S', api_user: str = '1') -> ck.AccountItem:
    return ck.AccountItem(name=name, cookies={'session': session}, api_user=api_user)


def run_checkin(monkeypatch, accounts, sign_in_result=None, expiry_map=None, renew_result=None, concurrency=4, notify=None):
    """搭好替身并跑一轮 run_anyrouter_checkin，返回 (st, calls) 供断言。

    sign_in_result：签到假实现返回的 dict；expiry_map：session 字符串 → _session_expiry_info 的返回；
    notify：覆盖 get_notify_config 的字段（默认 on_checkin_failed=False，与出厂一致）。
    """
    state = fresh_state()
    calls = {'renew': [], 'sign_in': [], 'updates': {}, 'alerts': []}

    async def fake_send_alert(subject, body, webhook=True, email_cfg=None):
        calls['alerts'].append({'subject': subject, 'body': body, 'webhook': webhook})
        return '邮件已发'

    monkeypatch.setattr(bs, 'send_alert', fake_send_alert)
    monkeypatch.setattr(
        bs,
        'get_notify_config',
        lambda: {'type': '', 'url': '', 'chat_id': '', 'on_alert': True, 'on_checkin_failed': False, **(notify or {})},
    )

    async def fake_sign_in(account, waf):
        calls['sign_in'].append(account.name)
        return sign_in_result or {'name': account.name, 'success': True, 'message': '', 'already_signed': False}

    async def fake_waf():
        return {'acw_tc': 'x'}

    async def fake_renew(account, waf):
        calls['renew'].append(account.name)
        if renew_result is not None:
            return {**renew_result, 'name': account.name}
        return {
            'name': account.name,
            'success': True,
            'message': '续期成功',
            'new_session': f'NEW-{account.name}',
            'expires_at': '2030-01-01 00:00:00',
            'days_left': 30.0,
        }

    monkeypatch.setattr(ck, 'load_cookie_accounts', lambda: accounts)
    monkeypatch.setattr(ck, 'save_anyrouter_checkin_state', lambda: None)
    monkeypatch.setattr(ck, 'save_renewed_sessions', lambda updates: calls['updates'].update(updates))
    monkeypatch.setattr(bs, 'anyrouter_checkin_state', state)
    monkeypatch.setattr(bs, 'sign_in', fake_sign_in)
    monkeypatch.setattr(bs, '_get_waf_cookies_if_needed', fake_waf)
    monkeypatch.setattr(bs, 'renew_one_cookie', fake_renew)
    monkeypatch.setattr(bs, '_session_expiry_info', lambda s: (expiry_map or {}).get(s, {'expires_at': 'x', 'days_left': 30.0}))
    monkeypatch.setattr(bs, 'ANYROUTER_CONCURRENCY', concurrency)

    asyncio.run(ck.run_anyrouter_checkin('manual'))
    return state, calls


def test_有效期充足时签到完不触发续期(monkeypatch):
    st, calls = run_checkin(monkeypatch, [acc('a', 'A'), acc('b', 'B')], expiry_map={'A': {'expires_at': 'x', 'days_left': 30.0}, 'B': {'expires_at': 'x', 'days_left': 29.9}})
    assert calls['renew'] == []
    assert [a['status'] for a in st['accounts'].values()] == ['signed', 'signed']
    assert any('全部 cookie 有效期充足' in rec['message'] for rec in st['logs'])


def test_剩余天数低于阈值时自动续期并写回新session(monkeypatch):
    # A 只剩 3 天，B 还有 25 天 → 只续 A
    st, calls = run_checkin(
        monkeypatch,
        [acc('a', 'A'), acc('b', 'B')],
        expiry_map={'A': {'expires_at': 'x', 'days_left': 3.0}, 'B': {'expires_at': 'x', 'days_left': 25.0}},
    )
    assert calls['renew'] == ['a']
    assert calls['updates'] == {'a': 'NEW-a'}  # 新 session 已写回 saved_config
    assert any('自动续期 1 个临期账号：a' in rec['message'] for rec in st['logs'])
    assert any('a: 自动续期成功' in rec['message'] for rec in st['logs'])
    assert not any('b' in rec['message'] and '续期' in rec['message'] for rec in st['logs'])


def test_本地解码失败也算临期触发续期核实(monkeypatch):
    # _session_expiry_info 返回 None（结构变了/判不出来）→ 交给 renew 打接口核实，别把账号晾着
    st, calls = run_checkin(monkeypatch, [acc('a', 'A')], expiry_map={'A': None})
    assert calls['renew'] == ['a']


def test_签到撞上站点限流时整轮跳过续期(monkeypatch):
    st, calls = run_checkin(
        monkeypatch,
        [acc('a', 'A')],
        sign_in_result={'name': 'a', 'success': False, 'message': '被站点安全策略拦截（ESA http_ratelimit）', 'blocked': 'ratelimit'},
        expiry_map={'A': {'expires_at': 'x', 'days_left': 1.0}},
    )
    assert calls['renew'] == []
    assert calls['updates'] == {}
    assert any('签到期间撞上站点限流' in rec['message'] for rec in st['logs'])


def test_续期中途撞限流时中止剩余账号(monkeypatch):
    # 并发度压到 1 保证顺序：a 先续、撞限流 → b 直接跳过，不再发上游请求
    st, calls = run_checkin(
        monkeypatch,
        [acc('a', 'A'), acc('b', 'B')],
        expiry_map={'A': {'expires_at': 'x', 'days_left': 1.0}, 'B': {'expires_at': 'x', 'days_left': 2.0}},
        renew_result={'success': False, 'message': '被站点安全策略拦截（ESA http_ratelimit）', 'blocked': 'ratelimit'},
        concurrency=1,
    )
    assert calls['renew'] == ['a']
    assert calls['updates'] == {}
    assert any('a: 自动续期失败' in rec['message'] for rec in st['logs'])
    # b 被跳过：不计成功也不计失败，汇总行只报 a 的失败
    assert any('自动续期结束：成功 0 · 失败 1' in rec['message'] for rec in st['logs'])
    assert not any('b:' in rec['message'] and '续期' in rec['message'] for rec in st['logs'])


# ===== 登录失效：立刻试续期 + 推送告警（2026-10 线上那次账号哑了三周无推送）=====


def test_签到返401时即使有效期充足也立刻试续期(monkeypatch):
    # 登录态什么时候失效和 cookie 上自称的到期时间是两回事（站点可提前作废），
    # 只看 days_left 会让账号静默烂到期 —— 线上 126296 用量断了 19 天，续期一次都没触发过
    st, calls = run_checkin(
        monkeypatch,
        [acc('a', 'A')],
        sign_in_result={'name': 'a', 'success': False, 'message': 'HTTP 401', 'blocked': 'http'},
        expiry_map={'A': {'expires_at': 'x', 'days_left': 29.0}},
    )
    assert calls['renew'] == ['a'], '签到已 401，即便 cookie 自称还有 29 天也要试续期'


def test_续期结论是登录失效时推送告警(monkeypatch):
    st, calls = run_checkin(
        monkeypatch,
        [acc('a', 'A')],
        sign_in_result={'name': 'a', 'success': False, 'message': 'HTTP 401', 'blocked': 'http'},
        expiry_map={'A': {'expires_at': 'x', 'days_left': 1.0}},
        renew_result={'success': False, 'message': 'cookie 已失效，无法续期，请重新登录'},
    )
    assert len(calls['alerts']) == 1
    alert = calls['alerts'][0]
    assert '登录已失效' in alert['subject']
    assert 'a' in alert['body'] and 'session' in alert['body'], '正文要告诉用户怎么换 cookie'
    assert any('登录失效告警' in rec['message'] for rec in st['logs'])


def test_失效告警七天节流(monkeypatch):
    args = dict(
        accounts=[acc('a', 'A')],
        sign_in_result={'name': 'a', 'success': False, 'message': 'HTTP 401', 'blocked': 'http'},
        expiry_map={'A': {'expires_at': 'x', 'days_left': 1.0}},
        renew_result={'success': False, 'message': 'cookie 已失效，无法续期，请重新登录'},
    )
    st1, calls1 = run_checkin(monkeypatch, **args)
    assert len(calls1['alerts']) == 1
    # 第二轮：账号还是坏的，但 7 天节流窗口内不再重复发信，只记一行日志
    st2, calls2 = run_checkin(monkeypatch, **args)
    assert calls2['alerts'] == []
    assert any('告警 7 天内不重复' in rec['message'] for rec in st2['logs'])


def test_续期成功后解除失效节流(monkeypatch):
    ck._relogin_alerted['a'] = time.time()
    st, calls = run_checkin(monkeypatch, [acc('a', 'A')], expiry_map={'A': {'expires_at': 'x', 'days_left': 1.0}})
    assert 'a' not in ck._relogin_alerted, '账号活过来了，下次再失效要能立刻告警'


def test_限流跳过续期时不误报告警(monkeypatch):
    # 续期整轮被限流跳过 → 拿不到续期结论；签到结果是限流文案而不是 401，不该报"登录失效"
    st, calls = run_checkin(
        monkeypatch,
        [acc('a', 'A')],
        sign_in_result={'name': 'a', 'success': False, 'message': '被站点安全策略拦截（ESA http_ratelimit）', 'blocked': 'ratelimit'},
        expiry_map={'A': {'expires_at': 'x', 'days_left': 1.0}},
    )
    assert calls['alerts'] == []


def test_签到失败推送受on_checkin_failed开关控制(monkeypatch):
    failing = {'name': 'a', 'success': False, 'message': 'HTTP 500'}
    _, calls_off = run_checkin(monkeypatch, [acc('a', 'A')], sign_in_result=failing)
    assert calls_off['alerts'] == [], '开关默认关，不推送'

    st, calls_on = run_checkin(monkeypatch, [acc('a', 'A')], sign_in_result=failing, notify={'on_checkin_failed': True})
    assert len(calls_on['alerts']) == 1
    assert '签到失败' in calls_on['alerts'][0]['subject']
    assert any('失败通知推送' in rec['message'] for rec in st['logs'])

