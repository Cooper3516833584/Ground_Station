# Deploy / 安装部署

本目录存放把地面站安装到一台新树莓派/新电脑上所需的一切。所有路径都由
`install.sh` 根据脚本自身位置计算，不再依赖 `/home/cooper/...`。

## 快速开始

```bash
cd Ground_Station
bash deploy/install.sh
```

脚本会依次：

1. 检查 `python3`；
2. 若 `config/station.local.json` 不存在，从
   `config/station.example.json` 复制一份（**不会覆盖**已有文件），并提示你
   修改串口与 GPIO；
3. 检查依赖（树莓派场景使用 `requirements-rpi.txt`），按提示安装；
4. 安装并启用 LED 守护进程的 systemd 单元 `ground-station-led.service`
   （每次都会先征求确认，使用**当前仓库路径**，不再写死作者用户名）；
5. （可选）安装 D 题桌面自启动 `ground-station-land-air.desktop`。

脚本**不会**生成或写入 HMAC 密钥；也不会在未经确认的情况下修改系统配置。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `install.sh` | 一键安装脚本（幂等，可重复执行） |
| `ground-station-led.service` | LED 守护进程 systemd 单元模板，含 `@APP_DIR@`/`@PYTHON@` 占位符，由 install.sh 替换 |
| `ground-station-land-air.desktop.in` | D 题显示程序桌面自启动模板，含 `@APP_DIR@` 占位符 |
| `README.md` | 本说明 |

## 手动安装（不运行 install.sh）

替换占位符后安装 LED 服务：

```bash
APP_DIR="$(pwd)"
PYTHON="$(command -v python3)"
sed -e "s|@APP_DIR@|${APP_DIR}|g" -e "s|@PYTHON@|${PYTHON}|g" \
    deploy/ground-station-led.service | sudo tee /etc/systemd/system/ground-station-led.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ground-station-led.service
```

## HMAC 密钥

LED 服务不需要 HMAC 密钥，但 `main.py` / `fleet_app.py` / `land_air_app.py`
等程序需要。生成一次：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" > config/secrets/hmac.key
chmod 600 config/secrets/hmac.key
```

或每次运行前导出 `GROUND_STATION_HMAC_KEY_HEX`。密钥绝不放进任何 JSON 配置，
也不提交到 Git（见根目录 `.gitignore`）。
