import sys
import numpy as np

import matplotlib
matplotlib.use("QtAgg")

import sys
import numpy as np

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QDoubleSpinBox,
    QComboBox, QPushButton, QTextEdit,
    QStackedWidget, QScrollArea, QSlider,
    QSizePolicy
)
from PySide6.QtCore import Qt, Slot

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib

matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "Arial Unicode MS"
]
matplotlib.rcParams["axes.unicode_minus"] = False


class HelmholtzController:
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

        self.actual_phase = {
            "X": 0.0,
            "Y": 90.0,
            "Z": None
        }

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

        self.vibrate_axis = axis
        self.vibrate_frequency = freq
        self.vibrate_current = current
        self.vibrate_running = True

        return True, (
            f"震荡磁场运行中\n"
            f"轴: {axis}\n"
            f"频率: {freq:.2f} Hz\n"
            f"电流: {current:.2f} A"
        )

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
            f"X:{self._fmt_phase(ph['X'])}  "
            f"Y:{self._fmt_phase(ph['Y'])}  "
            f"Z:{self._fmt_phase(ph['Z'])}"
        )

    def stop_rotate(self):
        self.rotate_running = False
        self.direction_confirmed = False
        return "旋转磁场已停止（需重新确认方向）"

    def _fmt_phase(self, value):
        if value is None:
            return "关闭"
        return f"{value:.1f}°"

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("亥姆霍兹线圈控制系统")
        self.setMinimumSize(1350, 1000)
        self.resize(1350, 1000)

        self.ctrl = HelmholtzController()

        self._build_ui()
        self._set_mode_ui("none")

        self.statusBar().showMessage("系统就绪 — 请选择工作模式")

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

        scroll.setWidget(inner)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

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

        self.v_start = QPushButton("▶  启动")
        self.v_start.setMinimumHeight(34)
        self.v_start.clicked.connect(self._start_vibrate)

        self.v_stop = QPushButton("■  停止")
        self.v_stop.setMinimumHeight(34)
        self.v_stop.clicked.connect(self._stop_vibrate)

        br.addWidget(self.v_start)
        br.addWidget(self.v_stop)

        lay.addLayout(br)

        self.v_status = QTextEdit()
        self.v_status.setReadOnly(True)
        self.v_status.setMinimumHeight(70)
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
        self.r_input_mode.currentIndexChanged.connect(
            self._on_input_mode_changed
        )

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

        hint0 = QLabel(
            "平面李萨如：A=sin(t), B=sin(t+φ)。0°/180°=直线，90°/270°=圆。"
        )
        hint0.setStyleSheet("color: #888; font-size: 11px;")
        g0.addWidget(hint0, 2, 0, 1, 2)

        self.phase_stack.addWidget(p0)

        p1 = QWidget()
        g1 = QGridLayout(p1)
        g1.setSpacing(8)
        g1.setContentsMargins(0, 6, 0, 6)

        self.r_3d_px_slider, self.r_3d_px_label = self._add_phase_slider(
            g1, 0, "X轴相位:", 0
        )
        self.r_3d_py_slider, self.r_3d_py_label = self._add_phase_slider(
            g1, 1, "Y轴相位:", 90
        )
        self.r_3d_pz_slider, self.r_3d_pz_label = self._add_phase_slider(
            g1, 2, "Z轴相位:", 45
        )

        hint1 = QLabel(
            "三维轨迹：X=sin(t+Px), Y=sin(t+Py), Z=sin(t+Pz)。滑块变化后需点击确认。"
        )
        hint1.setStyleSheet("color: #888; font-size: 11px;")
        g1.addWidget(hint1, 3, 0, 1, 3)

        self.phase_stack.addWidget(p1)
        phase_lay.addWidget(self.phase_stack)

        self.r_confirm = QPushButton("✔  确认旋转方向")
        self.r_confirm.setMinimumHeight(34)
        self.r_confirm.clicked.connect(self._confirm_direction)

        phase_lay.addWidget(self.r_confirm)

        ph_row = QHBoxLayout()
        ph_row.setSpacing(12)

        ph_row.addWidget(QLabel("实际相位:"))

        self.lbl_px = QLabel("X: --")
        self.lbl_py = QLabel("Y: --")
        self.lbl_pz = QLabel("Z: --")

        for lbl in (self.lbl_px, self.lbl_py, self.lbl_pz):
            lbl.setStyleSheet(
                "color:#ff8866; font-weight:bold; font-size:13px;"
            )
            lbl.setMinimumWidth(90)
            ph_row.addWidget(lbl)

        ph_row.addStretch()
        phase_lay.addLayout(ph_row)

        lay.addWidget(phase_grp)

        br = QHBoxLayout()
        br.setSpacing(8)

        self.r_start = QPushButton("▶  启动旋转")
        self.r_start.setMinimumHeight(36)
        self.r_start.clicked.connect(self._start_rotate)

        self.r_stop = QPushButton("■  停止")
        self.r_stop.setMinimumHeight(36)
        self.r_stop.clicked.connect(self._stop_rotate)

        br.addWidget(self.r_start)
        br.addWidget(self.r_stop)

        lay.addLayout(br)

        self.r_status = QTextEdit()
        self.r_status.setReadOnly(True)
        self.r_status.setMinimumHeight(90)
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

        self.statusBar().showMessage(
            f"✔ 方向已确认  "
            f"X:{self.ctrl._fmt_phase(ph['X'])}  "
            f"Y:{self.ctrl._fmt_phase(ph['Y'])}  "
            f"Z:{self.ctrl._fmt_phase(ph['Z'])}"
        )

    @Slot()
    def _start_vibrate(self):
        ok, msg = self.ctrl.start_vibrate(
            self.v_axis.currentText(),
            self.v_freq.value(),
            self.v_curr.value()
        )

        self.v_status.setText(msg)
        self.statusBar().showMessage(msg.split("\n")[0])

    @Slot()
    def _stop_vibrate(self):
        self.v_status.setText(self.ctrl.stop_vibrate())
        self.statusBar().showMessage("震荡磁场已停止")

    @Slot()
    def _start_rotate(self):
        ok, msg = self.ctrl.start_rotate(
            self.r_freq.value(),
            self.r_curr.value()
        )

        self.r_status.setText(msg)

        if ok:
            self.field_view.refresh()

        self.statusBar().showMessage(msg.split("\n")[0])

    @Slot()
    def _stop_rotate(self):
        self.r_status.setText(self.ctrl.stop_rotate())
        self.r_start.setEnabled(False)
        self.field_view.refresh()
        self.statusBar().showMessage("旋转磁场已停止")


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

        QDoubleSpinBox, QComboBox {
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