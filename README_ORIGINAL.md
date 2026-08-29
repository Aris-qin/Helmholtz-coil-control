# 可控磁场编译项目

PC 控制 + ESP32-S3 + DAC8555 + OPA549 亥姆霍兹线圈控制系统

## 系统架构

```
PC (PySide6 GUI)                ESP32-S3                   硬件
┌──────────────────┐    串口     ┌────────────────┐     ┌──────────┐
│  main.py         │ ◄───────► │  main.cpp       │────►│ DAC8555  │──── SPI ────┐
│  coil_serial.py  │  115200   │                 │     │ (16bit   │              │
│                  │  baud     │  波形生成       │     │  4ch)    │              │
│  UI:             │           │  安全保护       │     └──────────┘              │
│   - 震荡/旋转    │           │  相位控制       │           │                   │
│   - 相位控制     │           │  心跳上报       │           ▼                   │
│   - 磁轨迹图     │           └────────────────┘     ┌──────────┐              │
│   - 急停         │                                   │ OPA549   │─── 线圈 ─────┘
│   - 离线模拟     │                                   │ × 4      │    (X/Y/Z/W)
└──────────────────┘                                   │ 大电流   │
                                                       │ 运放     │
                                                       ├──────────┤
                                                       │ 瞬态     │ ← 反电动势保护
                                                       └──────────┘
```

## 文件结构

```
可控磁场编译项目/
├── firmware/                # ESP32-S3 固件 (PlatformIO)
│   ├── platformio.ini       # 编译配置
│   ├── include/
│   │   └── config.h         # 引脚定义 & 参数配置
│   └── src/
│       └── main.cpp         # 主固件
├── python/                  # PC 端控制软件
│   ├── main.py              # 主程序 (PySide6 GUI)
│   └── coil_serial.py       # 串口通信库
├── docs/
│   └── protocol.md          # 串口协议文档
└── README.md
```

## 快速开始

### 1. 编译 ESP32-S3 固件

```bash
# 安装 PlatformIO
pip install platformio

# 编译
cd firmware
pio run

# 烧录
pio run --target upload

# 监视串口
pio device monitor
```

### 2. 运行 PC 端

```bash
cd python
pip install PySide6 matplotlib numpy pyserial

# 离线模拟模式 (不连硬件)
python main.py --offline

# 自动连接硬件
python main.py

# 指定串口
python main.py --port COM3          # Windows
python main.py --port /dev/ttyACM0  # Linux
python main.py --port /dev/cu.SLAB_USBtoUART  # macOS
```

## 硬件连接

### ESP32-S3 → DAC8555

| ESP32-S3M | DAC8555 | 说明 |
|-----------|---------|------|
| GPIO4     | SYNC    | SPI 片选 |
| GPIO5     | SCLK    | SPI 时钟 |
| GPIO6     | DIN     | SPI 数据 |
| GPIO7     | LDAC    | 输出刷新 (低电平触发) |
| 3.3V      | VDD     | 电源 |
| GND       | GND     | 地 |
| —         | VREF    | 2.5V 外部基准 (需独立参考源) |

### DAC8555 → OPA549 → 线圈

```
DAC8555 通道      OPA549               线圈
AOUTA (Ch0) ───► OPA549#1 ──── 瞬态二极管 ──── X轴
AOUTB (Ch1) ───► OPA549#2 ──── 瞬态二极管 ──── Y轴
AOUTC (Ch2) ───► OPA549#3 ──── 瞬态二极管 ──── Z轴
AOUTD (Ch3) ───► OPA549#4 ──── 瞬态二极管 ──── W(备用)
```

### OPA549 连接

- **V+ / V-**: 双电源供电（根据线圈参数选择电压，建议 ±12V ~ ±28V）
- **Rcl**: 电流限制电阻（通过 10k 电位器可调限流）
- **Rf / Rg**: 增益电阻网络（增益 = 1 + Rf/Rg）
- **输出**: 经瞬态二极管保护后连接线圈

## 标定

首次使用必须进行电流标定：

1. 接上已知阻值的负载电阻（模拟线圈）
2. 在 PC 端使用 `SET_CURRENT:<ch>,<current_A>` 指令
3. 用万用表/示波器测量实际电流
4. 修改 `firmware/include/config.h` 中的 `CALIBRATION_FACTOR`

## 安全注意事项

- **首次上电前确认电流限制**：用 OPA549 的 Rcl 电位器将最大电流调至安全范围
- **不要在带电状态下插拔线圈接头**
- **瞬态二极管必须安装**：线圈是感性负载，断开瞬间会产生高压反电动势
- **DAC8555 VREF 需要独立精密的电压基准**，不要直接从 ESP 的 3.3V 取电
- **串口协议所有操作都经过软启动/软停止**，电流不会突变
