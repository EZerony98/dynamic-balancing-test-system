from __future__ import annotations

import time

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from models.data import AcquisitionConfig
from workers.acquisition_backends import validate_acquisition_config


def heading(title: str, subtitle: str) -> tuple[QLabel, QLabel]:
    h = QLabel(title); h.setObjectName("PageTitle")
    s = QLabel(subtitle); s.setObjectName("Hint"); s.setWordWrap(True)
    return h, s


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(20, 18, 20, 18); outer.setSpacing(14)
        h, s = heading("系统设置", "操作员使用基本参数；采集、传感器与通信参数建议只对系统管理员开放。")
        outer.addWidget(h); outer.addWidget(s)
        tabs = QTabWidget(); outer.addWidget(tabs, 1)

        basic = QWidget(); f = QFormLayout(basic); f.setContentsMargins(22, 22, 22, 22)
        self.average = QSpinBox(); self.average.setRange(1, 100); self.average.setValue(5)
        self.delay = QDoubleSpinBox(); self.delay.setRange(0, 120); self.delay.setValue(3); self.delay.setSuffix(" s")
        self.balance_mode = QComboBox(); self.balance_mode.addItems(["双面", "单面"])
        self.correction = QComboBox(); self.correction.addItems(["加重", "减重"])
        self.direction = QComboBox(); self.direction.addItems(["顺时针", "逆时针"])
        self.speed_tolerance = QDoubleSpinBox(); self.speed_tolerance.setRange(0.1, 50); self.speed_tolerance.setValue(2.0); self.speed_tolerance.setSuffix(" %")
        for label, widget in (("测量平均次数", self.average), ("启动后等待时间", self.delay), ("平衡模式", self.balance_mode),
                              ("修正方式", self.correction), ("角度方向", self.direction), ("转速稳定容差", self.speed_tolerance)):
            f.addRow(label, widget)
        tabs.addTab(basic, "基本设置")

        self.left_sensor_type = QComboBox()
        self.right_sensor_type = QComboBox()
        for combo in (self.left_sensor_type, self.right_sensor_type):
            combo.addItem("压电陶瓷 + 放大板", "piezo")
            combo.addItem("IEPE 加速度传感器（PCB 353B15）", "iepe_accel")
            combo.addItem("未安装", "none")

        acquisition = QWidget(); a = QFormLayout(acquisition); a.setContentsMargins(22, 22, 22, 22)
        self.mode = QComboBox()
        self.mode.addItem("自动：按左右传感器设置启用采集设备", "auto")
        self.mode.setEnabled(False)
        self.labjack_connection = QComboBox(); self.labjack_connection.addItems(["USB", "ETHERNET"])
        self.rate = QSpinBox(); self.rate.setRange(100, 100000); self.rate.setValue(30000); self.rate.setSuffix(" S/s/ch")
        self.scans = QSpinBox(); self.scans.setRange(128, 65536); self.scans.setValue(6000)
        self.ppr = QSpinBox(); self.ppr.setRange(1, 1000); self.ppr.setValue(1)
        self.edge = QComboBox(); self.edge.addItems(["下降沿", "上升沿"])
        self.lj_left_channel = QLineEdit("AIN0")
        self.lj_right_channel = QLineEdit("AIN3")
        self.lj_tach_channel = QLineEdit("FIO3")
        self.ni_module = QLineEdit("cDAQ1Mod1")
        self.ni_left_channel = QLineEdit("ai0")
        self.ni_right_channel = QLineEdit("ai1")
        self.ni_left_sensitivity = QDoubleSpinBox(); self.ni_left_sensitivity.setRange(.001, 10000); self.ni_left_sensitivity.setDecimals(4); self.ni_left_sensitivity.setValue(10.0); self.ni_left_sensitivity.setSuffix(" mV/g")
        self.ni_right_sensitivity = QDoubleSpinBox(); self.ni_right_sensitivity.setRange(.001, 10000); self.ni_right_sensitivity.setDecimals(4); self.ni_right_sensitivity.setValue(10.0); self.ni_right_sensitivity.setSuffix(" mV/g")
        self.ni_iepe_current = QDoubleSpinBox(); self.ni_iepe_current.setRange(2.0, 2.0); self.ni_iepe_current.setDecimals(1); self.ni_iepe_current.setValue(2.0); self.ni_iepe_current.setSuffix(" mA")
        self.ni_note = QLabel("只有某一侧选择 IEPE 加速度传感器时才启用 NI-9234；LabJack 始终负责 FIO3 光电转速，并同时采集仍为压电陶瓷的另一侧。")
        self.ni_note.setObjectName("Hint"); self.ni_note.setWordWrap(True)
        self.check = QLabel(); self.check.setWordWrap(True)
        self.rate.valueChanged.connect(self.validate)
        self.scans.valueChanged.connect(self.validate)
        self.left_sensor_type.currentIndexChanged.connect(self._mode_changed)
        self.right_sensor_type.currentIndexChanged.connect(self._mode_changed)
        self.ni_left_sensitivity.valueChanged.connect(self.validate)
        self.ni_right_sensitivity.valueChanged.connect(self.validate)
        for label, widget in (("设备启用逻辑", self.mode), ("LabJack 连接", self.labjack_connection),
                              ("扫描率", self.rate), ("每次读取扫描数", self.scans),
                              ("LabJack 左通道", self.lj_left_channel), ("LabJack 右通道", self.lj_right_channel),
                              ("LabJack 光电转速", self.lj_tach_channel), ("转速每转脉冲数 PPR", self.ppr),
                              ("光电有效边沿", self.edge), ("NI 模块名称", self.ni_module),
                              ("NI 左物理通道", self.ni_left_channel), ("NI 左灵敏度", self.ni_left_sensitivity),
                              ("NI 右物理通道", self.ni_right_channel), ("NI 右灵敏度", self.ni_right_sensitivity),
                              ("NI IEPE 激励", self.ni_iepe_current),
                              ("NI 说明", self.ni_note),
                              ("配置检查", self.check)):
            a.addRow(label, widget)
        tabs.addTab(acquisition, "采集与转速")

        sensor = QWidget(); sf = QFormLayout(sensor); sf.setContentsMargins(22, 22, 22, 22)
        self.gain = QComboBox(); self.gain.addItems(["1", "1.99", "4.98", "10", "25.7"])
        self.left_sensitivity = QDoubleSpinBox(); self.left_sensitivity.setRange(0.000001, 1000000); self.left_sensitivity.setDecimals(6); self.left_sensitivity.setValue(1)
        self.right_sensitivity = QDoubleSpinBox(); self.right_sensitivity.setRange(0.000001, 1000000); self.right_sensitivity.setDecimals(6); self.right_sensitivity.setValue(1)
        self.sensor_note = QLabel("压电陶瓷走 LabJack 并保留 mV；353B15 走 NI-9234，标称灵敏度 10 mV/g。左右选用不同采集设备时不输出两路相对相位。")
        self.sensor_note.setObjectName("Hint"); self.sensor_note.setWordWrap(True)
        for label, widget in (("左传感器", self.left_sensor_type), ("右传感器", self.right_sensor_type),
                              ("压电放大板硬件增益", self.gain), ("左压电换算比例", self.left_sensitivity),
                              ("右压电换算比例", self.right_sensitivity), ("说明", self.sensor_note)):
            sf.addRow(label, widget)
        tabs.addTab(sensor, "传感器")

        communication = QWidget(); cf = QFormLayout(communication); cf.setContentsMargins(22, 22, 22, 22)
        self.port = QComboBox(); self.port.addItems(["未启用", "串口控制器", "PLC / Modbus"])
        self.baud = QComboBox(); self.baud.addItems(["9600", "19200", "57600", "115200"]); self.baud.setCurrentText("115200")
        self.start_command = QLineEdit(); self.start_command.setPlaceholderText("启动命令，待设备协议确认")
        self.result_command = QLineEdit(); self.result_command.setPlaceholderText("结果发送格式，待设备协议确认")
        for label, widget in (("外部控制方式", self.port), ("波特率", self.baud), ("启动指令", self.start_command), ("测量结果指令", self.result_command)):
            cf.addRow(label, widget)
        tabs.addTab(communication, "外部通信")

        buttons = QHBoxLayout(); buttons.addStretch(); reset = QPushButton("恢复本页默认值"); save = QPushButton("保存设置"); save.setObjectName("Primary")
        save.clicked.connect(self.save); buttons.addWidget(reset); buttons.addWidget(save); outer.addLayout(buttons)
        self.load()
        self.validate()

    def load(self) -> None:
        q = QSettings("Boreasa", "BSI5")
        self.labjack_connection.setCurrentText(str(q.value("labjack_connection", "USB")))
        self.rate.setValue(int(q.value("rate", 30000)))
        self.scans.setValue(int(q.value("scans", 6000)))
        self.ppr.setValue(int(q.value("ppr", 1)))
        self.average.setValue(int(q.value("average", 5)))
        self.delay.setValue(float(q.value("delay", 3.0)))
        self.speed_tolerance.setValue(float(q.value("speed_tolerance", 2.0)))
        self.gain.setCurrentText(str(q.value("gain", "1")))
        self.edge.setCurrentText(str(q.value("edge", "下降沿")))
        self.lj_left_channel.setText(str(q.value("labjack_left_channel", "AIN0")))
        self.lj_right_channel.setText(str(q.value("labjack_right_channel", "AIN3")))
        self.lj_tach_channel.setText(str(q.value("labjack_tach_channel", "FIO3")))
        self.ni_module.setText(str(q.value("ni_module", "cDAQ1Mod1")))
        self.ni_left_channel.setText(str(q.value("ni_left_channel", "ai0")))
        self.ni_right_channel.setText(str(q.value("ni_right_channel", "ai1")))
        left_type = str(q.value("left_sensor_type", "piezo"))
        right_type = str(q.value("right_sensor_type", "piezo"))
        self.left_sensor_type.setCurrentIndex(max(0, self.left_sensor_type.findData(left_type)))
        self.right_sensor_type.setCurrentIndex(max(0, self.right_sensor_type.findData(right_type)))
        self.ni_left_sensitivity.setValue(float(q.value("ni_left_sensitivity_mv_g", 10.0)))
        self.ni_right_sensitivity.setValue(float(q.value("ni_right_sensitivity_mv_g", 10.0)))
        self._mode_changed()

    def acquisition_config(self) -> AcquisitionConfig:
        left_type = str(self.left_sensor_type.currentData())
        right_type = str(self.right_sensor_type.currentData())
        ni_required = "iepe_accel" in (left_type, right_type)
        connection = self.labjack_connection.currentText().lower()
        return AcquisitionConfig(
            backend="ni9234" if ni_required else f"labjack_{connection}",
            scan_rate=float(self.rate.value()),
            samples_per_read=self.scans.value(),
            labjack_left_channel=self.lj_left_channel.text().strip(),
            labjack_right_channel=self.lj_right_channel.text().strip(),
            labjack_tach_channel=self.lj_tach_channel.text().strip(),
            left_sensor_type=left_type,
            right_sensor_type=right_type,
            tach_ppr=self.ppr.value(),
            tach_edge="falling" if self.edge.currentText() == "下降沿" else "rising",
            ni_module=self.ni_module.text().strip(),
            ni_left_channel=self.ni_left_channel.text().strip(),
            ni_right_channel=self.ni_right_channel.text().strip(),
            ni_left_sensitivity_mv_g=self.ni_left_sensitivity.value(),
            ni_right_sensitivity_mv_g=self.ni_right_sensitivity.value(),
            ni_iepe_current_ma=self.ni_iepe_current.value(),
            ni_tach_connection=self.labjack_connection.currentText(),
        )

    def _mode_changed(self, _index: int | None = None) -> None:
        ni = "iepe_accel" in (self.left_sensor_type.currentData(), self.right_sensor_type.currentData())
        self.mode.setItemText(0, "自动：NI-9234 + LabJack" if ni else "自动：仅 LabJack T7")
        for widget in (self.ni_module, self.ni_left_channel, self.ni_right_channel,
                       self.ni_left_sensitivity, self.ni_right_sensitivity, self.ni_iepe_current, self.ni_note):
            widget.setEnabled(ni)
        self.ni_left_channel.setEnabled(self.left_sensor_type.currentData() == "iepe_accel")
        self.ni_left_sensitivity.setEnabled(self.left_sensor_type.currentData() == "iepe_accel")
        self.ni_right_channel.setEnabled(self.right_sensor_type.currentData() == "iepe_accel")
        self.ni_right_sensitivity.setEnabled(self.right_sensor_type.currentData() == "iepe_accel")
        self.lj_left_channel.setEnabled(self.left_sensor_type.currentData() == "piezo")
        self.lj_right_channel.setEnabled(self.right_sensor_type.currentData() == "piezo")
        for widget in (self.lj_tach_channel, self.ppr, self.edge):
            widget.setEnabled(True)
        self.rate.setMaximum(51200 if ni else 100000)
        self.rate.setMinimum(1652 if ni else 100)
        self.validate()

    def validate(self) -> None:
        ok, msg = validate_acquisition_config(self.acquisition_config())
        self.check.setText(("✓ " if ok else "✗ ") + msg)
        self.check.setObjectName("Success" if ok else "Danger")
        self.check.style().unpolish(self.check); self.check.style().polish(self.check)

    def save(self) -> None:
        q = QSettings("Boreasa", "BSI5")
        for key, value in {
            "labjack_connection": self.labjack_connection.currentText(),
            "left_sensor_type": self.left_sensor_type.currentData(),
            "right_sensor_type": self.right_sensor_type.currentData(),
            "rate": self.rate.value(), "scans": self.scans.value(), "ppr": self.ppr.value(),
            "average": self.average.value(), "delay": self.delay.value(), "speed_tolerance": self.speed_tolerance.value(),
            "gain": self.gain.currentText(), "edge": self.edge.currentText(),
            "labjack_left_channel": self.lj_left_channel.text().strip(),
            "labjack_right_channel": self.lj_right_channel.text().strip(),
            "labjack_tach_channel": self.lj_tach_channel.text().strip(),
            "ni_module": self.ni_module.text().strip(), "ni_left_channel": self.ni_left_channel.text().strip(),
            "ni_right_channel": self.ni_right_channel.text().strip(),
            "ni_left_sensitivity_mv_g": self.ni_left_sensitivity.value(),
            "ni_right_sensitivity_mv_g": self.ni_right_sensitivity.value(),
        }.items(): q.setValue(key, value)
        q.sync(); QMessageBox.information(self, "设置已保存", "本机设置已保存。算法相关参数仍需标定后启用。")


class TemplatePage(QWidget):
    def __init__(self):
        super().__init__(); outer = QVBoxLayout(self); outer.setContentsMargins(20, 18, 20, 18); outer.setSpacing(14)
        h, s = heading("测量模板与 QC", "QC管理员为不同产品预设转速、稳定条件和合格阈值；操作员只调用模板。")
        outer.addWidget(h); outer.addWidget(s)
        body = QHBoxLayout()
        list_panel = QFrame(objectName="Panel"); left = QVBoxLayout(list_panel)
        title = QLabel("模板列表"); title.setObjectName("SectionTitle")
        self.table = QTableWidget(3, 5)
        self.table.setHorizontalHeaderLabels(["模板", "目标RPM", "左限值", "右限值", "状态"])
        rows = (("样机默认模板", "9000", "--", "--", "待标定"), ("电机转子模板", "7500", "--", "--", "草稿"), ("风机叶轮模板", "3000", "--", "--", "草稿"))
        for r, values in enumerate(rows):
            for c, value in enumerate(values): self.table.setItem(r, c, QTableWidgetItem(value))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        left.addWidget(title); left.addWidget(self.table); body.addWidget(list_panel, 2)
        edit_panel = QFrame(objectName="Panel"); form = QFormLayout(edit_panel); form.setContentsMargins(20, 20, 20, 20)
        self.name = QLineEdit("样机默认模板")
        self.target_rpm = QSpinBox(); self.target_rpm.setRange(1, 200000); self.target_rpm.setValue(9000); self.target_rpm.setSuffix(" RPM")
        self.tolerance = QDoubleSpinBox(); self.tolerance.setRange(.1, 50); self.tolerance.setValue(2); self.tolerance.setSuffix(" %")
        self.left_limit = QDoubleSpinBox(); self.left_limit.setRange(0, 100000); self.left_limit.setSuffix(" mV")
        self.right_limit = QDoubleSpinBox(); self.right_limit.setRange(0, 100000); self.right_limit.setSuffix(" mV")
        self.average = QSpinBox(); self.average.setRange(1, 100); self.average.setValue(5)
        self.delay = QDoubleSpinBox(); self.delay.setRange(0, 120); self.delay.setValue(3); self.delay.setSuffix(" s")
        self.algorithm = QComboBox(); self.algorithm.addItems(["1X 双面试重法（待确认）", "单面等效法（待确认）"])
        save = QPushButton("保存模板"); save.setObjectName("Primary"); save.clicked.connect(self.save)
        for label, widget in (("模板名称", self.name), ("目标转速", self.target_rpm), ("允许波动", self.tolerance),
                              ("左面合格上限", self.left_limit), ("右面合格上限", self.right_limit), ("平均次数", self.average),
                              ("启动延时", self.delay), ("算法", self.algorithm)):
            form.addRow(label, widget)
        form.addRow("", save); body.addWidget(edit_panel, 1); outer.addLayout(body, 1)

    def save(self) -> None:
        q = QSettings("Boreasa", "BSI5")
        prefix = "template/default/"
        values = {
            "name": self.name.text().strip() or "未命名模板",
            "target_rpm": self.target_rpm.value(),
            "tolerance": self.tolerance.value(),
            "left_limit": self.left_limit.value(),
            "right_limit": self.right_limit.value(),
            "average": self.average.value(),
            "delay": self.delay.value(),
            "algorithm": self.algorithm.currentText(),
        }
        for key, value in values.items(): q.setValue(prefix + key, value)
        q.sync()
        display = (values["name"], str(values["target_rpm"]), f"{values['left_limit']:.3f} mV",
                   f"{values['right_limit']:.3f} mV", "待标定")
        for col, value in enumerate(display): self.table.setItem(0, col, QTableWidgetItem(value))
        QMessageBox.information(self, "模板已保存", "模板参数已保存为草稿；完成试重标定后才能用于合格判定。")


class AccessPage(QWidget):
    def __init__(self):
        super().__init__(); outer = QVBoxLayout(self); outer.setContentsMargins(20, 18, 20, 18); outer.setSpacing(14)
        h, s = heading("用户与权限", "按操作员、QC管理员和系统管理员分离生产操作、阈值管理与硬件配置。")
        outer.addWidget(h); outer.addWidget(s)
        table = QTableWidget(3, 5)
        table.setHorizontalHeaderLabels(["角色", "生产测量", "QC模板/阈值", "硬件与通信", "审计记录"])
        rows = (("操作员", "允许", "只读", "禁止", "记录姓名和时间"), ("QC管理员", "允许", "允许", "只读", "记录修改前后值"),
                ("系统管理员", "允许", "允许", "允许", "完整记录"))
        for r, values in enumerate(rows):
            for c, value in enumerate(values): table.setItem(r, c, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        outer.addWidget(table)
        panel = QFrame(objectName="Panel"); layout = QFormLayout(panel); layout.setContentsMargins(20, 20, 20, 20)
        operator = QLineEdit("调试员"); role = QComboBox(); role.addItems(["操作员", "QC管理员", "系统管理员"])
        password = QLineEdit(); password.setEchoMode(QLineEdit.EchoMode.Password); password.setPlaceholderText("正式部署时接入密码哈希和账号库")
        layout.addRow("当前用户名", operator); layout.addRow("当前角色", role); layout.addRow("密码", password)
        outer.addWidget(panel); outer.addStretch()


class HistoryPage(QWidget):
    def __init__(self, repo):
        super().__init__(); self.repo = repo
        outer = QVBoxLayout(self); outer.setContentsMargins(20, 18, 20, 18); outer.setSpacing(14)
        h, s = heading("测试记录", "生产记录按时间倒序保存，可继续扩展产品编号、操作员、模板、QC结果和原始数据文件。")
        outer.addWidget(h); outer.addWidget(s)
        filters = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText("搜索产品编号 / 操作员 / 文件名")
        export = QPushButton("导出筛选结果"); filters.addWidget(self.search, 1); filters.addWidget(export); outer.addLayout(filters)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["时间", "模式", "通道", "RMS", "Vpp", "主频", "主峰", "文件"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        outer.addWidget(self.table, 1)

    def refresh(self):
        rows = self.repo.rows(); self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [time.strftime("%F %T", time.localtime(row[0])), *row[1:]]
            for c, value in enumerate(values): self.table.setItem(r, c, QTableWidgetItem(str(value)))


class LogPage(QWidget):
    def __init__(self):
        super().__init__(); outer = QVBoxLayout(self); outer.setContentsMargins(20, 18, 20, 18); outer.setSpacing(14)
        h, s = heading("系统日志", "记录连接、采集、警告、导出和参数修改。")
        outer.addWidget(h); outer.addWidget(s)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True); outer.addWidget(self.text, 1)

    def add(self, msg): self.text.appendPlainText(time.strftime("%H:%M:%S  ") + msg)
