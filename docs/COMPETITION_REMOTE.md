# 比赛串口屏遥控接口

地面站只向小车发送开始/终止，小车是决策中心，无人机只接收小车的任务指令。
入口为 `python3 -B -m components.competition_remote`。复用现有 FleetBus V1、CRC、BB33 无线桥封装和串口传输，不使用旧比赛主程序或后台轮询。

## 已实测的按钮数据

2026-10-11 通过 SSH 只读记录串口屏，用户多次手动点按：

| 按钮 | 屏幕输出 | 发送给小车的 FleetBus 命令 |
| --- | --- | --- |
| 开始 | `run\n`，十六进制 `72 75 6e 0a` | `CAR_START_MISSION`，`0x16` |
| 终止 | `STOP\n`，十六进制 `53 54 4f 50 0a` | `TARGETED_STOP`，`0x02` |

完整 CR/LF 行才识别，大小写不敏感，未知消息和子串不触发。地面站源地址 `0x01`，唯一目标地址为小车 `0x20`，命令 body 为空。启动程序不会自动发开始，屏幕重连会丢弃旧缓存和半行数据。

每次输入都写日志。同类命令等待确认时合并；终止取消待发开始、打断开始的确认等待，随后优先发送。新的终止点按也取消排在终止后面的开始。单次请求等待 6 秒，最多重传 3 次，使用同一 session/seq 和原帧，让小车按请求标识去重。只认小车发来的、session/seq/command_id 匹配的 ACK：`RECEIVED` 继续等待，`ACCEPTED`/`COMPLETED` 确認成功，`REJECTED`/`FAILED` 记录失败。终止未获成功确认时丢弃排队的开始。

ACK 的 `ACCEPTED` 只代表接收处理，不表示任务执行完毕。终止不能当成切断电机/锁桨命令。

## 小车与无人机主程序待实现的契约

1. 小车接受开始后启动比赛流程，先读任务板；读完才向无人机发具体任务内容，确认准备完成后再启动无人机。
2. 小车接受终止后停止自身任务并向无人机转发。无人机在空中则原地降落，确认落地后重启任务程序到待命；已在地面则直接重启待命。小车端任务程序也重启待命。
3. 车机两端区分任务进程与通信接收进程，重启任务后仍需能接收后续开始。同 session/seq 重传不得重复启动或重复重启。
4. `CAR_START_MISSION`/`TARGETED_STOP` 是现有通信命令号。现有通用 stop handler 不等于上述比赛终止流程；必须在新小车任务入口实现转发与双方重启，并在机端实现落地确认。当前本提交仅完成地面站，不启动旧车机任务入口。

## 设备配置与自启动

设备仓库只通过正确分支的 `git pull --ff-only` 更新。先确认设备工作树干净、分支为 `ground_station_for_robocup`，更新到 PC 已测试并推送的提交。

目前树莓派仓库路径 `/home/cooper/Desktop/ground_station_for_robocup`，串口屏为 `/dev/serial/by-id/usb-jixin.pro_CMSIS-DAP_LU_LU_2022_8888-if00`（9600），HC-14 为 `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`（115200）。安装前确认这两路串口没有其他程序占用。

```bash
cd /home/cooper/Desktop/ground_station_for_robocup
sudo python3 -B tools/install_competition_remote.py \
  --screen-port /dev/serial/by-id/usb-jixin.pro_CMSIS-DAP_LU_LU_2022_8888-if00 \
  --radio-port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
```

安装器只写系统目录：配置位于 `/etc/robocup-ground-station/`，service 位于 `/etc/systemd/system/competition-remote.service`；已有系统配置先备份到 `/var/backups/robocup-ground-station/`。保留已有按钮/重试配置，更新已确认的串口映射，启用服务随系统启动。服务以 `cooper` 运行，只打开屏幕和无线串口，不初始化 LED/蜂鸣器。

```bash
systemctl is-enabled competition-remote.service
systemctl status competition-remote.service
journalctl -u competition-remote.service -f
```

日志包含 `screen_rx`、`screen_button`、`command_sent`、`command_acknowledged` 或 `command_timeout`。未运行小车接收端时超时不代表按钮识别失败。

要重新只记录按钮，先停止服务释放串口，然后运行：

```bash
sudo systemctl stop competition-remote.service
python3 -B -m components.competition_remote \
  --station-config /etc/robocup-ground-station/station.json \
  --remote-config /etc/robocup-ground-station/competition_remote.json --observe-screen
# 结束记录后恢复服务
sudo systemctl start competition-remote.service
```

硬件无关检查：`python3 -m unittest discover -s tests -p 'test_*.py'`。
