#!/usr/bin/env python3
"""GPU 显存接收端 —— 运行在你本机。

监听 8787 端口，接收上报并在终端打印，同时追加写入 gpu_log.csv。
只依赖 Python 标准库。用法: python3 gpu_receiver.py
"""
import csv
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 数据/配置文件放在脚本（或 exe）所在目录，与启动时的工作目录无关
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    sys.path.insert(0, BASE)  # exe 模式下让 local_config 可从 exe 旁边加载
else:
    BASE = os.path.dirname(os.path.abspath(__file__))

# ===== 配置 =====
PORT = 8787
try:
    from local_config import TOKEN
except ImportError:
    TOKEN = "change-me"  # 与上报端保持一致
# ================

LOG_FILE = os.path.join(BASE, "gpu_log.csv")
LATEST_FILE = os.path.join(BASE, "gpu_latest.json")  # 最新一条完整数据，供托盘程序读取


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/gpu" or self.headers.get("X-Token") != TOKEN:
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        host = payload.get("host", "?")
        ts = payload.get("ts", datetime.now().isoformat())
        rows = []
        for g in payload.get("gpus", []):
            line = (
                f"[{ts}] {host} GPU{g['index']} ({g['name']}): "
                f"{g['memory_used_mb']}/{g['memory_total_mb']} MiB, "
                f"util {g['utilization_pct']}%, {g['temperature_c']}C"
            )
            print(line, flush=True)
            rows.append(
                [ts, host, g["index"], g["name"], g["memory_used_mb"],
                 g["memory_total_mb"], g["utilization_pct"], g["temperature_c"]]
            )

        write_header = not os.path.exists(LOG_FILE)
        with open(LOG_FILE, "a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["ts", "host", "gpu_index", "gpu_name",
                            "mem_used_mb", "mem_total_mb", "util_pct", "temp_c"])
            w.writerows(rows)

        users = payload.get("users", [])
        if users:
            print("    users: " + ", ".join(
                f"{u['user']}={u['mem_mb']}MiB({u['procs']}p)" for u in users[:5]),
                flush=True)

        # 原子写入最新状态文件，供托盘程序读取
        tmp = LATEST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, LATEST_FILE)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass  # 静默默认的访问日志


if __name__ == "__main__":
    print(f"listening on 0.0.0.0:{PORT}, logging to {LOG_FILE}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
