# 配置说明

地面站配置分两层，**职责严格分开**：

1. **机器配置** `config/station.local.json`（不提交 Git，模板为
   `config/station.example.json`）——描述“在哪台机器上运行”：GPIO、LED 数量、
   串口设备、波特率、socket 路径、硬件开关。
2. **任务配置** `task_config.json` / `fleet_config.json` /
   `d_task_fleet_config.json`（提交 Git）——描述“做什么”：任务规则、时序、
   场地、坐标、提示颜色、流程策略。

判断一个值放哪里的规则：

- **换学校/换电脑/换树莓派可能变化** → `station.local.json`（GPIO、LED 数量、
  串口设备、波特率、socket 路径、硬件 enable/disable）；
- **同一道赛题中可能调整的策略** → 任务配置（任务 timeout、起飞高度阈值、
  场地尺寸、坐标原点、提示颜色、survey 间隔）；
- **改了会改变程序/协议定义** → 继续留在 Python（FleetBus 帧结构、
  CommandId、ACK、CRC/HMAC、坐标转换算法、状态机、协议 payload）。

---

## 1. `config/station.local.json`（机器配置）

加载顺序（`components/station_config.py` 的 `load_station_settings`）：

1. CLI `--station-config <path>`；
2. 环境变量 `GROUND_STATION_CONFIG`；
3. `config/station.local.json`；
4. 都不存在 → 明确报错并提示
   `cp config/station.example.json config/station.local.json`。

### `hardware.led`

| 参数 | 说明 | 换学校后通常要改？ |
| --- | --- | --- |
| `enabled` | LED 是否启用。`false` 时守护进程直接退出，不占用 GPIO | 可能 |
| `pin` | WS2812 数据脚（BCM 编号） | ✅ |
| `count` | WS2812 LED 数量（`pixels` 模式必须与之一致） | ✅ |
| `frequency_hz` | WS2812 时钟频率（默认 800000） | 否 |
| `dma` | rpi_ws281x DMA 通道（默认 10） | 否 |
| `channel` | PWM 通道（默认 0） | 否 |
| `invert` | 信号是否反相（默认 false） | 可能 |
| `strip_type` | 灯带类型，如 `WS2811_STRIP_GRB` | 可能（GRB/RGB 顺序不同） |
| `default_brightness` | 守护进程默认/空闲亮度（0～255，默认 3） | 可能 |
| `max_brightness` | 任务提示允许的最大亮度（0～255，默认 20） | 可能 |
| `socket_path` | 控制 socket 路径 | 可能（多实例） |
| `override_timeout_seconds` | 二进制 `pixels` 指令的超时（默认 30.0） | 否 |
| `flow_interval_seconds` | 流水灯帧间隔（默认 0.16） | 可能 |
| `flow_color_step` | 流水灯色环步进（默认 3） | 否 |

### `hardware.buzzer`

| 参数 | 说明 | 换学校后通常要改？ |
| --- | --- | --- |
| `enabled` | `false` 时蜂鸣器成为安全 no-op，不导入 `RPi.GPIO` | ✅（无蜂鸣器时） |
| `pin` | 蜂鸣器 GPIO（BCM 编号，默认 27） | ✅ |
| `numbering` | 编号体系，当前支持 `BCM` | 否 |
| `active_high` | `true`：高电平响、低电平静；`false` 反之 | ✅（看电路） |
| `default_duration_seconds` | 默认提示时长（默认 0.2） | 可能 |

### `serial.screen`（串口屏）

| 参数 | 说明 | 换学校后通常要改？ |
| --- | --- | --- |
| `port` | 串口屏设备路径（`/dev/serial/by-id/...`） | ✅ |
| `baudrate` | 波特率（默认 9600） | ✅ |
| `read_timeout_seconds` | 读超时（默认 0.05） | 否 |

### `serial.fleet_radio`（HC-14 无线串口）

| 参数 | 说明 | 换学校后通常要改？ |
| --- | --- | --- |
| `port` | HC-14 设备路径（`/dev/serial/by-id/...`） | ✅ |
| `baudrate` | 波特率（默认 115200） | ✅ |
| `read_timeout_seconds` | 读超时（默认 0.1） | 否 |
| `write_timeout_seconds` | 写超时（默认 0.5） | 否 |
| `reconnect_seconds` | 断开后重连间隔（默认 1.0） | 否 |

### 校验规则

所有字段在**启动阶段**校验，错误信息带 JSON 路径，例如：

```text
hardware.led.pin must be a non-negative integer
```

- GPIO pin ≥ 0；LED count ≥ 1；brightness 0～255；
- baudrate ≥ 1；timeout > 0；socket path 非空；
- LED frequency ≥ 1；DMA ≥ 0；channel ≥ 0；
- `numbering` 当前至少支持 `BCM`；
- `strip_type` 必须是 rpi_ws281x 支持的类型名。

---

## 2. `task_config.json`（通用屏幕指令任务）

只含任务规则，不含机器串口：

| 字段 | 说明 |
| --- | --- |
| `active_task` | 当前激活任务名 |
| `cooldown_seconds` | 同一屏幕指令的最短触发间隔（0.75） |
| `tasks.<name>.startup_led` | 任务启动时的 LED 模式 |
| `tasks.<name>.screen_commands` | 屏幕字符 → `aircraft_command`（+ 可选 `led`）映射 |

`aircraft_command.name` 必须是 `CommandId` 之一（`PING`、`SET_TARGETS`、
`START_MISSION`、`START_VISION_ACQUIRE`、`STOP_MISSION`）。

---

## 3. `fleet_config.json`（FleetBus 显示 + 测绘救灾策略）

| section | 说明 |
| --- | --- |
| `timing` | 半双工时序（节点周转、响应超时、重试等） |
| `ui` | 轨迹采样、刷新间隔、地形图片目录 |
| `field` / `coordinate_frames` / `display_geometry` | 场地与显示几何 |
| `disaster_survey` | 测绘救灾策略：起点/救援点、起飞高度阈值、各阶段 timeout、`screen_start_token`、`fallback_survey_grid`（survey 格中心坐标）、`ground_indicator`（白灯亮度/闪烁间隔） |
| `logging` | 轨迹 CSV 导出 |

`fallback_survey_grid` 与 `ground_indicator` 默认值与旧代码一致：

```json
"screen_start_token": "START",
"fallback_survey_grid": {
  "x_centres_cm": [115, 185, 255, 325, 395],
  "y_centres_cm": [175, 245, 315]
},
"ground_indicator": {
  "full_white_brightness": 20,
  "dim_white_brightness": 3,
  "blink_interval_seconds": 0.25
}
```

> 算法（`nearest_water_global`、`survey_cell_to_global`、
> `field_heading_to_math_ccw`、`drone_is_airborne`）**不迁移到 JSON**，继续留在
> `screen_start_bridge.py` 代码中。

---

## 4. `d_task_fleet_config.json`（D 题任务配置）

| section | 说明 |
| --- | --- |
| `timing` / `trace_sync` | 链路时序与轨迹同步 |
| `ui` | 轨迹策略 |
| `ground_cues` | 地面声光提示的**颜色与亮度**（时长/次数见下方说明） |
| `mission1_coordination` | 任务 1 启动协调 |
| `mission1_cues` / `mission2_cues` | 任务 1/2 提示的**次数与时长** |
| `field` / `coordinate_frames` / `display_geometry` | 场地与坐标 |
| `logging` | 轨迹 CSV 导出 |

### `ground_cues`

只保存“表现层”参数中不与 `mission1_cues`/`mission2_cues` 重复的部分
（避免同一参数存两份）：

```json
"ground_cues": {
  "brightness": 20,
  "start_notice": { "color": [255, 0, 0], "count": 3 },
  "escort_acquired": { "color": [255, 255, 255], "count": 3 },
  "mission1_drop": { "color": [255, 0, 0] },
  "mission_completed": { "color": [0, 255, 0] },
  "mission2_target_locked": { "color": [0, 255, 0] },
  "mission2_retakeoff": { "color": [0, 255, 0] }
}
```

- `brightness` 为提示亮度（0～255，默认 20）；
- 各 cue 的 `color` 为 RGB；`count` 为脉冲次数（仅 start/escort 有，默认 3）；
- 时长仍由 `mission1_cues`/`mission2_cues` 的 `*_seconds` 提供，与改造前行为一致。

---

## 5. 哪些值**不**进 JSON（留在 Python）

- FleetBus 帧结构、magic/header、`MessageKind`、`CommandId`、`AckStatus`、
  `NodeId`、`TerrainCode`、payload 编解码、CRC/HMAC 算法；
- 坐标转换 / 场地方向转换 / 最近水源选择算法；
- 任务状态机、流程顺序、安全判断；
- 禁止出现类似 `{"algorithm": "nearest_water"}` 的无意义抽象。
