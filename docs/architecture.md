# 软件架构

本文用最简文字说明数据流与职责划分，方便第一次接触项目的同学快速定位。

## 总览

```text
串口屏
  ↓  (字符指令，如 START)
主程序 / 任务协调器          (main.py / land_air_app.py / screen_start_bridge.py)
  ↓  (FleetBus 帧 + HMAC)
GroundLink / FleetBus 主站    (components/ground_link.py, half_duplex_master.py)
  ↓  (BB 33 信封, pyserial)
HC-14 无线串口
  ↓
无人机 / 小车
```

- 串口屏只发送短字符指令；主程序把字符映射为 FleetBus 命令并发送。
- FleetBus 是半双工协议：地面站是主站，轮询/命令都按 `timing` 配置的节奏进行。
- 所有帧都带 HMAC 认证（密钥见 README「安全说明」）。

## LED（WS2812）

```text
主程序 / 其他进程
  ↓  Unix Datagram Socket（GSLED1: JSON 控制指令）
LED daemon (led_daemon.py)
  ↓  (rpi_ws281x)
WS2812 GPIO
```

**为什么 LED daemon 独占 GPIO：**

1. WS2812 是时序敏感的 DMA 驱动设备；如果多个进程同时直接操作同一个
   GPIO/PWM 通道，会互相打断，灯带出现乱闪甚至损坏数据时序。
2. daemon 是常驻进程，负责“开机流水灯”等持久模式；主程序退出后灯带状态
   依然由 daemon 维持（例如任务结束恢复流水灯）。
3. 其它程序只需要向 socket 发送一条短指令（`solid`/`blink`/`flow`/
   `pixels`/`off`），不需要知道 GPIO 细节。

协议保持兼容：文本指令以 `GSLED1:` 开头（JSON），另有旧版二进制
LED_CONTROL payload（`components/models.py` 的 `LEDControl`）。

## 蜂鸣器

```text
GroundCuePlayer (components/ground_cue_player.py)
  ↓  抽象回调 Callable[[float], None]
buzzer_control (components/buzzer_control.py)
  ↓  (RPi.GPIO，延迟导入)
GPIO
```

- `GroundCuePlayer` 不知道任何 GPIO 引脚，只调用“LED client”和“buzzer
  callback”两个抽象；
- 蜂鸣器配置（`hardware.buzzer`）由 `buzzer_control.build_ground_buzzer(station)`
  组装成回调；`enabled: false` 时回调是安全 no-op，且不会导入 `RPi.GPIO`。

## 声光提示（D 题）

```text
任务状态检测 (mission1/2_cue_controller)
  ↓
GroundCuePlayer
  ↓  LED client                ↓  buzzer callback
Unix socket → LED daemon       GPIO 蜂鸣器
```

提示的**颜色/亮度**来自 `d_task_fleet_config.json` 的 `ground_cues`；
**时长/次数**来自 `mission1_cues` / `mission2_cues`（见
`docs/configuration.md`）。

## 配置分层

```text
station.local.json  ← 机器：GPIO、LED 数量、串口、波特率、socket、硬件开关
task/fleet JSON     ← 赛题：时序、场地、坐标、提示、流程策略
Python 代码         ← 协议：帧结构、CommandId、ACK、CRC/HMAC、算法、状态机
```

`components/station_config.py` 是读取机器配置的唯一公共入口；任务配置由
`components/task_config.py` 与各入口的 `load_config` 读取。

## 主要入口

| 入口 | 角色 | 命令 |
| --- | --- | --- |
| `main.py` | 通用屏幕→命令模板 | `python3 main.py [--task ...] [--station-config ...]` |
| `fleet_app.py` | FleetBus 显示/控制 | `python3 fleet_app.py` |
| `land_air_app.py` | D 题正式程序 | `python3 land_air_app.py` |
| `screen_start_bridge.py` | D 题测绘救灾流程 | `python3 screen_start_bridge.py` |
| `led_daemon.py` | LED 独占进程 | `python3 led_daemon.py --station-config ...` |

## 注意

- `screen_start_bridge.py` 是该流程唯一的 HC-14 主站，**不要**与
  `fleet_app.py` 同时运行（避免两个主站互相轮询冲突）。
