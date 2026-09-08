"""无状态基础设施：原子 JSON 读写（mtime 缓存）、后台任务强引用、上游线程池、
curl_cffi 会话（按线程复用）。

设计约定：业务域一律 `from server.common import <名字>`；测试要替换其中某个实现时
patch **被测模块**的绑定（如 `server.notify._get_cffi_session`），与此前 patch
`bs._get_cffi_session` 的语义一致（bs 过渡期重导出同一批名字）。
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _atomic_write_json(path: Path, data, indent: int | None = None) -> None:
	"""原子写 JSON：先写临时文件再 os.replace。

	直接 write_text 覆盖原文件，进程在写入中途崩溃/断电会留下半个 JSON；
	os.replace 在同一文件系统上是原子的，最坏情况也只是旧文件完好无损。
	"""
	tmp = path.with_name(path.name + '.tmp')
	tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding='utf-8')
	os.replace(tmp, path)
	# 本进程是这些配置文件唯一的写入方，写完直接丢缓存，下次读按新 mtime 重建
	_json_cache.pop(path, None)


# 配置文件读多写少（几乎每个 API 请求都要 load 一遍账号/站点清单），
# 按 (mtime_ns, size) 缓存解析结果：文件没变就只做一次 stat，不再反复读盘 + json.loads。
# 进程外手动改文件也会因 mtime 变化自动失效。
_json_cache: dict[Path, tuple[tuple[int, int], object]] = {}


def _read_json_cached(path: Path) -> object:
	"""读 JSON 并走 mtime 缓存；文件不存在抛 FileNotFoundError，损坏抛 JSONDecodeError"""
	try:
		st = path.stat()
	except OSError as e:
		_json_cache.pop(path, None)
		raise FileNotFoundError(str(path)) from e
	stamp = (st.st_mtime_ns, st.st_size)
	cached = _json_cache.get(path)
	if cached is not None and cached[0] == stamp:
		return cached[1]
	data = json.loads(path.read_text(encoding='utf-8'))
	_json_cache[path] = (stamp, data)
	return data


def _read_json_models(path: Path, model, tag: str) -> list:
	"""读取「JSON 数组 + pydantic 模型」配置文件的公共样板；文件不存在或损坏都返回空列表"""
	try:
		data = _read_json_cached(path)
		return [model(**item) for item in data]
	except FileNotFoundError:
		return []
	except Exception as e:
		print(f'[{tag}] 加载 {path.name} 失败: {e}')
		return []


# 后台任务强引用：事件循环对 task 只持弱引用，不保存随时可能被 GC 静默杀掉
_background_tasks: set = set()


def _spawn(coro):
	"""create_task 并持有引用，结束后自动清理。所有长生命周期调度器都该走这里。"""
	import asyncio

	task = asyncio.create_task(coro)
	_background_tasks.add(task)
	task.add_done_callback(_background_tasks.discard)
	return task


# 上游请求专用线程池。curl_cffi 是同步库，靠线程池并发；此前用的是 asyncio 默认线程池，
# 容量固定为 min(32, cpu_count + 4)，本机 4 核 = 8 个 worker，成了真正的瓶颈：
# 实测 162 个请求在 Semaphore=15/池=8 下 14.2s，池放到 20 后 7.5s，光加 Semaphore 无效。
_UPSTREAM_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix='upstream')

_thread_local = threading.local()


def _get_cffi_session(key: str, proxies: dict | None = None):
	"""取当前线程的 curl_cffi Session（按 key 区分不同站点/代理配置）。

	复用 Session 才能复用代理 CONNECT 隧道与 TLS 握手，实测单请求中位耗时 0.56s → 0.19s。

	注意：curl_cffi 的 Session 会把每次请求传入的 cookies 累积进自己的 jar，并在后续请求中
	继续发送（已实测），而同一个 Session 会被不同账号轮流复用，所以每次取用时必须清空 cookie，
	否则上一个账号的 session cookie 会串到下一个账号的请求上。
	"""
	from curl_cffi import requests as cffi_requests

	pool = getattr(_thread_local, 'sessions', None)
	if pool is None:
		pool = {}
		_thread_local.sessions = pool
	sess = pool.get(key)
	if sess is None:
		sess = cffi_requests.Session(impersonate='chrome131', proxies=proxies, timeout=30)
		pool[key] = sess
	sess.cookies.clear()
	return sess
