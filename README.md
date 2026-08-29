# Magnetic Coil Control（磁性脂质体 + 可控磁场平台）

> 状态: 进行中（软件完成，硬件未完成）

## 基本信息
- **项目ID**: magnetic-coil-control
- **全称**: Controllable Magnetic Coil System for Magnetically-Actuated Liposomal Drug Delivery
- **类型**: 硬件/嵌入式 + 桌面 GUI 上位机
- **语言**: Python + C
- **创建时间**: 2026-05-14
- **最后活跃**: 2026-05-20（软件封板）
- **项目根目录**: `/workspace/projects/magnetic-coil-control/`

## 核心科学问题
开发一个**磁场可控的药物递送验证平台**：以脂质体作为载体（脂质双层内嵌 Fe3O4 磁性纳米颗粒），通过 ESP32 驱动的三轴亥姆霍兹线圈产生**震荡磁场（OMF）**和**旋转磁场（RMF）**，控制磁性脂质体的运动/释药/产热行为。

## 系统架构
```
┌─────────────────────────────────────────────────┐
│  PC 上位机 (PySide6 GUI)                         │
│  - main.py (854 行)                              │
│  - matplotlib 实时波形显示                        │
│  - 串口通讯（coil_serial.py）                    │
└────────────────┬────────────────────────────────┘
                 │ UART 115200 8N1 ASCII
                 ↓
┌─────────────────────────────────────────────────┐
│  ESP32-S3 嵌入式控制器                           │
│  - coil/main/main.c (809 行)                     │
│  - ESP-IDF v4.0.3                                │
│  - 4 通道 DAC 输出（X/Y/Z/W 轴）                 │
│  - 震荡/旋转波形生成                             │
└────────────────┬────────────────────────────────┘
                 │ 模拟信号
                 ↓
┌─────────────────────────────────────────────────┐
│  亥姆霍兹线圈（三轴正交）                        │
│  - 4 通道功率放大                                │
│  - 震荡/旋转磁场输出                             │
└─────────────────────────────────────────────────┘
```

## 支持的磁场模式
- **震荡磁场 (VIBE)**: 单轴正弦震荡 `VIBE:<轴>,<频率Hz>,<电流A>`
- **旋转磁场 (ROTATE)**: 2D 平面或 3D 矢量旋转 `ROTATE:<频率Hz>,<电流A>`
- **2D 平面**: X-Y、X-Z、Y-Z，相位差控制旋转形态（90°=正圆，0°/180°=直线）
- **3D 矢量**: 任意相位组合 `SET_PHASE:<X>,<Y>,<Z>`
- **安全机制**: 软停止（100 步渐减）+ ALL_STOP 硬停止

## 协议
完整协议见 `docs/protocol.md`：
- 命令格式: `CMD:<参数>\n`
- 响应: `OK:<msg>\n` 或 `ERR:<error>\n`
- 心跳: 5 秒上报 `OK:Status:running | t=X.XXXs`

## 文件结构
```
magnetic-coil-control/
├── README.md                    ← 本文档（项目定义）
├── progress.md                  ← 项目进度时间线
├── decisions.md                 ← 决策记录
├── issues.md                    ← 未解决问题与教训
│
├── main.py                      ← PySide6 GUI 上位机（854 行）
├── README_ORIGINAL.md           ← 原始项目说明（5-18 写）
│
├── python/                      ← Python 端控制代码
│   ├── main.py                  ← Python 控制端（注意重名）
│   ├── main_original.py         ← 早期版本
│   ├── main-PC.py               ← PC 端控制
│   └── coil_serial.py           ← 串口通讯库
│
├── coil/                        ← ESP32-S3 固件（ESP-IDF）
│   ├── CMakeLists.txt
│   ├── sdkconfig                ← 编译配置（90KB）
│   └── main/
│       └── main.c               ← 主控源码（809 行）
│
├── docs/
│   ├── protocol.md              ← 串口通信协议
│   └── literature-survey/       ← 2026-07-27 文献调研档案
│       ├── README.md
│       ├── pubmed_2025-07-27.json
│       ├── corpus_existing_2025-07-27.md
│       └── github_resources_2025-07-27.md
│
└── _archive/                    ← 归档（旧版本）
```

## 当前状态
- ✅ 软件层完成（ESP32 固件 + PySide6 GUI + Python 串口库）
- ❌ 硬件层未完成（线圈/功率放大/DAC 电路待做）
- ✅ 通讯协议 v1.0 文档化
- ✅ 文献调研完成（10 PubMed + 6 corpus + 12+ GitHub）

## 重新编译 ESP32 固件
```bash
cd projects/magnetic-coil-control/coil
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

## 信息边界规则
| 文件 | 唯一职责 | 谁写 |
|------|----------|------|
| README.md | 项目定义、架构、运行方法 | 一次写完，后续只更新技术细节 |
| progress.md | 时间线进展 | 持续追加，最新放最前 |
| decisions.md | 已确认决策（不可删改） | 决策时写 |
| issues.md | 未解决问题、教训 | 发现问题时写 |
| docs/literature-survey/ | 文献调研归档 | 调研时写 |

## 未完成任务
见 `progress.md` 的 "Next" 区段。
