#!/usr/bin/env python3
"""GPU 显存上报端 —— 部署在被监控的服务器上。

每隔 INTERVAL 秒读取一次 nvidia-smi，把结果 POST 到接收端。
只依赖 Python 标准库 + nvidia-smi 命令。
"""
import json
import os
import pwd
import re
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


# ===== reporter.log 解析 =====
# 管理员脚本会在服务器上定时生成 reporter.log（人类可读的 GPU 检查报告），
# 这里解析最后一个检查块，把进程级任务信息随上报一起发走。
REPORTER_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "reporter.log")

_PID_RE = re.compile(r"^\s*- PID (\d+) \(uid=\d+ user=([^)]+)\):")
_FIELD_RE = re.compile(r"^\s*(显存|CPU|已运行|命令|预计剩余)[:：]\s*(.*)$")


def _elapsed_to_seconds(s):
    """ps etime 格式: [[d-]h:]m:s 或 d-h:m:s → 秒；解析失败返回 None。"""
    m = re.match(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$", s.strip())
    if not m:
        return None
    d, h, mi, sec = (int(g) if g else 0 for g in m.groups())
    # 无 d- 前缀时，格式是 h:m:s（3 段）或 m:s（2 段）
    if m.group(1) is None and m.group(2) is None:
        d, h, mi, sec = 0, 0, mi, sec
    return d * 86400 + h * 3600 + mi * 60 + sec


def _short_cmd(cmd):
    """从完整命令提取脚本/程序名，如 'python /a/b/main.py --x' → 'main.py'。"""
    tokens = cmd.split()
    for t in tokens[1:]:  # 优先找 .py 脚本
        if t.endswith(".py"):
            return os.path.basename(t)
    for t in tokens:  # 否则跳过 python 解释器，取第一个实际程序
        base = os.path.basename(t)
        if not re.match(r"^(python[\d.]*)$", base):
            return base
    return os.path.basename(tokens[0]) if tokens else cmd


def parse_reporter_log(path=REPORTER_LOG):
    """解析 reporter.log 最后一个检查块，返回任务列表（可能为空）。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return []
    idx = text.rfind("【GPU 检查】")
    if idx < 0:
        return []
    block = text[idx:]

    jobs = []
    job = None
    in_eta = False
    for line in block.splitlines():
        m = _PID_RE.match(line)
        if m:
            job = {"pid": int(m.group(1)), "user": m.group(2),
                   "mem_mb": None, "cpu_pct": None,
                   "elapsed_s": None, "elapsed": "", "cmd": "", "eta": ""}
            jobs.append(job)
            in_eta = False
            continue
        if job is None:
            continue
        if line.startswith("## "):  # 进程区块结束（如"## 用户汇总"）
            job = None
            continue
        fm = _FIELD_RE.match(line)
        if fm:
            key, val = fm.group(1), fm.group(2).strip()
            in_eta = key == "预计剩余"
            if key == "显存":
                mm = re.match(r"(\d+)", val)
                job["mem_mb"] = int(mm.group(1)) if mm else None
            elif key == "CPU":
                mm = re.match(r"([\d.]+)", val)
                job["cpu_pct"] = float(mm.group(1)) if mm else None
            elif key == "已运行":
                job["elapsed"] = val
                job["elapsed_s"] = _elapsed_to_seconds(val)
            elif key == "命令":
                job["cmd"] = _short_cmd(val)
            continue
        if in_eta:
            em = re.match(r"^\s+- .*?[:：]\s*(.+)$", line)
            if em:
                # "预计剩余"下可能有多行（模式/经验估计…），优先含"估计/剩余"的
                candidate = em.group(1).strip()
                if not job["eta"] or re.search(r"估计|剩余", line):
                    job["eta"] = candidate
    return [j for j in jobs if j["mem_mb"] is not None]
# ================================


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
                "jobs": parse_reporter_log(),
            }
            report(payload)
        except Exception as e:
            # 网络抖动等不致命，打印后继续
            print(f"[{datetime.now().isoformat()}] report failed: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
