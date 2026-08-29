# Issues

> 专门放：未解决问题、根因假设、临时绕过方案、下一步验证

## 2026-08-27：DAC8555 SPI 帧通道寻址位错位（致命，模拟输出级）

- **问题：** `coil/main/main.c:308` 构造 24-bit 帧首字节 `buf[ch][0] = ((ch & 0x03) << 4)`，把通道号放到了 bit5/bit4
- **证据：** DAC8555 数据手册（TI SLAS475）Fig.47/Table 2，24-bit 帧 MSB-first 控制位为 `DB23 DB22=00 | DB21 DB20=LD1 LD0 | DB19=X | DB18 DB17=Sel1 Sel0 | DB16=PD0 | DB15..DB0=data`；通道选择在 **bit2/bit1（Sel1/Sel0：00=A 01=B 10=C 11=D）**，bit5/bit4 是 LD1/LD0 加载模式位。位级解码：固件 ch=0→0x00、ch=1→0x10、ch=2→0x20、ch=3→0x30，**Sel1/Sel0 恒为 00 → 4 帧全部寻址通道 A**，ch 值反被当成加载模式
- **影响：** 4 帧数据全写进 buffer A（被 X→Y→Z 依次覆盖），buffer B/C/D 从未写入；LDAC 脉冲后仅 VOUTA 更新且值=Z 通道数据，**VOUTB/C/D（Y/Z/W 轴功放）永远零电平**。旋转磁场（需 X/Y 相位差正弦）物理上不可能；切 Y/Z 轴震荡也无输出
- **修法：** `buf[ch][0] = ((ch & 0x03) << 1);`（ch=0→0x00/A, 1→0x02/B, 2→0x04/C, 3→0x06/D，LD=00 只写 buffer），4 帧写满后由现有 LDAC 脉冲（main.c:322-324）上升沿同步加载（手册 Example 1 + 硬件 LDAC 标准用法）
- **状态：** 待改（数据手册铁证，可先改）

## 2026-08-27：DAC8555 SPI 模式错误 + 三个控制脚悬空

- **问题 1（SPI mode）：** 固件 `main.c:282` 用 `.mode = 0`（CPHA=0）；手册 pin11 规定数据在 **SCLK 下降沿锁存**，68HC11 接口示例明确要求 **CPOL=0, CPHA=1 = SPI mode 1**。mode 0 下主机下降沿更新 MOSI、从机同沿采样，建立时间不满足，20MHz 下采到的 bit 不可靠。应改 `.mode = 1`（20MHz 时钟本身合规：IOVDD=3.3V 时 t1≥40ns）
- **问题 2（三脚悬空，已与 Q 确认实物现状）：** ENABLE(pin15)/RSTSEL(pin14)/RST(pin13) **目前全悬空**。CMOS/Schmitt 输入悬空电平随机
  - ENABLE 必须 **接 GND**（active-low，手册原话 normal operation 须 tie low；拉高/悬空则 DAC 停止监听 SPI）
  - RSTSEL 必须 **接 GND**（=binary 编码+上电零电平；接高=2's complement，与固件 unipolar 正弦表冲突会半量程翻转）
  - RST 应 **10kΩ 上拉到 IOVDD**（active-low 复位，正常工作须为高），或接 MCU GPIO 控制
- **状态：** 接线待 Q 实物处理；SPI mode 待改

## 2026-08-27：双极性（方向反转）电流输出方案未定型 —— VREFH 未接

- **背景：** Q 需先弄明白"DAC 如何输出周期性方向相反的电流"再接 VREFH
- **关键原理（手册 Fig.55 Bipolar Operation）：** DAC8555 是**电压输出、单极性**器件，输出恒为 0~VREFH（VREFL=GND），**自身不能输出负电压/反向电流**；方向反转靠后级。信号链：`DAC(0~VREF, code32768=VREF/2=零电流点)` → 双极性电平搬移级（运放减法 VOUT=VREF×(D/32768−1)，映射成 −VREF~+VREF）→ OPA549 功率级(±24V) → 线圈(0.5Ω)，电流方向随 ±V 反转
- **待 Q 定：** 后级方案 (a) 手册 Fig.55 运放减法搬移 + OPA549 功率级，还是 (b) OPA549 一级同时完成搬移+放大
- **VREFH 接法：** 精密基准 2.5V（REF5025/REF02），VREFL=GND，AVDD/IOVDD=3.3V 或 5V；基准只定零点/比例，不提供功率
- **关联固件 BUG-C（波形偏置）：** 波形合成 `out=(sin_table[idx]*current_code)>>16`（main.c:449/459）是 unipolar 缩放，限幅时输出中点落在 current_code/2 而非 32768 → 双极性电路下产生负直流偏置（如 current_code=16384 半幅时中点=8192）。双极性正确式：`out = 32768 + (sin_table[idx]-32768)*amp_code/32768`；`current_to_code()`(main.c:186) 也要改为以中点为零点。**待后级方案定后一起改**（换算系数依赖 OPA549 增益）

## 2026-08-27：±24V/0.5Ω 功率级限流与增益标定

- **问题：** 双极性 ±24V 双电源供 OPA549；线圈直流电阻约 **0.5Ω**。若不限流，±22V 输出理论电流达 ~44A，远超线圈/功放承受
- **待办：** OPA549 ILIM 引脚接电阻设硬件限流（建议先封 5~6A），比软件保护可靠
- **标定缺口：** OPA549 反馈电阻（增益 G）**未知** → 电流无法标定。链路 `I_coil = V_out_OPA/0.5Ω`，`V_out_OPA=(V_DAC−VREF/2)×G`。建议先小信号（code 围绕 32768 小摆幅）实测 V_out~code 关系反推 G，再写 `current_to_code`；现有 `OPA549_GAIN=3.0`(main.c:78) 是开环估算
- **注：** 固件 `DAC_VREF=2.5V` 假设需与实际基准核对；电源域须隔离——24V 只供 OPA549，DAC AVDD/IOVDD/VREF 为 3.3/5/2.5V 独立域

## 2026-08-27：固件形态 ESP-IDF vs 实际 Arduino 环境（ATK-MVS3S）

- **问题：** 现有 `coil/main/main.c` 是 **ESP-IDF** 工程（app_main + driver/spi_master.h + idf.py build）；Q 实际用 **Arduino 版 ATK-MVS3S 模组**（ESP32-S3，8MB PSRAM / 16MB Flash）
- **待 Q 定：** Arduino IDE 还是 PlatformIO；据此改为 Arduino 结构（SPI.h + Serial1，setup/loop）或保留 IDF API 混编
- **已确认无冲突：** SYNC/SCLK/DIN/LDAC = GPIO4/5/6/7（Q 核实）；ESP32-S3 octal PSRAM/flash 占 GPIO26–32，GPIO4–7 不冲突

## 2026-08-27：通信协议 BUG — PC 发 SET_MODE:2D 固件未实现（2D 旋转必失败）

- **问题：** `coil_serial.py:137` 发 `SET_MODE:2D:X-Y,90.0`；固件 `process_cmd()`(main.c:481-715) **无 SET_MODE 分支**，落到底 `send_err("Unknown command")`(main.c:714)
- **影响：** `_direction_confirmed` 永远 False → `start_rotate()` 返回 `ERR:请先确认旋转方向`(coil_serial.py:153)，**2D 旋转磁场硬件模式必然启动不了**
- **旁证：** 旧版 `main-PC.py:144-156` 走 `SET_PHASE` + OFF token，此路径固件支持——固件只认 SET_PHASE，新版 PC 库改用了固件不认识的 SET_MODE:2D
- **修法（二选一）：** 固件补 SET_MODE:2D 解析；或 PC 端 set_phase_2d 改发 SET_PHASE（对齐 main-PC.py 的 OFF token）

## 2026-08-27：通信协议 BUG — STATUS 回复格式三方不一致（状态回读永久失效）

- **问题：** 固件实发(main.c:689-701) `OK:Mode=VIBRATE|Run=YES|Freq=100.00|Curr=0.50|...|PhX=..|PhZ=OFF`（`=`分隔、键名 Run/Curr/PhX、无单位）；PC 解析器 `_parse_status`(coil_serial.py:200-223) 期望 `Mode:...|Running:YES|Freq:..Hz|Current:..A|PhaseX:..`（`:`分隔、键名 running/current/phasex、带 Hz/A/° 后缀）；protocol.md:86 文档又是**第三种**格式
- **机制：** 分隔符 `=` vs `:` → 固件每个字段无冒号，coil_serial.py:207 `if ":" not in part: continue` **整行全跳过**；模拟实测 PC 解析器对固件真实输出返回全默认值（running=False, freq=0）
- **影响：** GUI `_poll_hw_status`(main.py:703-715) 永远显示不了真实运行状态
- **附带：** 修好键名后 `PhZ=OFF` 会让 `float("OFF")` 抛 ValueError(coil_serial.py:210-223 无容错)，现被 _poll 的 try/except 吞掉
- **修法：** 三方统一（建议固件改为 PC 解析器期望格式），解析器加 OFF/缺失容错

## 2026-08-27：通信协议 BUG — UART0 console 日志与命令口共用（数据流污染）

- **问题：** sdkconfig 实测 `CONFIG_ESP_CONSOLE_UART_NUM=0` + `CONFIG_LOG_DEFAULT_LEVEL=INFO`(=3)；固件同时用 ESP_LOGI 打日志、用 UART0 收发命令
- **影响：** boot/运行期日志行（`I (1234) COIL: ...`）混入串口流，PC `send()`(coil_serial.py:108-114) 取队列第一条即返回，可能拿到日志而非 OK/ERR → 命令响应误判；复位后 boot 未完即发命令时必然串扰
- **修法：** sdkconfig 设 `CONFIG_ESP_CONSOLE_NONE=y`（或命令口挪 UART1）；PC 端 send() 加命令-响应配对、过滤非 OK/ERR 行

## 2026-08-27：通信协议次要问题（一批）

- **心跳未实现：** protocol.md:27 承诺每 5s 上报 `OK:Status...`，固件无周期上报；coil_serial.py:237 的 OK:Status 回调分支为死代码（现靠 GUI 500ms 轮询 STATUS 掩盖）
- **send() 无命令-响应关联 + 主线程阻塞：** _rx_queue 被 rx 线程/send/_wait_for_ready 三方共享，send 取第一条即返回，慢响应/残留消息会错配；最坏阻塞 10×2s=20s，而 _poll_hw_status 跑在 Qt 主线程 → ESP 掉线时 GUI 卡死最多 20s。应加配对 + 轮询移出主线程
- **-1 语义冲突：** protocol.md:64 称"任意轴传 -1 关闭该轴"，固件 parse_phase_token 只认 OFF/NONE/NaN，`-1` 被归一化为 359° 且 enable=True（模拟已验证）→ 照文档操作会意外以近同相启动
- **连接握手无效：** connect() sleep(2) 后 reset_input_buffer(coil_serial.py:64/70) 把 boot 的 "System ready" 丢光，_wait_for_ready 只能 5s 超时兜底，无法识别连到的是否 ESP32；自动选口只取第一个串口
- **GUI 不检查回复：** _start_vibrate/_start_rotate(main.py:659-690) 不判断 resp 是否 OK，固件拒绝（超频超流）时界面仍显示"运行中"

## 2026-07-27：调研方向认知修正

- **问题：** Q 最初把"震荡磁场/磁性脂质体/旋转磁场"当作一个研究方向调研
- **实际情况：** PubMed 检索显示这是 3 个相对独立的方向
  - 磁性脂质体主要跟磁热疗配对（高频 AC 场 → IONP 产热 → 释药，参数 100-300 kHz / 10-50 mT）
  - 震荡磁场 OMF 多用于食品/物理效应（低频，10 mT / 10 Hz）
  - 旋转磁场 RMF 多用于磁驱动/微机器人（低频，1.5 mT / 6 Hz）
- **教训：** 调研关键词看似同领域，实际物理机制与应用完全不同。下次调研前先做 scoping review，避免方向混淆
- **下一步：** Q 决定聚焦路径（A/B/C）后，单独跑针对性的检索

## 2026-07-27：PubMed 10 篇无 review/meta-analysis

- **问题：** 本批 5 个查询拉到的 10 篇全是 original research（2019-2025），没有 review/meta-analysis
- **原因：** 检索策略用 `[tiab]` 限定 title/abstract 关键词，未做综述类专门检索
- **临时绕过：** 10 篇均为原始研究，可作为机制理解的一手材料；综述缺口由 L/Q 决定是否补查
- **下一步：** 如需 review 优先，补跑 query `"magnetic liposome"[mh] AND review[pt]` / `"magnetic hyperthermia"[mh] AND review[pt]`

## 2026-07-27：GitHub 磁性脂质体代码 0 命中

- **问题：** GitHub 13 个 query 均未找到磁性脂质体相关公开代码
- **原因：** 该子领域研究可能仍处于实验室阶段，代码/数据多在论文 supplementary 或实验室内部 GitLab
- **影响：** 项目代码实现必须自己写，无法 fork 现成方案
- **建议借鉴的邻近资源：**
  - `magpylib/magpylib` (362⭐) — 静磁场计算，可用于仿真本项目线圈产生的磁场
  - `rfjakob/HelmholtzM` — 三轴 Helmholtz 线圈硬件控制（MATLAB App + 嵌入式 C），与本项目同构度最高

## 2026-07-27：项目根 main.py 与 python/main.py 命名冲突

- **问题：** 项目根 `main.py`（PySide6 GUI，854 行）与 `python/main.py`（Python 控制端，781 行）重名
- **风险：** `python main.py` 执行哪个不明确，IDE 索引可能混乱
- **临时方案：** README 已加命名冲突警告（line 36）
- **下一步：** 重构时考虑改名（`gui_main.py` / `coil_main.py` 或合并到 `python/` 下）

## 2026-07-27：硬件层未实施

- **问题：** 项目软件层（ESP32 固件 + GUI + 通讯库）已封板，但实际硬件（线圈/功率放大/DAC 电路）未做
- **影响：** 调研结果（PubMed 文献中的磁性脂质体 + 磁热疗实验）无法直接用本平台复现
- **下一步：** Q 决定硬件实施计划（购买清单 / PCB 设计 / 线圈绕制）

## 2026-05-20：coil/build/ 编译产物 62MB 已删除

- **问题：** ESP32 固件 build 产物占 62MB，影响仓库体积
- **处理：** 已删除
- **重新编译：** `cd coil && idf.py build`
- **教训：** 编译产物不应入库，添加 `.gitignore` 排除 `coil/build/`
