# Ground Station Components

本分支已清除旧比赛任务入口、任务配置、归档代码和旧测试，保留 `components/` 作为新比赛主程序的组件基础。

当前工作从组件层开始；新比赛的主程序、任务流程和配置将在此基础上重新设计。

## 保留内容

- `components/`：通信协议、地面链路、状态/数据存储、硬件控制和 UI 组件
- `config/station.example.json`：地面站硬件与串口配置示例
- `requirements.txt`、`requirements-rpi.txt`：运行依赖
- `assets/`：组件使用的静态资源
