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

## LED 控制

`main.py` 启动时会先熄灭开机自启流水灯，但不会停止 LED 守护进程，因为守护进程必须继续
独占 GPIO18。随后可在 JSON 中为启动状态和每个屏幕命令配置 `off`、`solid`、`blink` 或
`flow`，亮度范围为 0～20。`flow` 任意时刻只点亮一颗灯，亮点循环移动并沿色环持续渐变。

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
