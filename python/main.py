"""
亥姆霍兹线圈控制系统 - 硬件版
集成 PC GUI + ESP32-S3 串口控制

用法:
  # 默认自动扫描串口
  python main.py

  # 指定串口
  python main.py --port COM3

  # 纯离线模拟（不连硬件）
  python main.py --offline

依赖:
  pip install PySide6 matplotlib numpy pyserial
"""

import sys
import argparse
import numpy as np

import matplotlib
matplotlib.use("QtAgg")

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QDoubleSpinBox,
    QComboBox, QPushButton, QTextEdit,
    QStackedWidget, QScrollArea, QSlider,
    QSizePolicy, QMessageBox
)
from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QColor

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"
]
matplotlib.rcParams["axes.unicode_minus"] = False

from coil_serial import CoilSerialController


# ============================================================
# 模拟控制器 (离线模式)
# ============================================================
class SimulatedController:
    """纯软件模拟，不依赖硬件"""

    MAX_CURRENT = 5.0

    def __init__(self):
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
        self.actual_phase = {"X": 0.0, "Y": 90.0, "Z": None}

    def confirm_direction_2d(self, plane, angle):
        self.phase_input_mode = "平面旋转"
        self.phase_plane = plane
        self.phase_angle = angle
        ph = {"X": None, "Y": None, "Z": None}
        if plane == "X-Y":
            ph["X"], ph["Y"], ph["Z"] = 0.0, float(angle), None
        elif plane == "X-Z":
            ph["X"], ph["Y"], ph["Z"] = 0.0, None, float(angle)
        elif plane == "Y-Z":
            ph["X"], ph["Y"], ph["Z"] = None, 0.0, float(angle)
        self.actual_phase = ph
        self.direction_confirmed = True
        return ph

    def confirm_direction_3d(self, px, py, pz):
        self.phase_input_mode = "三维直角坐标系 X-Y-Z"
        self.actual_phase = {"X": float(px), "Y": float(py), "Z": float(pz)}
        self.direction_confirmed = True
        return self.actual_phase.copy()

    def start_vibrate(self, axis, freq, current):
        if current > self.MAX_CURRENT:
            return False, f"电流超限！最大 {self.MAX_CURRENT} A"
        self.vibrate_axis = axis
        self.vibrate_frequency = freq
        self.vibrate_current = current
        self.vibrate_running = True
        return True, f"震荡磁场运行中\n轴: {axis}\n频率: {freq:.2f} Hz\n电流: {current:.2f} A"

    def stop_vibrate(self):
        self.vibrate_running = False
        return "震荡磁场已停止"

    def start_rotate(self, freq, current):
        if not self.direction_confirmed:
            return False, "⚠ 请先点击「确认旋转方向」！"
        if current > self.MAX_CURRENT:
            return False, f"电流超限！最大 {self.MAX_CURRENT} A"
        self.rotate_frequency = freq
        self.rotate_current = current
        self.rotate_running = True
        ph = self.actual_phase
        return True, (
            f"旋转磁场运行中\n"
            f"频率: {freq:.2f} Hz  |  电流: {current:.2f} A\n"
            f"模式: {self.phase_input_mode}\n"
            f"实际相位 → "
            f"X:{self._fmt(ph['X'])}  Y:{self._fmt(ph['Y'])}  Z:{self._fmt(ph['Z'])}"
        )

    def stop_rotate(self):
        self.rotate_running = False
        self.direction_confirmed = False
        return "旋转磁场已停止（需重新确认方向）"

    def _fmt(self, v):
        if v is None: return "关闭"
        return f"{v:.1f}°"

    def compute_trajectory(self, n_points=1000):
        ph = self.actual_phase
        I = self.rotate_current if self.rotate_current > 0 else 1.0
        omega = 1.0
        t = np.linspace(0, 2 * np.pi, n_points)
        def wave(ax):
            return np.zeros_like(t) if ph[ax] is None else I * np.sin(omega * t + np.deg2rad(ph[ax]))
        return wave("X"), wave("Y"), wave("Z")


# ============================================================
# 磁轨迹图
# ============================================================
class FieldMatplotlibWidget(QWidget):
    def __init__(self, sim_ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = sim_ctrl
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
        self.fig.subplots_adjust(left=0.06, right=0.97, bottom=0.07, top=0.92, wspace=0.28, hspace=0.35)
        self.refresh()

    def _style_2d(self, ax, title, xl, yl, lim):
        ax.set_title(title); ax.set_xlabel(xl); ax.set_ylabel(yl)
        ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, alpha=0.3)
        ax.axhline(0, color="gray", linewidth=0.8, alpha=0.5)
        ax.axvline(0, color="gray", linewidth=0.8, alpha=0.5)

    def _draw_empty(self):
        lim = 1.2
        self.ax3d.clear(); self.ax_xy.clear(); self.ax_xz.clear(); self.ax_yz.clear()
        self.ax3d.set_title("3D 轨迹\n未确认方向")
        self.ax3d.set_xlabel("X"); self.ax3d.set_ylabel("Y"); self.ax3d.set_zlabel("Z")
        self.ax3d.set_xlim(-lim, lim); self.ax3d.set_ylim(-lim, lim); self.ax3d.set_zlim(-lim, lim)
        try: self.ax3d.set_box_aspect((1, 1, 1))
        except: pass
        self._style_2d(self.ax_xy, "XY 投影", "X", "Y", lim)
        self._style_2d(self.ax_xz, "XZ 投影", "X", "Z", lim)
        self._style_2d(self.ax_yz, "YZ 投影", "Y", "Z", lim)

    def refresh(self):
        if not self.ctrl.direction_confirmed:
            self._draw_empty()
            self.canvas.draw_idle()
            return
        x, y, z = self.ctrl.compute_trajectory(1000)
        I = self.ctrl.rotate_current if self.ctrl.rotate_current > 0 else 1.0
        lim = max(I * 1.2, 1.0)
        ph = self.ctrl.actual_phase
        title = f"{self.ctrl.phase_input_mode}\nX={self.ctrl._fmt(ph['X'])}, Y={self.ctrl._fmt(ph['Y'])}, Z={self.ctrl._fmt(ph['Z'])}"
        self.ax3d.clear(); self.ax_xy.clear(); self.ax_xz.clear(); self.ax_yz.clear()
        self.ax3d.plot(x, y, z, "r", linewidth=2)
        self.ax3d.scatter(x[0], y[0], z[0], c="blue", s=45, label="起点")
        self.ax3d.scatter(x[-1], y[-1], z[-1], c="green", s=45, label="终点")
        self.ax3d.set_title("3D 轨迹\n" + title)
        self.ax3d.set_xlabel("X"); self.ax3d.set_ylabel("Y"); self.ax3d.set_zlabel("Z")
        self.ax3d.set_xlim(-lim, lim); self.ax3d.set_ylim(-lim, lim); self.ax3d.set_zlim(-lim, lim)
        try: self.ax3d.set_box_aspect((1, 1, 1))
        except: pass
        self.ax3d.legend()
        self.ax_xy.plot(x, y, "r", linewidth=2)
        self.ax_xy.scatter(x[0], y[0], c="blue", s=35)
        self.ax_xy.scatter(x[-1], y[-1], c="green", s=35)
        self._style_2d(self.ax_xy, "XY 投影", "X", "Y", lim)
        self.ax_xz.plot(x, z, "m", linewidth=2)
        self.ax_xz.scatter(x[0], z[0], c="blue", s=35)
        self.ax_xz.scatter(x[-1], z[-1], c="green", s=35)
        self._style_2d(self.ax_xz, "XZ 投影", "X", "Z", lim)
        self.ax_yz.plot(y, z, "c", linewidth=2)
        self.ax_yz.scatter(y[0], z[0], c="blue", s=35)
        self.ax_yz.scatter(y[-1], z[-1], c="green", s=35)
        self._style_2d(self.ax_yz, "YZ 投影", "Y", "Z", lim)
        self.canvas.draw_idle()


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self, offline=False, port=""):
        super().__init__()
        self.setWindowTitle("亥姆霍兹线圈控制系统")
        self.setMinimumSize(1350, 1000)
        self.resize(1350, 1000)

        self.offline = offline
        self.serial_port = port

        # 模拟控制器（始终存在，轨迹绘图用）
        self.ctrl = SimulatedController()

        # 硬件控制器
        self.hw = CoilSerialController()
        self.hw_connected = False

        self._build_ui()
        self._set_mode_ui("none")

        # 硬件状态轮询
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._poll_hw_status)
        self._status_timer.setInterval(500)  # 500ms

        # 自动连接
        if not offline:
            QTimer.singleShot(500, self._auto_connect)

        self.statusBar().showMessage("系统就绪")

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

        # 硬件连接栏
        vbox.addWidget(self._build_connect_bar())

        # 模式选择
        vbox.addWidget(self._build_mode_bar())

        # 控制面板
        mid = QHBoxLayout()
        mid.setSpacing(8)
        mid.addWidget(self._build_vibrate_panel(), 1)
        mid.addWidget(self._build_rotate_panel(), 1)
        vbox.addLayout(mid)

        # 轨迹显示
        viz_grp = QGroupBox("XYZ 三维直角坐标系磁场轨迹模拟")
        viz_grp.setMinimumHeight(680)
        viz_grp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vl = QVBoxLayout(viz_grp)
        vl.setContentsMargins(4, 16, 4, 4)
        self.field_view = FieldMatplotlibWidget(self.ctrl)
        vl.addWidget(self.field_view, 1)
        vbox.addWidget(viz_grp)

        scroll.setWidget(inner)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    # ---- 连接栏 ----
    def _build_connect_bar(self):
        grp = QGroupBox("硬件连接")
        lay = QHBoxLayout(grp)
        lay.setContentsMargins(10, 8, 10, 8)

        lay.addWidget(QLabel("串口:"))

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(180)
        self.port_combo.setEditable(True)
        self._refresh_ports()
        lay.addWidget(self.port_combo)

        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.setFixedWidth(70)
        self.refresh_btn.clicked.connect(self._refresh_ports)
        lay.addWidget(self.refresh_btn)

        self.connect_btn = QPushButton("🔗 连接")
        self.connect_btn.setFixedWidth(90)
        self.connect_btn.clicked.connect(self._toggle_connect)
        lay.addWidget(self.connect_btn)

        self.conn_status = QLabel("⚪ 未连接")
        self.conn_status.setStyleSheet("color: #888; font-weight: bold;")
        self.conn_status.setMinimumWidth(120)
        lay.addWidget(self.conn_status)

        self.estop_btn = QPushButton("🔴 紧急停止")
        self.estop_btn.setFixedWidth(100)
        self.estop_btn.setStyleSheet(
            "background-color: #8b0000; color: white; font-weight: bold;"
        )
        self.estop_btn.clicked.connect(self._emergency_stop)
        self.estop_btn.setEnabled(False)
        lay.addWidget(self.estop_btn)

        lay.addStretch()
        return grp

    def _refresh_ports(self):
        self.port_combo.clear()
        ports = CoilSerialController.list_ports()
        if ports:
            self.port_combo.addItems(ports)
            self.port_combo.setCurrentIndex(0)
        self.port_combo.addItem("(手动输入)")

    def _auto_connect(self):
        ports = CoilSerialController.list_ports()
        if self.serial_port:
            target = self.serial_port
        elif ports:
            target = ports[0]
        else:
            self._set_conn_status("未发现串口设备", "#cc6600")
            return

        self._set_conn_status(f"正在连接 {target}...", "#ccaa00")
        try:
            if self.hw.connect(target):
                self.hw_connected = True
                self.connect_btn.setText("🔌 断开")
                self.port_combo.setEnabled(False)
                self.refresh_btn.setEnabled(False)
                self.estop_btn.setEnabled(True)
                self._status_timer.start()
                self._set_conn_status(f"✅ {self.hw.port}", "#44cc44")
                self.statusBar().showMessage(f"✔ 硬件已连接: {self.hw.port}")
        except Exception as e:
            self._set_conn_status(f"连接失败: {e}", "#cc4444")

    def _toggle_connect(self):
        if self.hw_connected:
            self.hw.disconnect()
            self.hw_connected = False
            self.connect_btn.setText("🔗 连接")
            self.port_combo.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            self.estop_btn.setEnabled(False)
            self._status_timer.stop()
            self._set_conn_status("⚪ 已断开", "#888")
            self.statusBar().showMessage("硬件已断开")
        else:
            port = self.port_combo.currentText()
            if not port or port == "(手动输入)":
                self._set_conn_status("请选择串口", "#cc6600")
                return
            self._auto_connect()

    def _set_conn_status(self, text, color="#888"):
        self.conn_status.setText(text)
        self.conn_status.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _emergency_stop(self):
        if self.hw_connected:
            resp = self.hw.emergency_stop()
            self.log_hw(f"紧急停止: {resp}")
        self.ctrl.mode = "none"
        self.ctrl.vibrate_running = False
        self.ctrl.rotate_running = False
        self.ctrl.direction_confirmed = False
        self._set_mode_ui("none")
        self.field_view.refresh()
        self.statusBar().showMessage("🔴 紧急停止！所有输出已归零")

    # ---- 模式选择 ----
    def _build_mode_bar(self):
        grp = QGroupBox("模式选择")
        lay = QHBoxLayout(grp)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(QLabel("当前模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumWidth(140)
        self.mode_combo.addItems(["— 请选择 —", "震荡磁场", "旋转磁场"])
        lay.addWidget(self.mode_combo)
        self.mode_btn = QPushButton("确认模式")
        self.mode_btn.setFixedWidth(100)
        self.mode_btn.clicked.connect(self._on_mode_confirm)
        lay.addWidget(self.mode_btn)
        lay.addStretch()
        return grp

    # ---- 震荡面板 ----
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
        self.v_start = QPushButton("▶  启动")
        self.v_start.setMinimumHeight(34)
        self.v_start.clicked.connect(self._start_vibrate)
        self.v_stop = QPushButton("■  停止")
        self.v_stop.setMinimumHeight(34)
        self.v_stop.clicked.connect(self._stop_vibrate)
        br.addWidget(self.v_start); br.addWidget(self.v_stop)
        lay.addLayout(br)

        self.v_status = QTextEdit()
        self.v_status.setReadOnly(True)
        self.v_status.setMinimumHeight(70)
        self.v_status.setPlaceholderText("等待启动...")
        lay.addWidget(self.v_status)
        lay.addStretch()
        return self.vibrate_grp

    # ---- 旋转面板 ----
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

        # 相位控制器
        phase_grp = QGroupBox("XYZ 相位控制器")
        phase_lay = QVBoxLayout(phase_grp)
        phase_lay.setSpacing(8)
        phase_lay.setContentsMargins(10, 16, 10, 10)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(QLabel("输入模式:"))
        self.r_input_mode = QComboBox()
        self.r_input_mode.addItems(["平面旋转", "三维直角坐标系 X-Y-Z"])
        self.r_input_mode.setMinimumHeight(28)
        self.r_input_mode.currentIndexChanged.connect(self._on_input_mode_changed)
        mode_row.addWidget(self.r_input_mode, 1)
        phase_lay.addLayout(mode_row)

        self.phase_stack = QStackedWidget()
        self.phase_stack.setMinimumHeight(180)

        # 2D 页面
        p0 = QWidget()
        g0 = QGridLayout(p0); g0.setSpacing(8); g0.setContentsMargins(0, 6, 0, 6)
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

        # 3D 页面
        p1 = QWidget()
        g1 = QGridLayout(p1); g1.setSpacing(8); g1.setContentsMargins(0, 6, 0, 6)
        self.r_3d_px_sl, self.r_3d_px_lb = self._add_slider(g1, 0, "X轴相位:", 0)
        self.r_3d_py_sl, self.r_3d_py_lb = self._add_slider(g1, 1, "Y轴相位:", 90)
        self.r_3d_pz_sl, self.r_3d_pz_lb = self._add_slider(g1, 2, "Z轴相位:", 45)
        hint1 = QLabel("三维轨迹：X=sin(t+Px), Y=sin(t+Py), Z=sin(t+Pz)。滑块变化后需点击确认。")
        hint1.setStyleSheet("color: #888; font-size: 11px;")
        g1.addWidget(hint1, 3, 0, 1, 3)
        self.phase_stack.addWidget(p1)

        phase_lay.addWidget(self.phase_stack)

        self.r_confirm = QPushButton("✔  确认旋转方向")
        self.r_confirm.setMinimumHeight(34)
        self.r_confirm.clicked.connect(self._confirm_direction)
        phase_lay.addWidget(self.r_confirm)

        ph_row = QHBoxLayout(); ph_row.setSpacing(12)
        ph_row.addWidget(QLabel("实际相位:"))
        self.lbl_px = QLabel("X: --"); self.lbl_py = QLabel("Y: --"); self.lbl_pz = QLabel("Z: --")
        for lbl in (self.lbl_px, self.lbl_py, self.lbl_pz):
            lbl.setStyleSheet("color:#ff8866; font-weight:bold; font-size:13px;")
            lbl.setMinimumWidth(90)
            ph_row.addWidget(lbl)
        ph_row.addStretch()
        phase_lay.addLayout(ph_row)
        lay.addWidget(phase_grp)

        br = QHBoxLayout(); br.setSpacing(8)
        self.r_start = QPushButton("▶  启动旋转")
        self.r_start.setMinimumHeight(36)
        self.r_start.clicked.connect(self._start_rotate)
        self.r_stop = QPushButton("■  停止")
        self.r_stop.setMinimumHeight(36)
        self.r_stop.clicked.connect(self._stop_rotate)
        br.addWidget(self.r_start); br.addWidget(self.r_stop)
        lay.addLayout(br)

        self.r_status = QTextEdit()
        self.r_status.setReadOnly(True)
        self.r_status.setMinimumHeight(90)
        self.r_status.setPlaceholderText("请先确认旋转方向...")
        lay.addWidget(self.r_status)
        return self.rotate_grp

    def _add_slider(self, grid, row, title, init_value):
        grid.addWidget(QLabel(title), row, 0)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 360); slider.setValue(init_value)
        slider.setTickInterval(30); slider.setTickPosition(QSlider.TicksBelow)
        slider.valueChanged.connect(self._on_3d_phase_slider_changed)
        grid.addWidget(slider, row, 1)
        label = QLabel(f"{init_value}°")
        label.setMinimumWidth(45)
        label.setStyleSheet("color:#ff8866; font-weight:bold;")
        grid.addWidget(label, row, 2)
        return slider, label

    def _on_3d_phase_slider_changed(self):
        self.r_3d_px_lb.setText(f"{self.r_3d_px_sl.value()}°")
        self.r_3d_py_lb.setText(f"{self.r_3d_py_sl.value()}°")
        self.r_3d_pz_lb.setText(f"{self.r_3d_pz_sl.value()}°")

    def _set_mode_ui(self, mode):
        self.vibrate_grp.setEnabled(mode == "vibrate")
        self.rotate_grp.setEnabled(mode == "rotate")
        if mode == "rotate": self.r_start.setEnabled(False)

    # ---- 日志 ----
    def log_hw(self, msg):
        print(f"[HW] {msg}")

    # ---- 槽函数 ----
    @Slot()
    def _on_mode_confirm(self):
        text = self.mode_combo.currentText()
        if text == "震荡磁场":
            self.ctrl.mode = "vibrate"; self._set_mode_ui("vibrate")
            self.statusBar().showMessage("✔ 震荡磁场模式激活")
        elif text == "旋转磁场":
            self.ctrl.mode = "rotate"; self.ctrl.direction_confirmed = False
            self._set_mode_ui("rotate"); self.field_view.refresh()
            self.statusBar().showMessage("✔ 旋转磁场激活 — 请先确认旋转方向")
        else:
            self.ctrl.mode = "none"; self._set_mode_ui("none")
            self.statusBar().showMessage("请选择工作模式")

    @Slot(int)
    def _on_input_mode_changed(self, idx):
        self.phase_stack.setCurrentIndex(idx)
        self.ctrl.direction_confirmed = False; self.r_start.setEnabled(False)
        self.lbl_px.setText("X: --"); self.lbl_py.setText("Y: --"); self.lbl_pz.setText("Z: --")
        self.field_view.refresh()

    @Slot()
    def _confirm_direction(self):
        idx = self.phase_stack.currentIndex()
        if idx == 0:
            ph = self.ctrl.confirm_direction_2d(self.r_plane.currentText(), self.r_angle.value())
            # 同步到硬件
            if self.hw_connected:
                resp = self.hw.set_phase_2d(self.r_plane.currentText(), self.r_angle.value())
                self.log_hw(f"SET_PHASE_2D: {resp}")
        else:
            ph = self.ctrl.confirm_direction_3d(
                self.r_3d_px_sl.value(), self.r_3d_py_sl.value(), self.r_3d_pz_sl.value()
            )
            if self.hw_connected:
                resp = self.hw.set_phase_3d(ph["X"] or 0, ph["Y"] or 0, ph["Z"] or 0)
                self.log_hw(f"SET_PHASE_3D: {resp}")

        self.lbl_px.setText(f"X: {self.ctrl._fmt(ph['X'])}")
        self.lbl_py.setText(f"Y: {self.ctrl._fmt(ph['Y'])}")
        self.lbl_pz.setText(f"Z: {self.ctrl._fmt(ph['Z'])}")
        self.r_start.setEnabled(True)
        self.field_view.refresh()
        self.statusBar().showMessage(f"✔ 方向确认  X:{self.ctrl._fmt(ph['X'])}  Y:{self.ctrl._fmt(ph['Y'])}  Z:{self.ctrl._fmt(ph['Z'])}")

    @Slot()
    def _start_vibrate(self):
        axis = self.v_axis.currentText()
        freq = self.v_freq.value()
        curr = self.v_curr.value()

        # 硬件
        if self.hw_connected:
            resp = self.hw.start_vibrate(axis, freq, curr)
            self.log_hw(f"VIBE: {resp}")
            self.v_status.setText(resp)
        else:
            ok, msg = self.ctrl.start_vibrate(axis, freq, curr)
            self.v_status.setText(msg)
        self.statusBar().showMessage(f"震荡磁场运行中 - {axis}轴 {freq}Hz {curr}A")

    @Slot()
    def _stop_vibrate(self):
        if self.hw_connected:
            resp = self.hw.stop_vibrate()
            self.log_hw(f"VIBE_STOP: {resp}")
        self.v_status.setText(self.ctrl.stop_vibrate())
        self.statusBar().showMessage("震荡磁场已停止")

    @Slot()
    def _start_rotate(self):
        freq = self.r_freq.value()
        curr = self.r_curr.value()

        if self.hw_connected:
            resp = self.hw.start_rotate(freq, curr)
            self.log_hw(f"ROTATE: {resp}")
            if resp.startswith("OK"):
                self.field_view.refresh()
            self.r_status.setText(resp)
        else:
            ok, msg = self.ctrl.start_rotate(freq, curr)
            if ok: self.field_view.refresh()
            self.r_status.setText(msg)
        self.statusBar().showMessage(f"旋转磁场运行中 {freq}Hz {curr}A")

    @Slot()
    def _stop_rotate(self):
        if self.hw_connected:
            resp = self.hw.stop_rotate()
            self.log_hw(f"ROTATE_STOP: {resp}")
        self.r_status.setText(self.ctrl.stop_rotate())
        self.r_start.setEnabled(False)
        self.field_view.refresh()
        self.statusBar().showMessage("旋转磁场已停止")

    @Slot()
    def _poll_hw_status(self):
        """定时轮询硬件状态"""
        if not self.hw_connected:
            return
        try:
            status = self.hw.query_status()
            # 更新状态栏
            if status["running"]:
                self.statusBar().showMessage(
                    f"⚡ {status['mode']} | {status['frequency']:.1f}Hz | {status['current']:.2f}A"
                )
        except Exception:
            pass


# ============================================================
# 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="亥姆霍兹线圈控制系统")
    parser.add_argument("--port", "-p", default="", help="ESP32-S3 串口 (如 COM3, /dev/ttyACM0)")
    parser.add_argument("--offline", action="store_true", help="纯离线模拟模式")
    args = parser.parse_args()

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
        QGroupBox:disabled { border-color: #333; color: #555; }
        QPushButton {
            background-color: #2d2d2d;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 5px 14px;
            min-width: 80px;
        }
        QPushButton:hover { background-color: #3c3c3c; }
        QPushButton:pressed { background-color: #505050; }
        QPushButton:disabled { color: #555; border-color: #333; }
        QDoubleSpinBox, QComboBox {
            background-color: #2a2a2a;
            border: 1px solid #555;
            border-radius: 3px;
            padding: 3px 5px;
            min-height: 24px;
        }
        QDoubleSpinBox:disabled, QComboBox:disabled { color: #555; border-color: #333; }
        QSlider::groove:horizontal { height: 6px; background: #444; border-radius: 3px; }
        QSlider::handle:horizontal { background: #ff8866; width: 14px; margin: -5px 0; border-radius: 7px; }
        QTextEdit { background-color: #232323; border: 1px solid #444; border-radius: 3px; }
        QStatusBar { background-color: #252525; border-top: 1px solid #444; color: #aaa; }
        QStackedWidget { background: transparent; }
        QScrollArea { background: transparent; border: none; }
    """)

    window = MainWindow(offline=args.offline, port=args.port)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
