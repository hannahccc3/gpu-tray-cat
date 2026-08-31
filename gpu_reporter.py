#!/usr/bin/env python3
"""GPU 显存上报端 —— 部署在被监控的服务器上。

每隔 INTERVAL 秒读取一次 nvidia-smi，把结果 POST 到接收端。
只依赖 Python 标准库 + nvidia-smi 命令。
"""
import json
import os
import pwd
import subprocess
import time
import socket
import urllib.request
from datetime import datetime, timezone

# ===== 配置 =====
# 敏感值（地址/token）放在本地未跟踪的 local_config.py 中，格式见 README。
try:
    from local_config import TARGET_URL, INTERVAL, TOKEN
except ImportError:
    TARGET_URL = "http://<接收端IP>:8787/gpu"  # 接收端地址
    INTERVAL = 60                             # 上报间隔（秒）
    TOKEN = "change-me"                       # 简单鉴权，两端保持一致
# ================


def collect_gpu_stats():
    out = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    )
    gpus = []
    for line in out.strip().splitlines():
        idx, name, mem_used, mem_total, util, temp = [x.strip() for x in line.split(",")]
        gpus.append(
            {
                "index": int(idx),
                "name": name,
                "memory_used_mb": int(mem_used),
                "memory_total_mb": int(mem_total),
                "utilization_pct": int(util),
                "temperature_c": int(temp),
            }
        )
    return gpus


def collect_gpu_users():
    """按用户聚合显存占用（基于 nvidia-smi 进程级数据 + /proc 反查属主）。"""
    out = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory",
         "--format=csv,noheader,nounits"],
        text=True,
    )
    agg = {}
    for line in out.strip().splitlines():
        try:
            pid_s, mem_s = [x.strip() for x in line.split(",")]
            pid, mem = int(pid_s), int(mem_s)
            uid = os.stat(f"/proc/{pid}").st_uid
            user = pwd.getpwuid(uid).pw_name
        except Exception:
            continue  # 进程刚好退出等情况，跳过
        u = agg.setdefault(user, {"user": user, "mem_mb": 0, "procs": 0})
        u["mem_mb"] += mem
        u["procs"] += 1
    return sorted(agg.values(), key=lambda x: -x["mem_mb"])


def report(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        TARGET_URL,
        data=data,
        headers={"Content-Type": "application/json", "X-Token": TOKEN},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10).read()


def main():
    host = socket.gethostname()
    while True:
        try:
            payload = {
                "host": host,
                "ts": datetime.now(timezone.utc).isoformat(),
                "gpus": collect_gpu_stats(),
                "users": collect_gpu_users(),
            }
            report(payload)
        except Exception as e:
            # 网络抖动等不致命，打印后继续
            print(f"[{datetime.now().isoformat()}] report failed: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
