# 串口通信协议 v1.0

ESP32-S3 ↔ PC 上位机，115200 baud, 8N1, ASCII

## 消息格式

### 请求 (PC → ESP)

```
CMD:<参数>\n
```

### 响应 (ESP → PC)

成功:
```
OK:<消息>\n
```

失败:
```
ERR:<错误描述>\n
```

心跳:
```
OK:Status:running | t=3.000s\n    (每5秒上报，运行时)
```

## 指令列表

### 震荡磁场

**启动震荡**
```
VIBE:X,100.00,0.50
VIBE:Y,200.00,1.00
VIBE:Z,50.00,0.30
```
- 参数: `<轴>,<频率Hz>,<电流A>`
- 注意: 新 VIBE 命令会自动停止之前正在运行的任何模式

**停止震荡**
```
VIBE_STOP
```

### 旋转磁场

**设置相位 (2D 平面)**
```
SET_MODE:2D:X-Y,90.0
SET_MODE:2D:X-Z,45.0
SET_MODE:2D:Y-Z,180.0
```
- 平面: `X-Y`, `X-Z`, `Y-Z`
- 角度: `-360 ~ 360`，90°=正圆，0°/180°=直线

**设置相位 (3D 直角坐标)**
```
SET_PHASE:0.0,90.0,45.0
```
- 参数: `<X相位>,<Y相位>,<Z相位>` (度数)
- 任意轴传 `-1` 表示关闭该轴

**启动旋转**
```
ROTATE:100.00,0.50
```
- 参数: `<频率Hz>,<电流A>`
- 必须先通过 `SET_MODE:2D` 或 `SET_PHASE` 确认方向

**停止旋转**
```
ROTATE_STOP
```

### 系统

**查询状态**
```
STATUS
```
响应示例:
```
OK:Mode:ROTATE|Running:YES|Freq:100.00Hz|Current:0.50A|PhaseX:0.0|PhaseY:90.0|PhaseZ:-1.0|OutX:16384|OutY:24576|OutZ:0|OutW:0
```

**单通道测试**
```
SET_CURRENT:0,0.50
```
- 参数: `<通道号(0-3)>,<电流A>`
- 通道映射: 0=X, 1=Y, 2=Z, 3=W

**紧急停止**
```
ALL_STOP
```

## Python 示例

```python
from coil_serial import CoilSerialController

hw = CoilSerialController()
hw.connect("/dev/ttyACM0")

# 震荡模式
print(hw.start_vibrate("X", 100, 0.5))
hw.stop_vibrate()

# 旋转模式
hw.set_phase_2d("X-Y", 90)
hw.start_rotate(100, 0.5)
hw.stop_rotate()

# 紧急停止
hw.emergency_stop()

hw.disconnect()
```

## 安全机制

所有 STOP 指令都会触发**软停止**（100步渐减），防止电流突变导致线圈震荡。
`ALL_STOP` 立即归零，仅用于紧急情况。
