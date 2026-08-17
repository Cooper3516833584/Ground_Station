# Ground Station

面向“空地协同测绘救灾”（D 题）的地面站软件。本项目把**比赛任务逻辑**与
**地面站机器配置**分开：换一台地面站时，通常只需要改一个文件
`config/station.local.json`，不需要修改任何 Python 业务代码。

## 项目用途

- 接收串口屏按钮（如 `START`），把屏幕指令映射为经过 HMAC 认证的无人机/小车命令；
- 通过 HC-14 无线串口与无人机、小车组成半双工 FleetBus 链路；
- 任务 1：空地协同取水（无人机电磁铁 + 小车跟随）；
- 任务 2：空地协同测绘救灾（无人机测绘 3×5 场地、识别 wildfire，小车前往
  水源与 wildfire 位置并返航）；
- 地面 LED（WS2812）与蜂鸣器声光提示；
- 实时轨迹显示界面（PyQt5）。

## 软件架构

见 `docs/architecture.md`（数据流、LED 守护进程为什么独占 GPIO）。

## 仓库目录

```text
Ground_Station/
├── main.py                  # 通用“屏幕按键 → 飞机命令”入口
├── fleet_app.py             # FleetBus 地面站界面（任务 1/2 通用）
├── land_air_app.py          # D 题正式程序：空地协同只读显示 + 任务协调
├── screen_start_bridge.py   # D 题正式程序：串口屏 START 触发测绘救灾全流程
├── screen_led_toggle.py     # 调试工具：屏幕 START 切换 LED
├── led_daemon.py            # WS2812 守护进程（唯一操作 LED GPIO 的进程）
├── components/              # 公共组件（协议、状态机、LED/蜂鸣器、串口等）
├── config/
│   ├── station.example.json # 机器配置模板（提交 Git）
│   ├── station.local.json   # 你的机器配置（不提交 Git）
│   └── secrets/             # HMAC 密钥目录（不提交 Git）
├── task_config.json         # 任务 1/2 的“屏幕指令 → 命令”映射
├── fleet_config.json        # FleetBus 显示/时序/场地/测绘策略
├── d_task_fleet_config.json # D 题任务参数（时序、提示、场地、坐标）
├── deploy/                  # 安装脚本与 systemd/desktop 模板
├── docs/                    # architecture.md / configuration.md
├── tests/                   # pytest 测试集
├── requirements.txt          # 通用依赖（pyserial / PyQt5）
├── requirements-rpi.txt      # 树莓派完整依赖（含 RPi.GPIO / rpi-ws281x）
└── README.md
```

## 支持的比赛任务

| 任务 | 入口 | 说明 |
| --- | --- | --- |
| 任务 1 | `main.py` | 屏幕指令 → 认证后的飞机命令（可配置） |
| 任务 1/2 通用显示 | `fleet_app.py` | FleetBus 半双工显示与手动控制 |
| D 题任务 1/2 | `land_air_app.py` | 空地协同只读显示、任务协调、声光提示 |
| D 题测绘救灾 | `screen_start_bridge.py` | 屏幕 `START` → 无人机测绘 → 小车救灾全流程 |

## 硬件要求

- 树莓派（GPIO 驱动 LED 与蜂鸣器）；
- WS2812 LED 灯带（默认 7 颗，`hardware.led.count` 可改）；
- 有源蜂鸣器（默认 BCM 27，`hardware.buzzer` 可改或禁用）；
- 串口屏（USB 转串口）；
- HC-14（或类似）无线串口模块 ×2（无人机、小车）；
- 无人机/小车端需配置与地面站相同的 HMAC 密钥。

## 快速开始

### 1. 克隆仓库

```bash
git clone <your-repo-url> Ground_Station
cd Ground_Station
```

### 2. 安装依赖

**普通开发电脑**（只跑测试/查看代码，无 GPIO 硬件）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Raspberry Pi**（完整运行，含 GPIO/LED 依赖）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-rpi.txt
```

> `RPi.GPIO` 与 `rpi-ws281x` 只在树莓派上需要（见 `requirements-rpi.txt`）；
> 普通电脑上跑测试不需要它们（测试全部使用 fake/mock，见“测试”一节）。
> 桌面自启动脚本会优先使用项目内 `.venv/bin/python`（见
> `tools/land_air_autostart.sh`），因此依赖装进 `.venv` 即可被自动启动找到。

### 3. 创建 station.local.json

```bash
cp config/station.example.json config/station.local.json
nano config/station.local.json
```

`station.local.json` 是**换一台地面站必须修改的唯一的机器配置文件**。
至少修改：

- `serial.screen.port` —— 串口屏设备；
- `serial.fleet_radio.port` —— HC-14 设备；
- `hardware.led.pin` / `hardware.led.count` —— LED GPIO 与数量；
- `hardware.buzzer.pin` —— 蜂鸣器 GPIO（没有蜂鸣器就设 `"enabled": false`）。

### 4. 查找 USB 串口设备

```bash
ls -l /dev/serial/by-id/
```

把输出中的设备名填入 `station.local.json`（不要假设其他机器有相同的
VID/PID/serial，`station.example.json` 中的 `CHANGE_ME_*` 只是占位符）。

### 5. 配置 LED / 蜂鸣器 GPIO

GPIO 使用 BCM 编号。默认 LED 引脚 18、蜂鸣器引脚 27。按你的接线修改
`station.local.json` 即可，无需改代码。

### 6. 配置 HMAC key

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" > config/secrets/hmac.key
chmod 600 config/secrets/hmac.key
```

或运行前导出 `GROUND_STATION_HMAC_KEY_HEX`。密钥**绝不**写入任何 JSON
配置文件，也不提交到 Git（见 `.gitignore`）。

### 7. 启动程序

```bash
python3 main.py                      # 任务 1 通用入口
python3 main.py --task vision_acquire
python3 main.py --log-raw
python3 land_air_app.py              # D 题正式程序
python3 screen_start_bridge.py       # D 题测绘救灾流程
```

所有入口都支持：

```bash
--station-config config/station.local.json   # 指定机器配置（可选）
```

## LED daemon

`led_daemon.py` 是**唯一**操作 WS2812 GPIO 的进程，通过 Unix Datagram Socket
（默认 `/run/ground-station-led.sock`，可在 `hardware.led.socket_path` 修改）
接收控制指令：

```text
GSLED1:{"mode":"solid","color":[255,0,0],"brightness":4,"interval_seconds":0.5}
```

支持 `off` / `solid` / `blink` / `flow` / `pixels` 五种模式。安装为系统服务：

```bash
bash deploy/install.sh     # 交互式安装，自动使用当前仓库路径
```

或手动：

```bash
python3 led_daemon.py --station-config config/station.local.json
```

## D 题运行方式

- 正式程序：`python3 land_air_app.py`
- 测绘救灾：`python3 screen_start_bridge.py`（屏幕 `START` 触发）
- 两者都通过 `--config`（任务配置）与 `--station-config`（机器配置）区分
  两类配置，含义不混用。

任务流程（`screen_start_bridge.py`）：

```text
START → 无人机准备 → LED 指示 → 小车报警 → 无人机任务开始
→ 检测无人机起飞 → 启动小车建图 → 请求 survey → 获取水源 → wildfire → 返回
```

## 配置文件说明

| 文件 | 内容 | 换机器时要改吗 |
| --- | --- | --- |
| `config/station.local.json` | GPIO、LED 数量、串口设备、波特率、socket、硬件开关 | ✅ 唯一必须改 |
| `task_config.json` | 屏幕指令 → 飞机命令映射、启动 LED 模式、冷却时间 | 按赛题策略 |
| `fleet_config.json` | 时序、UI、场地、坐标、测绘救灾策略 | 按赛题策略 |
| `d_task_fleet_config.json` | D 题时序、提示（`ground_cues`）、场地、坐标 | 按赛题策略 |

每个参数的详细说明见 `docs/configuration.md`。

## 常见故障

- **`station configuration not found: config/station.local.json`**
  先执行 `cp config/station.example.json config/station.local.json` 再修改。
- **`Missing HMAC key`**
  生成 `config/secrets/hmac.key` 或导出 `GROUND_STATION_HMAC_KEY_HEX`。
- **串口打不开 / 设备不存在**
  检查 `station.local.json` 中的端口是否与本机 `/dev/serial/by-id/` 一致。
- **LED 不动**
  确认 `led_daemon.py` 正在运行（`systemctl status ground-station-led`），
  且 socket 路径与 `hardware.led.socket_path` 一致。
- **蜂鸣器不响**
  确认 `hardware.buzzer.enabled` 为 `true` 且引脚正确；若 `active_high`
  与实际电路不符，反转该值。
- **无 PyQt5 界面**
  在树莓派上 `pip install PyQt5`（或系统包 `python3-pyqt5`）。

## 测试

```bash
pytest -q
```

测试不需要真实 GPIO/串口硬件：`RPi.GPIO`、`rpi_ws281x` 全部延迟导入，
测试使用 fake/mock。提交前请执行 `pytest -q`，以实际输出为准。

## 安全说明

- HMAC 密钥只从 `GROUND_STATION_HMAC_KEY_HEX` 或 `config/secrets/hmac.key`
  读取，禁止写入 JSON 或提交 Git；
- `config/station.local.json`、`config/*.local.json`、`config/secrets/*`
  已被 `.gitignore` 排除；
- 部署脚本 `deploy/install.sh` 不会静默修改系统配置，每一步都会先询问。

## License

License 尚待仓库所有者选择（建议 MIT / Apache-2.0 / GPL-3.0 之一）。
在所有者明确授权之前，本仓库不自动附加任何 License。
