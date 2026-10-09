# 部署备忘（systemd）

线上是单进程 FastAPI（`python balance_server.py`）跑在一台小内存 VPS 上，由 systemd 托管。
这里记两件**必须配的**事，以及它们的来由 —— 漏掉任何一条都会重现已经踩过的坑。

## systemd unit

```ini
# /etc/systemd/system/newapi-checkin.service
[Unit]
Description=New API Balance Manager
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/newapi-checkin
Environment=TZ=Asia/Shanghai
Environment=PYTHONUNBUFFERED=1
LimitNOFILE=65535
ExecStart=/opt/newapi-checkin/.venv/bin/python balance_server.py
Restart=always
RestartSec=5
Nice=5

[Install]
WantedBy=multi-user.target
```

### 为什么必须有 LimitNOFILE

默认软限是 **1024**，而这个服务同时在跑：24 个站点的并发请求（上游线程池 32）、
静态文件与 JSON 读写、以及每个线程各自缓存的 curl 会话（每个会话 1 个 eventfd，
带 keep-alive 时再加 1 个 socket）。

2026-10-09 线上就是这么挂的：fd 表被顶到 1023/1024，`accept()` 开始报 EMFILE、
首页静态文件 500，但后台定时任务还在跑 —— 表现是「网站打不开、签到却在动」。
代码侧已把会话缓存改成每线程有界 LRU（上限 8，淘汰即 close，见 `server/common.py`），
但 fd 上限仍要放宽：按 32 线程 × 8 会话 × 2 个 fd 算，最坏也要 ~512 个。

### 为什么必须有 PYTHONUNBUFFERED

非 TTY 下 Python 的 stdout 是块缓冲（攒满 ~8KB 才刷），日志会滞后几分钟。排查
「告警到底发没发」「签到卡在哪一步」时看不到实时输出，等于没日志。加上它之后写一行刷一行。

## 数据文件

全部在 `WorkingDirectory` 下（即 `/opt/newapi-checkin`），**不进仓库**：

| 文件 | 内容 |
|---|---|
| `newapi_sites.json` | 站点清单（含 `use_proxy` 开关） |
| `saved_config.json` | anyrouter cookie 账号 + SMTP 邮箱 + webhook 通知配置 |
| `*_accounts.json` / `*_checkin_state.json` | 各站点账号与签到状态 |
| `daily_usage.json` | 每日用量快照（保留 90 天） |
| `.env` | 登录凭据、代理、MIHOMO_GROUP、COLLECT_KEY |

## 部署与回滚

代码用 tar 包铺上去（不是 git 仓库）：

```bash
git archive --format=tar.gz -o /tmp/deploy.tar.gz HEAD balance_server.py server
scp /tmp/deploy.tar.gz <host>:/root/ && ssh <host> 'cd /opt/newapi-checkin && tar xzf /root/deploy.tar.gz && systemctl restart newapi-checkin'
```

改代码前先备份，回滚就是把备份铺回去再重启：

```bash
tar czf /root/newapi-code-backup/code-$(date +%Y%m%d-%H%M%S).tar.gz balance_server.py server saved_config.json
```

## 排障

```bash
systemctl status newapi-checkin          # 服务状态
journalctl -u newapi-checkin -n 100      # 最近日志（已关缓冲，实时）
ls /proc/$(systemctl show -p MainPID --value newapi-checkin)/fd | wc -l   # 当前 fd 数
ss -lptn 'sport = :8003'                 # 别用 pkill -f "uvicorn balance_server"，会误杀自己的 shell
```
