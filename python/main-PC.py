import sys
import traceback
import numpy as np
import serial
import serial.tools.list_ports

import matplotlib
matplotlib.use("QtAgg")

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QDoubleSpinBox,
    QComboBox, QPushButton, QTextEdit,
    QStackedWidget, QScrollArea, QSlider,
    QSizePolicy, QMessageBox, QLineEdit
)
from PySide6.QtCore import Qt, Slot, QTimer

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "Arial Unicode MS"
]
matplotlib.rcParams["axes.unicode_minus"] = False


# ============================================================
# 串口通信
# ============================================================
class SerialManager:
    def __init__(self, log_callback=None):
        self.ser = None
        self.log_callback = log_callback

    def is_open(self):
        return self.ser is not None and self.ser.is_open

    def list_ports(self):
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append(p.device)
        return ports

    def connect(self, port, baud=115200):
        self.disconnect()
        self.ser = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=0.02,
            write_timeout=0.5
        )
        self._log(f"[SERIAL] 已连接 {port} @ {baud}")

    def disconnect(self):
        if self.ser is not None:
            try:
                port = self.ser.port
                self.ser.close()
                self._log(f"[SERIAL] 已断开 {port}")
            except Exception:
                pass
        self.ser = None

    def send_line(self, line: str):
        if not self.is_open():
            raise RuntimeError("串口未连接")

        msg = (line.strip() + "\r\n").encode("utf-8")
        self.ser.write(msg)
        self.ser.flush()
        self._log(f"[TX] {line.strip()}")

    def read_lines(self):
        if not self.is_open():
            return []

        lines = []
        try:
            while self.ser.in_waiting > 0:
                raw = self.ser.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="ignore").strip()
                if text:
                    lines.append(text)
                    self._log(f"[RX] {text}")
        except Exception as e:
            self._log(f"[SERIAL-ERR] {e}")

        return lines

    def _log(self, text):
        print(text)
        if self.log_callback:
            self.log_callback(text)


# ============================================================
# 控制器
# ============================================================
class HelmholtzController:
    MAX_CURRENT = 5.0

    def __init__(self, serial_mgr: SerialManager):
        self.serial = serial_mgr

        self.mode = "none"

        self.vibrate_axis = "X"
        self.vibrate_frequency = 0.0
        self.vibrate_current = 0.5
        self.vibrate_running = False

        self.rotate_frequency = 0.0
        self.rotate_current = 0.5
        self.rotate_running = False
        self.direction_confirmed = False

        self.phase_input_mode = "平面旋转"
        self.phase_plane = "X-Y"
        self.phase_angle = 90.0

        self.actual_phase = {
            "X": 0.0,
            "Y": 90.0,
            "Z": None
        }

    def _fmt_phase(self, value):
        if value is None:
            return "关闭"
        return f"{value:.1f}°"

    def _phase_token(self, value):
        if value is None:
            return "OFF"
        return f"{float(value):.1f}"

    def build_phase_command(self):
        ph = self.actual_phase
        return (
            f"SET_PHASE:"
            f"{self._phase_token(ph['X'])},"
            f"{self._phase_token(ph['Y'])},"
            f"{self._phase_token(ph['Z'])}"
        )

    def send_phase_now(self):
        cmd = self.build_phase_command()
        self.serial.send_line(cmd)
        return cmd

    def confirm_direction_2d(self, plane, angle):
        self.phase_input_mode = "平面旋转"
        self.phase_plane = plane
        self.phase_angle = angle

        ph = {
            "X": None,
            "Y": None,
            "Z": None
        }

        if plane == "X-Y":
            ph["X"] = 0.0
            ph["Y"] = float(angle)
            ph["Z"] = None

        elif plane == "X-Z":
            ph["X"] = 0.0
            ph["Y"] = None
            ph["Z"] = float(angle)

        elif plane == "Y-Z":
            ph["X"] = None
            ph["Y"] = 0.0
            ph["Z"] = float(angle)

        self.actual_phase = ph
        self.direction_confirmed = True
        return ph.copy()

    def confirm_direction_3d(self, px, py, pz):
        self.phase_input_mode = "三维直角坐标系 X-Y-Z"

        self.actual_phase = {
            "X": float(px),
            "Y": float(py),
            "Z": float(pz)
        }

        self.direction_confirmed = True
        return self.actual_phase.copy()

    def start_vibrate(self, axis, freq, current):
        if current > self.MAX_CURRENT:
            return False, f"电流超限！最大 {self.MAX_CURRENT} A"
        if not self.serial.is_open():
            return False, "串口未连接"

        self.vibrate_axis = axis
        self.vibrate_frequency = freq
        self.vibrate_current = current

        cmd = f"VIBE:{axis},{freq:.2f},{current:.2f}"
        self.serial.send_line(cmd)

        self.vibrate_running = True
        self.rotate_running = False
        self.mode = "vibrate"

        return True, (
            f"震荡磁场运行中\n"
            f"轴: {axis}\n"
            f"频率: {freq:.2f} Hz\n"
            f"电流: {current:.2f} A\n"
            f"下发命令: {cmd}"
        )

    def stop_vibrate(self):
        if self.serial.is_open():
            self.serial.send_line("VIBE_STOP")
        self.vibrate_running = False
        return "震荡磁场已停止"

    def start_rotate(self, freq, current):
        if not self.direction_confirmed:
            return False, "⚠ 请先点击「确认旋转方向」！"
        if current > self.MAX_CURRENT:
            return False, f"电流超限！最大 {self.MAX_CURRENT} A"
        if not self.serial.is_open():
            return False, "串口未连接"

        self.rotate_frequency = freq
        self.rotate_current = current

        phase_cmd = self.build_phase_command()
        rotate_cmd = f"ROTATE:{freq:.2f},{current:.2f}"

        self.serial.send_line(phase_cmd)
        self.serial.send_line(rotate_cmd)

        self.rotate_running = True
        self.vibrate_running = False
        self.mode = "rotate"

        ph = self.actual_phase

        return True, (
            f"旋转磁场运行中\n"
            f"频率: {freq:.2f} Hz  |  电流: {current:.2f} A\n"
            f"模式: {self.phase_input_mode}\n"
            f"实际相位 → "
            f"X:{self._fmt_phase(ph['X'])}  "
            f"Y:{self._fmt_phase(ph['Y'])}  "
            f"Z:{self._fmt_phase(ph['Z'])}\n"
            f"下发命令:\n{phase_cmd}\n{rotate_cmd}"
        )

    def update_rotate_phase_live(self):
        if not self.serial.is_open():
            return False, "串口未连接"
        if not self.rotate_running:
            return False, "当前未在旋转运行中"

        cmd = self.build_phase_command()
        self.serial.send_line(cmd)
        return True, f"已实时更新相位: {cmd}"

    def stop_rotate(self):
        if self.serial.is_open():
            self.serial.send_line("ROTATE_STOP")
        self.rotate_running = False
        self.direction_confirmed = False
        return "旋转磁场已停止（需重新确认方向）"

    def request_status(self):
        if self.serial.is_open():
            self.serial.send_line("STATUS")

    def emergency_stop(self):
        if self.serial.is_open():
            self.serial.send_line("ALL_STOP")
        self.vibrate_running = False
        self.rotate_running = False
        self.direction_confirmed = False

    def compute_trajectory(self, n_points=1000):
        ph = self.actual_phase

        I = self.rotate_current
        if I <= 0:
            I = 1.0

        omega = 1.0
        t = np.linspace(0, 2 * np.pi, n_points)

        def wave(axis):
            if ph[axis] is None:
                return np.zeros_like(t)
            return I * np.sin(omega * t + np.deg2rad(ph[axis]))

        x = wave("X")
        y = wave("Y")
        z = wave("Z")

        return x, y, z


# ============================================================
# 轨迹显示
# ============================================================
class FieldMatplotlibWidget(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)

        self.ctrl = controller

        self.setMinimumHeight(620)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.fig = Figure(figsize=(12, 7), dpi=100)
        self.canvas = FigureCanvas(self.fig)

        self.canvas.setMinimumHeight(600)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.canvas, 1)

        self.ax3d = self.fig.add_subplot(221, projection="3d")
        self.ax_xy = self.fig.add_subplot(222)
        self.ax_xz = self.fig.add_subplot(223)
        self.ax_yz = self.fig.add_subplot(224)

        self.fig.subplots_adjust(
            left=0.06,
            right=0.97,
            bottom=0.07,
            top=0.92,
            wspace=0.28,
            hspace=0.35
        )

        self.refresh()

    def _style_2d_axis(self, ax, title, xlabel, ylabel, lim):
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)

        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)

        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, alpha=0.3)

        ax.axhline(0, color="gray", linewidth=0.8, alpha=0.5)
        ax.axvline(0, color="gray", linewidth=0.8, alpha=0.5)

    def _draw_start_end_2d(self, ax, a, b):
        ax.scatter(a[0], b[0], c="blue", s=35)
        ax.scatter(a[-1], b[-1], c="green", s=35)

    def _draw_empty(self):
        lim = 1.2

        self.ax3d.clear()
        self.ax_xy.clear()
        self.ax_xz.clear()
        self.ax_yz.clear()

        self.ax3d.set_title("3D 轨迹\n未确认方向")
        self.ax3d.set_xlabel("X")
        self.ax3d.set_ylabel("Y")
        self.ax3d.set_zlabel("Z")
        self.ax3d.set_xlim(-lim, lim)
        self.ax3d.set_ylim(-lim, lim)
        self.ax3d.set_zlim(-lim, lim)

        try:
            self.ax3d.set_box_aspect((1, 1, 1))
        except Exception:
            pass

        self._style_2d_axis(self.ax_xy, "XY 投影", "X", "Y", lim)
        self._style_2d_axis(self.ax_xz, "XZ 投影", "X", "Z", lim)
        self._style_2d_axis(self.ax_yz, "YZ 投影", "Y", "Z", lim)

    def refresh(self):
        if not self.ctrl.direction_confirmed:
            self._draw_empty()
            self.canvas.draw_idle()
            return

        x, y, z = self.ctrl.compute_trajectory(1000)

        I = self.ctrl.rotate_current
        if I <= 0:
            I = 1.0

        lim = max(I * 1.2, 1.0)

        ph = self.ctrl.actual_phase

        title = (
            f"{self.ctrl.phase_input_mode}\n"
            f"X={self.ctrl._fmt_phase(ph['X'])}, "
            f"Y={self.ctrl._fmt_phase(ph['Y'])}, "
            f"Z={self.ctrl._fmt_phase(ph['Z'])}"
        )

        self.ax3d.clear()
        self.ax_xy.clear()
        self.ax_xz.clear()
        self.ax_yz.clear()

        self.ax3d.plot(x, y, z, "r", linewidth=2)
        self.ax3d.scatter(x[0], y[0], z[0], c="blue", s=45, label="起点")
        self.ax3d.scatter(x[-1], y[-1], z[-1], c="green", s=45, label="终点")

        self.ax3d.set_title("3D 轨迹\n" + title)
        self.ax3d.set_xlabel("X")
        self.ax3d.set_ylabel("Y")
        self.ax3d.set_zlabel("Z")

        self.ax3d.set_xlim(-lim, lim)
        self.ax3d.set_ylim(-lim, lim)
        self.ax3d.set_zlim(-lim, lim)

        try:
            self.ax3d.set_box_aspect((1, 1, 1))
        except Exception:
            pass

        self.ax3d.legend()

        self.ax_xy.plot(x, y, "r", linewidth=2)
        self._draw_start_end_2d(self.ax_xy, x, y)
        self._style_2d_axis(self.ax_xy, "XY 投影", "X", "Y", lim)

        self.ax_xz.plot(x, z, "m", linewidth=2)
        self._draw_start_end_2d(self.ax_xz, x, z)
        self._style_2d_axis(self.ax_xz, "XZ 投影", "X", "Z", lim)

        self.ax_yz.plot(y, z, "c", linewidth=2)
        self._draw_start_end_2d(self.ax_yz, y, z)
        self._style_2d_axis(self.ax_yz, "YZ 投影", "Y", "Z", lim)

        self.canvas.draw_idle()


# ============================================================
# 主界面
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("亥姆霍兹线圈控制系统")
        self.setMinimumSize(1400, 1050)
        self.resize(1400, 1050)

        self.serial_mgr = SerialManager(self._append_log)
        self.ctrl = HelmholtzController(self.serial_mgr)

        self._build_ui()
        self._set_mode_ui("none")

        self.statusBar().showMessage("系统就绪 — 请先连接串口并选择工作模式")

        self.serial_timer = QTimer(self)
        self.serial_timer.timeout.connect(self._poll_serial)
        self.serial_timer.start(50)

    def _append_log(self, text):
        self.serial_log.append(text)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setSpacing(8)
        vbox.setContentsMargins(10, 10, 10, 6)

        vbox.addWidget(self._build_serial_bar())
        vbox.addWidget(self._build_mode_bar())

        mid = QHBoxLayout()
        mid.setSpacing(8)
        mid.addWidget(self._build_vibrate_panel(), 1)
        mid.addWidget(self._build_rotate_panel(), 1)

        vbox.addLayout(mid)

        viz_grp = QGroupBox("XYZ 三维直角坐标系磁场轨迹模拟")
        viz_grp.setMinimumHeight(680)
        viz_grp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        vl = QVBoxLayout(viz_grp)
        vl.setContentsMargins(4, 16, 4, 4)

        self.field_view = FieldMatplotlibWidget(self.ctrl)
        vl.addWidget(self.field_view, 1)

        vbox.addWidget(viz_grp)

        log_grp = QGroupBox("串口通信日志（Python 发出的调度命令 / 固件返回）")
        log_lay = QVBoxLayout(log_grp)

        self.serial_log = QTextEdit()
        self.serial_log.setReadOnly(True)
        self.serial_log.setMinimumHeight(180)
        log_lay.addWidget(self.serial_log)

        log_btns = QHBoxLayout()
        self.btn_status = QPushButton("查询 STATUS")
        self.btn_status.clicked.connect(self._send_status)

        self.btn_all_stop = QPushButton("紧急停止 ALL_STOP")
        self.btn_all_stop.clicked.connect(self._all_stop)

        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(self.serial_log.clear)

        log_btns.addWidget(self.btn_status)
        log_btns.addWidget(self.btn_all_stop)
        log_btns.addWidget(self.btn_clear_log)
        log_btns.addStretch()
        log_lay.addLayout(log_btns)

        vbox.addWidget(log_grp)

        scroll.setWidget(inner)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _build_serial_bar(self):
        grp = QGroupBox("串口连接")
        lay = QHBoxLayout(grp)
        lay.setContentsMargins(10, 8, 10, 8)

        lay.addWidget(QLabel("端口:"))

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(140)
        lay.addWidget(self.port_combo)

        self.refresh_port_btn = QPushButton("刷新")
        self.refresh_port_btn.clicked.connect(self._refresh_ports)
        lay.addWidget(self.refresh_port_btn)

        lay.addWidget(QLabel("波特率:"))
        self.baud_edit = QLineEdit("115200")
        self.baud_edit.setFixedWidth(90)
        lay.addWidget(self.baud_edit)

        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._toggle_connect)
        lay.addWidget(self.connect_btn)

        self.conn_label = QLabel("未连接")
        self.conn_label.setStyleSheet("color:#ff8866; font-weight:bold;")
        lay.addWidget(self.conn_label)

        lay.addStretch()

        self._refresh_ports()
        return grp

    def _build_mode_bar(self):
        grp = QGroupBox("模式选择")
        lay = QHBoxLayout(grp)
        lay.setContentsMargins(10, 8, 10, 8)

        lay.addWidget(QLabel("当前模式:"))

        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumWidth(140)
        self.mode_combo.addItems([
            "— 请选择 —",
            "震荡磁场",
            "旋转磁场"
        ])

        lay.addWidget(self.mode_combo)

        self.mode_btn = QPushButton("确认模式")
        self.mode_btn.setFixedWidth(100)
        self.mode_btn.clicked.connect(self._on_mode_confirm)

        lay.addWidget(self.mode_btn)
        lay.addStretch()

        return grp

    def _build_vibrate_panel(self):
        self.vibrate_grp = QGroupBox("震荡磁场控制")
        lay = QVBoxLayout(self.vibrate_grp)
        lay.setSpacing(10)
        lay.setContentsMargins(10, 16, 10, 10)

        g = QGridLayout()
        g.setSpacing(8)

        g.addWidget(QLabel("震荡轴:"), 0, 0)

        self.v_axis = QComboBox()
        self.v_axis.addItems(["X", "Y", "Z"])
        self.v_axis.setMinimumHeight(28)
        g.addWidget(self.v_axis, 0, 1)

        g.addWidget(QLabel("频率 (Hz):"), 1, 0)

        self.v_freq = QDoubleSpinBox()
        self.v_freq.setRange(0.01, 5000)
        self.v_freq.setValue(100)
        self.v_freq.setMinimumHeight(28)
        g.addWidget(self.v_freq, 1, 1)

        g.addWidget(QLabel("电流 (A):"), 2, 0)

        self.v_curr = QDoubleSpinBox()
        self.v_curr.setRange(0.1, 5.0)
        self.v_curr.setValue(0.5)
        self.v_curr.setSingleStep(0.1)
        self.v_curr.setMinimumHeight(28)
        g.addWidget(self.v_curr, 2, 1)

        lay.addLayout(g)

        br = QHBoxLayout()
        br.setSpacing(8)

        self.v_start = QPushButton("▶ 启动")
        self.v_start.setMinimumHeight(34)
        self.v_start.clicked.connect(self._start_vibrate)

        self.v_stop = QPushButton("■ 停止")
        self.v_stop.setMinimumHeight(34)
        self.v_stop.clicked.connect(self._stop_vibrate)

        br.addWidget(self.v_start)
        br.addWidget(self.v_stop)

        lay.addLayout(br)

        self.v_status = QTextEdit()
        self.v_status.setReadOnly(True)
        self.v_status.setMinimumHeight(90)
        self.v_status.setPlaceholderText("等待启动...")
        lay.addWidget(self.v_status)

        lay.addStretch()
        return self.vibrate_grp

    def _build_rotate_panel(self):
        self.rotate_grp = QGroupBox("旋转磁场控制")
        lay = QVBoxLayout(self.rotate_grp)
        lay.setSpacing(10)
        lay.setContentsMargins(10, 16, 10, 10)

        g = QGridLayout()
        g.setSpacing(8)

        g.addWidget(QLabel("频率 (Hz):"), 0, 0)

        self.r_freq = QDoubleSpinBox()
        self.r_freq.setRange(0.01, 5000)
        self.r_freq.setValue(100)
        self.r_freq.setMinimumHeight(28)
        g.addWidget(self.r_freq, 0, 1)

        g.addWidget(QLabel("电流 (A):"), 1, 0)

        self.r_curr = QDoubleSpinBox()
        self.r_curr.setRange(0.1, 5.0)
        self.r_curr.setValue(0.5)
        self.r_curr.setSingleStep(0.1)
        self.r_curr.setMinimumHeight(28)
        g.addWidget(self.r_curr, 1, 1)

        lay.addLayout(g)

        phase_grp = QGroupBox("XYZ 相位控制器")
        phase_lay = QVBoxLayout(phase_grp)
        phase_lay.setSpacing(8)
        phase_lay.setContentsMargins(10, 16, 10, 10)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)

        mode_row.addWidget(QLabel("输入模式:"))

        self.r_input_mode = QComboBox()
        self.r_input_mode.addItems([
            "平面旋转",
            "三维直角坐标系 X-Y-Z"
        ])
        self.r_input_mode.setMinimumHeight(28)
        self.r_input_mode.currentIndexChanged.connect(self._on_input_mode_changed)

        mode_row.addWidget(self.r_input_mode, 1)
        phase_lay.addLayout(mode_row)

        self.phase_stack = QStackedWidget()
        self.phase_stack.setMinimumHeight(180)

        p0 = QWidget()
        g0 = QGridLayout(p0)
        g0.setSpacing(8)
        g0.setContentsMargins(0, 6, 0, 6)

        g0.addWidget(QLabel("旋转平面:"), 0, 0)

        self.r_plane = QComboBox()
        self.r_plane.addItems(["X-Y", "X-Z", "Y-Z"])
        self.r_plane.setMinimumHeight(28)
        g0.addWidget(self.r_plane, 0, 1)

        g0.addWidget(QLabel("相位差 (°):"), 1, 0)

        self.r_angle = QDoubleSpinBox()
        self.r_angle.setRange(-360, 360)
        self.r_angle.setValue(90)
        self.r_angle.setSingleStep(5)
        self.r_angle.setMinimumHeight(28)
        g0.addWidget(self.r_angle, 1, 1)

        hint0 = QLabel("平面李萨如：A=sin(t), B=sin(t+φ)。0°/180°=直线，90°/270°=圆。")
        hint0.setStyleSheet("color: #888; font-size: 11px;")
        g0.addWidget(hint0, 2, 0, 1, 2)

        self.phase_stack.addWidget(p0)

        p1 = QWidget()
        g1 = QGridLayout(p1)
        g1.setSpacing(8)
        g1.setContentsMargins(0, 6, 0, 6)

        self.r_3d_px_slider, self.r_3d_px_label = self._add_phase_slider(g1, 0, "X轴相位:", 0)
        self.r_3d_py_slider, self.r_3d_py_label = self._add_phase_slider(g1, 1, "Y轴相位:", 90)
        self.r_3d_pz_slider, self.r_3d_pz_label = self._add_phase_slider(g1, 2, "Z轴相位:", 45)

        hint1 = QLabel("三维轨迹：X=sin(t+Px), Y=sin(t+Py), Z=sin(t+Pz)。滑块变化后需点击确认。")
        hint1.setStyleSheet("color: #888; font-size: 11px;")
        g1.addWidget(hint1, 3, 0, 1, 3)

        self.phase_stack.addWidget(p1)
        phase_lay.addWidget(self.phase_stack)

        self.r_confirm = QPushButton("✔ 确认旋转方向")
        self.r_confirm.setMinimumHeight(34)
        self.r_confirm.clicked.connect(self._confirm_direction)
        phase_lay.addWidget(self.r_confirm)

        self.r_apply_phase = QPushButton("↻ 运行中实时更新相位")
        self.r_apply_phase.setMinimumHeight(34)
        self.r_apply_phase.clicked.connect(self._apply_rotate_phase_live)
        phase_lay.addWidget(self.r_apply_phase)

        ph_row = QHBoxLayout()
        ph_row.setSpacing(12)

        ph_row.addWidget(QLabel("实际相位:"))

        self.lbl_px = QLabel("X: --")
        self.lbl_py = QLabel("Y: --")
        self.lbl_pz = QLabel("Z: --")

        for lbl in (self.lbl_px, self.lbl_py, self.lbl_pz):
            lbl.setStyleSheet("color:#ff8866; font-weight:bold; font-size:13px;")
            lbl.setMinimumWidth(90)
            ph_row.addWidget(lbl)

        ph_row.addStretch()
        phase_lay.addLayout(ph_row)

        lay.addWidget(phase_grp)

        br = QHBoxLayout()
        br.setSpacing(8)

        self.r_start = QPushButton("▶ 启动旋转")
        self.r_start.setMinimumHeight(36)
        self.r_start.clicked.connect(self._start_rotate)

        self.r_stop = QPushButton("■ 停止")
        self.r_stop.setMinimumHeight(36)
        self.r_stop.clicked.connect(self._stop_rotate)

        br.addWidget(self.r_start)
        br.addWidget(self.r_stop)

        lay.addLayout(br)

        self.r_status = QTextEdit()
        self.r_status.setReadOnly(True)
        self.r_status.setMinimumHeight(110)
        self.r_status.setPlaceholderText("请先确认旋转方向...")
        lay.addWidget(self.r_status)

        return self.rotate_grp

    def _add_phase_slider(self, grid, row, title, init_value):
        grid.addWidget(QLabel(title), row, 0)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 360)
        slider.setValue(init_value)
        slider.setTickInterval(30)
        slider.setTickPosition(QSlider.TicksBelow)
        slider.valueChanged.connect(self._on_3d_phase_slider_changed)

        grid.addWidget(slider, row, 1)

        label = QLabel(f"{init_value}°")
        label.setMinimumWidth(45)
        label.setStyleSheet("color:#ff8866; font-weight:bold;")
        grid.addWidget(label, row, 2)

        return slider, label

    def _set_mode_ui(self, mode):
        self.vibrate_grp.setEnabled(mode == "vibrate")
        self.rotate_grp.setEnabled(mode == "rotate")

        if mode == "rotate":
            self.r_start.setEnabled(False)

    def _refresh_ports(self):
        self.port_combo.clear()
        self.port_combo.addItems(self.serial_mgr.list_ports())

    @Slot()
    def _toggle_connect(self):
        try:
            if self.serial_mgr.is_open():
                self.serial_mgr.disconnect()
                self.connect_btn.setText("连接")
                self.conn_label.setText("未连接")
                self.statusBar().showMessage("串口已断开")
                return

            port = self.port_combo.currentText().strip()
            if not port:
                QMessageBox.warning(self, "提示", "未找到串口")
                return

            baud = int(self.baud_edit.text().strip())
            self.serial_mgr.connect(port, baud)
            self.connect_btn.setText("断开")
            self.conn_label.setText(f"已连接: {port}")
            self.statusBar().showMessage(f"串口已连接 {port} @ {baud}")

            self.ctrl.request_status()

        except Exception as e:
            QMessageBox.critical(self, "串口错误", str(e))
            self._append_log(traceback.format_exc())

    @Slot()
    def _on_mode_confirm(self):
        text = self.mode_combo.currentText()

        if text == "震荡磁场":
            self.ctrl.mode = "vibrate"
            self._set_mode_ui("vibrate")
            self.statusBar().showMessage("✔ 震荡磁场模式激活")

        elif text == "旋转磁场":
            self.ctrl.mode = "rotate"
            self.ctrl.direction_confirmed = False
            self._set_mode_ui("rotate")
            self.field_view.refresh()
            self.statusBar().showMessage("✔ 旋转磁场激活 — 请先确认旋转方向")

        else:
            self.ctrl.mode = "none"
            self._set_mode_ui("none")
            self.statusBar().showMessage("请选择工作模式")

    @Slot(int)
    def _on_input_mode_changed(self, idx):
        self.phase_stack.setCurrentIndex(idx)

        self.ctrl.direction_confirmed = False
        self.r_start.setEnabled(False)

        self.lbl_px.setText("X: --")
        self.lbl_py.setText("Y: --")
        self.lbl_pz.setText("Z: --")

        self.field_view.refresh()

    @Slot()
    def _on_3d_phase_slider_changed(self):
        self.r_3d_px_label.setText(f"{self.r_3d_px_slider.value()}°")
        self.r_3d_py_label.setText(f"{self.r_3d_py_slider.value()}°")
        self.r_3d_pz_label.setText(f"{self.r_3d_pz_slider.value()}°")

    @Slot()
    def _confirm_direction(self):
        idx = self.phase_stack.currentIndex()

        if idx == 0:
            ph = self.ctrl.confirm_direction_2d(
                self.r_plane.currentText(),
                self.r_angle.value()
            )
        else:
            ph = self.ctrl.confirm_direction_3d(
                self.r_3d_px_slider.value(),
                self.r_3d_py_slider.value(),
                self.r_3d_pz_slider.value()
            )

        self.lbl_px.setText(f"X: {self.ctrl._fmt_phase(ph['X'])}")
        self.lbl_py.setText(f"Y: {self.ctrl._fmt_phase(ph['Y'])}")
        self.lbl_pz.setText(f"Z: {self.ctrl._fmt_phase(ph['Z'])}")

        self.r_start.setEnabled(True)
        self.field_view.refresh()

        msg = (
            f"✔ 方向已确认  "
            f"X:{self.ctrl._fmt_phase(ph['X'])}  "
            f"Y:{self.ctrl._fmt_phase(ph['Y'])}  "
            f"Z:{self.ctrl._fmt_phase(ph['Z'])}"
        )

        # 如果当前正在旋转，确认后可立即实时下发
        if self.ctrl.rotate_running and self.serial_mgr.is_open():
            try:
                cmd = self.ctrl.send_phase_now()
                msg += f" | 已实时下发: {cmd}"
            except Exception as e:
                msg += f" | 实时下发失败: {e}"

        self.statusBar().showMessage(msg)

    @Slot()
    def _apply_rotate_phase_live(self):
        try:
            ok, msg = self.ctrl.update_rotate_phase_live()
            self.r_status.append(msg)
            self.statusBar().showMessage(msg)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    @Slot()
    def _start_vibrate(self):
        try:
            ok, msg = self.ctrl.start_vibrate(
                self.v_axis.currentText(),
                self.v_freq.value(),
                self.v_curr.value()
            )
            self.v_status.setText(msg)
            self.statusBar().showMessage(msg.split("\n")[0])
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    @Slot()
    def _stop_vibrate(self):
        try:
            self.v_status.setText(self.ctrl.stop_vibrate())
            self.statusBar().showMessage("震荡磁场已停止")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    @Slot()
    def _start_rotate(self):
        try:
            ok, msg = self.ctrl.start_rotate(
                self.r_freq.value(),
                self.r_curr.value()
            )

            self.r_status.setText(msg)

            if ok:
                self.field_view.refresh()

            self.statusBar().showMessage(msg.split("\n")[0])
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    @Slot()
    def _stop_rotate(self):
        try:
            self.r_status.setText(self.ctrl.stop_rotate())
            self.r_start.setEnabled(False)
            self.field_view.refresh()
            self.statusBar().showMessage("旋转磁场已停止")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    @Slot()
    def _send_status(self):
        try:
            self.ctrl.request_status()
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    @Slot()
    def _all_stop(self):
        try:
            self.ctrl.emergency_stop()
            self.r_start.setEnabled(False)
            self.field_view.refresh()
            self.statusBar().showMessage("已发送 ALL_STOP")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _poll_serial(self):
        try:
            lines = self.serial_mgr.read_lines()
            if not lines:
                return
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self.serial_mgr.disconnect()
        except Exception:
            pass
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    app.setStyleSheet("""
        QMainWindow, QWidget {
            background-color: #1e1e1e;
            color: #dcdcdc;
            font-family: "Microsoft YaHei", Arial, sans-serif;
            font-size: 13px;
        }

        QGroupBox {
            border: 1px solid #555;
            border-radius: 5px;
            margin-top: 14px;
            padding-top: 12px;
            font-weight: bold;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
        }

        QGroupBox:disabled {
            border-color: #333;
            color: #555;
        }

        QPushButton {
            background-color: #2d2d2d;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 5px 14px;
            min-width: 80px;
        }

        QPushButton:hover {
            background-color: #3c3c3c;
        }

        QPushButton:pressed {
            background-color: #505050;
        }

        QPushButton:disabled {
            color: #555;
            border-color: #333;
        }

        QDoubleSpinBox, QComboBox, QLineEdit {
            background-color: #2a2a2a;
            border: 1px solid #555;
            border-radius: 3px;
            padding: 3px 5px;
            min-height: 24px;
        }

        QDoubleSpinBox:disabled, QComboBox:disabled {
            color: #555;
            border-color: #333;
        }

        QSlider::groove:horizontal {
            height: 6px;
            background: #444;
            border-radius: 3px;
        }

        QSlider::handle:horizontal {
            background: #ff8866;
            width: 14px;
            margin: -5px 0;
            border-radius: 7px;
        }

        QTextEdit {
            background-color: #232323;
            border: 1px solid #444;
            border-radius: 3px;
        }

        QStatusBar {
            background-color: #252525;
            border-top: 1px solid #444;
            color: #aaa;
        }

        QStackedWidget {
            background: transparent;
        }

        QScrollArea {
            background: transparent;
            border: none;
        }
    """)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())