# Ground Station Components

本分支已清除旧比赛任务入口、任务配置、归档代码和旧测试，保留 `components/` 作为新比赛主程序的组件基础。

当前工作从组件层开始；新比赛的主程序、任务流程和配置将在此基础上重新设计。

已新增地面站串口屏开始/终止接口及开机自启动安装器。按钮 `run`/`STOP` 通过现有 FleetBus 只发送给小车；车机主程序读任务板、任务下发、落地与双方重启待命仍待接入。详见 [比赛遥控接口与部署说明](docs/COMPETITION_REMOTE.md)。

## 保留内容

- `components/`：通信协议、地面链路、状态/数据存储、硬件控制和 UI 组件
- `config/station.example.json`：地面站硬件与串口配置示例
- `requirements.txt`、`requirements-rpi.txt`：运行依赖
- `assets/`：组件使用的静态资源
