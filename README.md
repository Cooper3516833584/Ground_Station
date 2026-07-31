# Ground Station

## 可配置任务入口

正式入口是 `main.py`。通常不需要改 Python：编辑根目录 `task_config.json`，用
`active_task` 选择任务，在该任务的 `screen_commands` 中把串口屏字符映射到无人机命令。

支持的无人机命令与现有安全协议保持一致：`PING`、`SET_TARGETS`、
`START_MISSION`、`START_VISION_ACQUIRE`、`STOP_MISSION`。`SET_TARGETS` 还需配置
`target1` 和 `target2`（0～255）。未知屏幕字符不会发送任何内容。

```bash
python3 main.py
python3 main.py --task vision_acquire
python3 main.py --log-raw
```

HMAC 密钥仍从 `GROUND_STATION_HMAC_KEY_HEX` 或
`config/secrets/hmac.key` 读取，不应写进 JSON 或提交到 Git。

## 空地协同测绘救灾入口

`screen_start_bridge.py` 使用一条 FleetBus 半双工链路同时协调无人机和小车。点击串口屏
`START` 后，地面站立即通知无人机吸合电磁铁，白灯满亮度闪烁 3 秒并转为
3/255 白灯常亮；第 15 秒启动小车声光报警，第 20 秒先关闭报警再向无人机
发送任务 START。检测到无人机已解锁且高度达到配置门限后，才发送
`CAR_START_MAPPING`，小车此前保持静止且不会打开雷达建图。无人机测绘期间地面站每
0.5 秒请求一次最新结果，并在同一程序的 3×5 界面中把已识别格更新为 `assets/terrain/`
内对应图片；每格同时使用无人机上报的场地绝对坐标，未识别的 `UNKNOWN` 格会清除旧图并
保持空白。无人机完成测绘和降落后，地面站从 3×5 结果中选择离小车起点最近的
lake/river，依次发送水源、wildfire 和起点三个场地全局厘米坐标，均不附带最终车头角度。
水源和山火两次到点各用满亮度白灯闪烁 3 秒，最后恢复后台流水灯。

场地坐标以左下角为 `(0,0)`，`+X` 向右、`+Y` 向上；无人机起飞区外圆直径为
`75 cm`、内圆直径为 `50 cm`，外圆左缘和下缘距场地边界均为 `75 cm`，因此圆心为
`(112.5,112.5)`；
小车启动后轴中心为 `(160,50)`。比赛角度约定“正上方为 0°、顺时针为正”；无人机初始为
`0°`，小车默认车头沿场地长边向右，即比赛角度 `90°`。地面站仅在
`SET_COORDINATE_FRAME` 边界把它换算为现有导航内部的 `+X=0°/逆时针为正`，
不修改车端导航角度语义。水源、山火和返航目标均不
限制最终车头角度。`screen_start_bridge.py` 是本流程唯一的 HC-14 主站，不得与
`fleet_app.py` 同时运行。

```bash
python3 screen_start_bridge.py
```

## LED 控制

`main.py` 启动时会先熄灭开机自启流水灯，但不会停止 LED 守护进程，因为守护进程必须继续
独占 GPIO18。随后可在 JSON 中为启动状态和每个屏幕命令配置 `off`、`solid`、`blink` 或
`flow`，亮度范围为 0～255。`flow` 任意时刻只点亮一颗灯，亮点循环移动并沿色环持续渐变。

其他 Python 程序也可用一个函数直接控制：

```python
from components.led_control import set_led

set_led(mode="solid", color=(255, 0, 0), brightness=4)
set_led(mode="blink", color=(0, 255, 0), brightness=3, interval_seconds=0.5)
set_led(mode="flow", brightness=2, interval_seconds=0.16)
set_led(mode="off")
```

七颗灯分别设置时使用 `mode="pixels"`，并传入恰好 7 个 RGB 值的 `pixels`。

当前 systemd 服务运行独立安装副本。Git 拉取后先安装并重启，才能使用新的本地控制协议：

```bash
sudo install -m 755 led_daemon.py \
  /home/cooper/.local/share/ground_station_led/ground_station_led_chase.py
sudo systemctl restart ground-station-led.service
```
