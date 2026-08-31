# gpu-tray-cat

GPU 服务器显存监控：服务器端驻留上报 → 本机接收 → Windows 任务栏托盘一只奔跑的猫。

灵感来自 [RunCat365](https://github.com/runcat-dev/RunCat365)：**猫跑的速度 = 显存占用率**，
占用越高猫跑得越欢。右键猫图标即可查看服务器 GPU 的实时状态。

## 架构

```
GPU 服务器                     你的 Windows 机器
┌──────────────────┐  HTTP   ┌────────────────────────────┐
│ gpu_reporter.py  │  POST   │ gpu_receiver.py (端口 8787) │
│ (systemd 常驻)   │ ──────► │  ├─ gpu_log.csv  (历史记录) │
│ nvidia-smi 采集  │  每分钟  │  └─ gpu_latest.json (最新)  │
└──────────────────┘         └─────────────┬──────────────┘
                                           │ 读文件
                               ┌───────────▼──────────────┐
                               │ gpu_tray.py / gpu-tray.exe│
                               │ 托盘动画猫 + 右键状态菜单 │
                               └──────────────────────────┘
```

## 功能

- 托盘图标动画猫，奔跑速度跟随显存占用百分比；数据离线（>3 分钟未更新）时猫变灰
- 右键菜单显示：主机名、GPU 型号、显存用量/总量、利用率、温度、更新时间
- 按用户聚合的显存占用排行（哪个用户占了多少显存、几个进程），最多显示 5 人
- 任务详情：解析服务器上管理员脚本生成的 `reporter.log`，展示每个任务的
  运行时长和预计剩余时间（如日志提供），最多显示 5 个任务
- ETA 估算：对 reporter 有读取权限的进程（通常是你自己的），自动从
  stdout/stderr 日志解析 tqdm 进度条（直接用其内置剩余时间）或
  `epoch x/N` 字样（按匀速外推），有进度信息时覆盖"预计剩余"字段
- 历史数据自动记录到 CSV，可直接用 pandas 分析
- 服务器侧只读 `nvidia-smi`，零依赖；接收端/托盘端支持 PyInstaller 打包成 exe

## 部署

### 1. 服务器端（被监控的 GPU 服务器）

需要：Python 3 + `nvidia-smi`。

```bash
mkdir -p ~/gpu-reporter
cp gpu_reporter.py ~/gpu-reporter/
# 创建本地配置（含接收端地址和鉴权 token）
cat > ~/gpu-reporter/local_config.py <<'EOF'
TARGET_URL = "http://<你的机器IP>:8787/gpu"
INTERVAL = 60          # 上报间隔（秒）
TOKEN = "change-me"    # 与接收端保持一致
EOF
```

注册为用户级 systemd 服务（无需 sudo）：

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/gpu-reporter.service <<'EOF'
[Unit]
Description=GPU usage reporter
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 %h/gpu-reporter/gpu_reporter.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now gpu-reporter.service
loginctl enable-linger   # 退出登录后也保持运行
```

### 2. 本机接收端（Windows）

在 `local_config.py` 中配置相同的 `TOKEN`，然后：

```bash
python gpu_receiver.py        # 源码方式
# 或使用打包好的 exe：
gpu-receiver.exe
```

### 3. 托盘猫（Windows）

```bash
pip install -r requirements.txt
pythonw gpu_tray.py           # 源码方式
# 或使用打包好的 exe：
gpu-tray.exe
```

## 打包 exe

```powershell
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name gpu-tray --add-data "frames;frames" gpu_tray.py
python -m PyInstaller --onefile --name gpu-receiver gpu_receiver.py
# 产物在 dist/ 下；local_config.py 放在 exe 旁边即可被读取
```

## 注意

- 网络：服务器需要能直连你本机的 8787 端口（内网/Tailscale 等）。HTTP 明文 + token
  鉴权，仅适合可信网络；走公网请自行套 HTTPS 或 VPN。
- 用户显存统计基于 `nvidia-smi --query-compute-apps`（仅 compute 进程）。

## 致谢

- 猫咪动画帧来自 [RunCat365](https://github.com/runcat-dev/RunCat365)，
  遵循 Apache License 2.0 使用（见 [NOTICE](NOTICE)）。
