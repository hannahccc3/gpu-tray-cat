#!/usr/bin/env python3
"""GPU 监控托盘图标 —— 仿 RunCat，跑在你本机 Windows 上。

读取 gpu_receiver.py 写入的 gpu_log.csv 最后一行：
- 托盘图标是一只奔跑的猫，显存占用越高跑得越快（仿 RunCat 行为）
- 右键菜单显示服务器 GPU 的实时信息
- 数据超过 3 分钟没更新时，猫变灰并提示离线

猫咪动画帧来自 RunCat365（Apache License 2.0）:
https://github.com/runcat-dev/RunCat365

用法: pythonw gpu_tray.py   （无窗口后台运行，右键托盘图标退出）
"""
import csv
import json
import os
import threading
import time
import sys
from datetime import datetime, timezone

import pystray
from PIL import Image, ImageOps

# PyInstaller 冻结时：数据文件读 exe 所在目录，帧素材读内嵌资源目录
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    FRAMES_DIR = os.path.join(sys._MEIPASS, "frames")
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    FRAMES_DIR = os.path.join(BASE, "frames")
LOG_FILE = os.path.join(BASE, "gpu_log.csv")
JSON_FILE = os.path.join(BASE, "gpu_latest.json")
MAX_USER_ROWS = 5        # 菜单里最多显示几个用户
STALE_SEC = 180          # 超过这么久没新数据视为离线
ANIM_SLOW_MS = 450       # 占用 0% 时的帧间隔
ANIM_FAST_MS = 100       # 占用 100% 时的帧间隔

_stop = threading.Event()
_state = {"row": None, "stale": True}


def _load_frames():
    frames = []
    for i in range(5):
        img = Image.open(os.path.join(FRAMES_DIR, f"cat_{i}.png")).convert("RGBA")
        frames.append(img)
    return frames


def _gray(img):
    """保留 alpha 的灰度版，用于离线状态。"""
    g = ImageOps.grayscale(img).convert("RGBA")
    g.putalpha(img.getchannel("A"))
    return g


FRAMES = _load_frames()
FRAMES_GRAY = [_gray(f) for f in FRAMES]


def read_last_row():
    """高效读 CSV 最后一行，返回 dict 或 None。"""
    try:
        size = os.path.getsize(LOG_FILE)
        with open(LOG_FILE, "rb") as f:
            f.seek(max(0, size - 8192))
            lines = f.read().decode("utf-8", errors="ignore").strip().splitlines()
        for line in reversed(lines):
            row = next(csv.reader([line]))
            if row and row[0] != "ts" and len(row) >= 8:
                return {
                    "ts": row[0], "host": row[1], "name": row[3],
                    "used": int(row[4]), "total": int(row[5]),
                    "util": int(row[6]), "temp": int(row[7]),
                }
    except Exception:
        pass
    return None


def read_latest():
    """优先读 gpu_latest.json（含用户占用），缺失时回退到 CSV 最后一行。"""
    try:
        with open(JSON_FILE, encoding="utf-8") as f:
            p = json.load(f)
        g = p["gpus"][0]
        return {
            "ts": p["ts"], "host": p["host"], "name": g["name"],
            "used": g["memory_used_mb"], "total": g["memory_total_mb"],
            "util": g["utilization_pct"], "temp": g["temperature_c"],
            "users": p.get("users", []),
        }
    except Exception:
        row = read_last_row()
        if row:
            row["users"] = []
        return row


def is_stale(row):
    if not row:
        return True
    try:
        ts = datetime.fromisoformat(row["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() > STALE_SEC
    except Exception:
        return True


# ---------- 右键菜单文本（pystray 会在每次展开菜单时重新调用） ----------

def t_title(_):
    row, stale = _state["row"], _state["stale"]
    if not row:
        return "GPU 监控: 等待数据..."
    if stale:
        return f"{row['host']} · {row['name']} (离线)"
    return f"{row['host']} · {row['name']}"


def t_mem(_):
    row = _state["row"]
    if not row:
        return "显存: -"
    pct = row["used"] * 100 // row["total"]
    return f"显存: {row['used']}/{row['total']} MiB ({pct}%)"


def t_util(_):
    row = _state["row"]
    if not row:
        return "利用率: -   温度: -"
    return f"利用率: {row['util']}%   温度: {row['temp']}C"


def t_time(_):
    row, stale = _state["row"], _state["stale"]
    if not row:
        return ""
    try:
        local = datetime.fromisoformat(row["ts"]).astimezone()
        s = local.strftime("%H:%M:%S")
    except Exception:
        s = row["ts"]
    return ("数据已过期 " if stale else "更新于 ") + s


def on_exit(icon, _):
    _stop.set()
    icon.stop()


def _user_row(idx):
    """第 idx 个用户的菜单项：无对应数据时隐藏。"""
    def text(_):
        u = _state["row"]["users"][idx]
        return f"{u['user']}: {u['mem_mb']} MiB ({u['procs']} 个进程)"

    def visible(_):
        row = _state["row"]
        return bool(row and not _state["stale"]
                    and len(row.get("users", [])) > idx)

    return pystray.MenuItem(text, None, enabled=False, visible=visible)


def _users_header_visible(_):
    row = _state["row"]
    return bool(row and not _state["stale"] and row.get("users"))


def animate(icon):
    i = 0
    last_user_count = -1
    while not _stop.is_set():
        row = read_latest()
        stale = is_stale(row)
        _state.update(row=row, stale=stale)

        # 用户行数量变化时重建菜单，让 visible 生效
        uc = 0 if stale or not row else len(row.get("users", []))
        if uc != last_user_count:
            last_user_count = uc
            icon.update_menu()

        if stale:
            delay = ANIM_SLOW_MS / 1000
            frames = FRAMES_GRAY
            icon.title = "GPU 监控: 离线 (检查接收端是否在运行)"
        else:
            pct = row["used"] / max(row["total"], 1)
            delay = (ANIM_SLOW_MS - pct * (ANIM_SLOW_MS - ANIM_FAST_MS)) / 1000
            frames = FRAMES
            icon.title = (
                f"GPU {row['used']}/{row['total']} MiB "
                f"({row['used'] * 100 // row['total']}%) | "
                f"util {row['util']}% | {row['temp']}C"
            )

        icon.icon = frames[i % len(frames)]
        i += 1
        time.sleep(delay)


def main():
    menu = pystray.Menu(
        pystray.MenuItem(t_title, None, enabled=False),
        pystray.MenuItem(t_mem, None, enabled=False),
        pystray.MenuItem(t_util, None, enabled=False),
        pystray.MenuItem(t_time, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("用户占用", None, enabled=False,
                         visible=_users_header_visible),
        *[_user_row(i) for i in range(MAX_USER_ROWS)],
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_exit),
    )
    icon = pystray.Icon("gpu-monitor", FRAMES[0], "GPU 监控: 启动中...", menu)
    threading.Thread(target=animate, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
