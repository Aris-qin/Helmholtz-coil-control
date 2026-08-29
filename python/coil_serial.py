"""
线圈串口通信模块
与 ESP32-S3 通过 UART 通信，发送指令/接收状态

协议:
  发送: CMD:<params>\\n
  接收: OK:<msg>\\n  或  ERR:<msg>\\n
"""

import serial
import serial.tools.list_ports
import threading
import queue
import time
import re
from typing import Optional, Callable


class CoilSerialController:
    """亥姆霍兹线圈 ESP32-S3 串口控制器"""

    def __init__(self, port: str = "", baud: int = 115200, timeout: float = 0.5):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None

        self._rx_queue = queue.Queue()
        self._rx_thread: Optional[threading.Thread] = None
        self._running = False

        self._on_status: Optional[Callable] = None  # 状态回调

        # 硬件状态缓存
        self.status = {
            "mode": "NONE",
            "running": False,
            "frequency": 0.0,
            "current": 0.0,
            "phaseX": 0.0,
            "phaseY": 0.0,
            "phaseZ": 0.0,
        }

        self._phase_degrees = {"X": 0.0, "Y": 90.0, "Z": None}
        self._direction_confirmed = False

    @staticmethod
    def list_ports() -> list:
        """列出可用串口"""
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port: str = "") -> bool:
        """连接到 ESP32-S3"""
        p = port or self.port
        if not p:
            available = self.list_ports()
            if not available:
                raise ConnectionError("没有可用的串口")
            # 自动选择第一个 USB 串口设备
            for dev in available:
                self.ser = serial.Serial(dev, self.baud, timeout=self.timeout)
                time.sleep(2)  # 等待 ESP 重启
                self.ser.reset_input_buffer()
                self.port = dev
                break
        else:
            self.ser = serial.Serial(p, self.baud, timeout=self.timeout)
            time.sleep(2)
            self.ser.reset_input_buffer()
            self.port = p

        if not self.ser or not self.ser.is_open:
            raise ConnectionError(f"无法打开串口 {p}")

        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

        # 等待设备就绪
        self._wait_for_ready()
        return True

    def disconnect(self):
        """断开连接"""
        self._running = False
        if self._rx_thread:
            self._rx_thread.join(timeout=2)
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    @property
    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    # --- 命令发送 ---

    def send(self, cmd: str) -> str:
        """发送命令并等待回复"""
        if not self.connected:
            return "ERR:Not connected"

        self.ser.write((cmd + "\n").encode("utf-8"))
        self.ser.flush()

        # 等待回复（最多 5 条）
        for _ in range(10):
            try:
                resp = self._rx_queue.get(timeout=2.0)
                return resp
            except queue.Empty:
                continue
        return "ERR:Timeout"

    def start_vibrate(self, axis: str, freq_hz: float, current_a: float) -> str:
        """启动震荡磁场"""
        cmd = f"VIBE:{axis},{freq_hz:.2f},{current_a:.2f}"
        resp = self.send(cmd)
        if resp.startswith("OK"):
            self.status["mode"] = "VIBRATE"
            self.status["running"] = True
            self.status["frequency"] = freq_hz
            self.status["current"] = current_a
        return resp

    def stop_vibrate(self) -> str:
        """停止震荡磁场"""
        resp = self.send("VIBE_STOP")
        if resp.startswith("OK"):
            self.status["mode"] = "NONE"
            self.status["running"] = False
        return resp

    def set_phase_2d(self, plane: str, angle: float) -> str:
        """设置 2D 平面相位"""
        resp = self.send(f"SET_MODE:2D:{plane},{angle:.1f}")
        if resp.startswith("OK"):
            self._phase_degrees = self._to_phase_dict(plane, angle)
            self._direction_confirmed = True
        return resp

    def set_phase_3d(self, px: float, py: float, pz: float) -> str:
        """设置三维相位"""
        resp = self.send(f"SET_PHASE:{px:.1f},{py:.1f},{pz:.1f}")
        if resp.startswith("OK"):
            self._phase_degrees = {"X": px, "Y": py, "Z": pz}
            self._direction_confirmed = True
        return resp

    def start_rotate(self, freq_hz: float, current_a: float) -> str:
        """启动旋转磁场"""
        if not self._direction_confirmed:
            return "ERR:请先确认旋转方向"

        resp = self.send(f"ROTATE:{freq_hz:.2f},{current_a:.2f}")
        if resp.startswith("OK"):
            self.status["mode"] = "ROTATE"
            self.status["running"] = True
            self.status["frequency"] = freq_hz
            self.status["current"] = current_a
        return resp

    def stop_rotate(self) -> str:
        """停止旋转磁场"""
        resp = self.send("ROTATE_STOP")
        if resp.startswith("OK"):
            self.status["mode"] = "NONE"
            self.status["running"] = False
            self._direction_confirmed = False
        return resp

    def emergency_stop(self) -> str:
        """紧急停止"""
        resp = self.send("ALL_STOP")
        self.status["mode"] = "NONE"
        self.status["running"] = False
        self._direction_confirmed = False
        return resp

    def query_status(self) -> dict:
        """查询硬件状态"""
        resp = self.send("STATUS")
        if resp.startswith("OK:"):
            self._parse_status(resp[3:])
        return self.status.copy()

    # --- 内部 ---

    def _to_phase_dict(self, plane: str, angle: float) -> dict:
        ph = {"X": None, "Y": None, "Z": None}
        if plane == "X-Y":
            ph["X"], ph["Y"], ph["Z"] = 0.0, angle, None
        elif plane == "X-Z":
            ph["X"], ph["Y"], ph["Z"] = 0.0, None, angle
        elif plane == "Y-Z":
            ph["X"], ph["Y"], ph["Z"] = None, 0.0, angle
        return ph

    def _parse_status(self, data: str):
        """解析 STATUS 回复"""
        # Format: "Mode:VIBRATE|Running:YES|Freq:100.00Hz|..."
        for part in data.split("|"):
            part = part.strip()
            if ":" not in part:
                continue
            key, val = part.split(":", 1)
            k = key.lower()

            if k == "mode":
                self.status["mode"] = val
            elif k == "running":
                self.status["running"] = val == "YES"
            elif k == "freq":
                self.status["frequency"] = float(val.rstrip("Hz"))
            elif k == "current":
                self.status["current"] = float(val.rstrip("A"))
            elif k == "phasex":
                self.status["phaseX"] = float(val.rstrip("°"))
            elif k == "phasey":
                self.status["phaseY"] = float(val.rstrip("°"))
            elif k == "phasez":
                self.status["phaseZ"] = float(val.rstrip("°"))

    def _rx_loop(self):
        """接收线程"""
        buf = ""
        while self._running and self.ser and self.ser.is_open:
            try:
                data = self.ser.read(1)
                if data:
                    c = data.decode("utf-8", errors="ignore")
                    if c in "\r\n":
                        if buf.strip():
                            self._rx_queue.put(buf.strip())
                            # 状态回调
                            if self._on_status and buf.startswith("OK:Status"):
                                self._parse_status(buf[3:])
                                self._on_status(self.status)
                        buf = ""
                    else:
                        buf += c
            except (serial.SerialException, OSError):
                break
            except Exception:
                break

    def _wait_for_ready(self, timeout: float = 5.0):
        """等待设备就绪信号"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self._rx_queue.get(timeout=0.5)
                if "ready" in msg.lower() or "System ready" in msg:
                    return
            except queue.Empty:
                pass
        # 超时也继续，不一定所有版本都发 ready

    def set_status_callback(self, cb: Callable):
        """设置状态更新回调"""
        self._on_status = cb
