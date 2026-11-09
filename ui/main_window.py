from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMetaObject, QThread, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from repositories.history import HistoryRepository
from services.spectrum import spectrum
from ui.pages.dashboard import BalancePage, CalibrationPage, ProductionPage, SpectrumPage
from ui.pages.support import AccessPage, HistoryPage, LogPage, SettingsPage, TemplatePage
from ui.theme import RED
from ui.widgets.balance_widgets import StatusPill
from workers.acquisition import AcquisitionWorker
from workers.acquisition_backends import validate_acquisition_config


class MainWindow(QMainWindow):
    start_worker = Signal(object)
    stop_worker = Signal()

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.last = None
        self.repo = HistoryRepository(str(root / "data/history.db"))
        self.setWindowTitle("动平衡测试系统")
        self.setWindowIcon(QIcon(str(root / "assets/logo.svg")))
        self.resize(1550, 940)
        self.setMinimumSize(1180, 760)
        self._ui()
        self._worker()
        self.statusBar().showMessage("系统就绪 · 请在系统设置中选择采集设备")

    def _ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        outer.addWidget(self._toolbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QListWidget(objectName="Navigation")
        self.nav.setFixedWidth(210)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()

        self.production = ProductionPage()
        self.balance = BalancePage()
        self.spectrum_page = SpectrumPage()
        self.calibration = CalibrationPage()
        self.templates = TemplatePage()
        self.history = HistoryPage(self.repo)
        self.settings = SettingsPage()
        self.access = AccessPage()
        self.logs = LogPage()
        pages = [
            ("生产测量", self.production),
            ("双面动平衡", self.balance),
            ("频谱诊断", self.spectrum_page),
            ("试重标定", self.calibration),
            ("测量模板与 QC", self.templates),
            ("测试记录", self.history),
            ("系统设置", self.settings),
            ("用户与权限", self.access),
            ("系统日志", self.logs),
        ]
        for name, page in pages:
            item = QListWidgetItem(name)
            item.setToolTip(name)
            self.nav.addItem(item)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._page)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)

        self.start.clicked.connect(self.begin)
        self.stop.clicked.connect(self.halt)
        self.export.clicked.connect(self.export_data)
        self.production.product_changed.connect(self._product_changed)

    def _header(self) -> QFrame:
        header = QFrame(objectName="Header")
        header.setFixedHeight(74)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        logo = QLabel()
        pixmap = QPixmap(str(self.root / "assets/logo.svg"))
        logo.setPixmap(pixmap.scaled(76, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        logo.setFixedWidth(82)
        title_box = QVBoxLayout()
        title = QLabel("动平衡测试系统")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Dual-plane balancing measurement system")
        subtitle.setObjectName("Hint")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addWidget(logo)
        layout.addLayout(title_box)
        layout.addStretch()
        self.device = QLabel("采集设备未连接")
        self.device.setObjectName("Hint")
        self.connection = StatusPill("未采集", "neutral")
        operator = QLabel("调试员 · 系统管理员")
        operator.setObjectName("Hint")
        layout.addWidget(self.device)
        layout.addWidget(self.connection)
        layout.addWidget(operator)
        return header

    def _toolbar(self) -> QFrame:
        toolbar = QFrame(objectName="Toolbar")
        toolbar.setFixedHeight(64)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(20, 10, 20, 10)
        self.context = QLabel("当前任务：生产测量")
        self.context.setObjectName("SectionTitle")
        self.product = QLabel("产品编号：临时测量")
        self.product.setObjectName("Hint")
        self.start = QPushButton("开始测量  F3")
        self.start.setObjectName("Primary")
        self.stop = QPushButton("停止  Del")
        self.stop.setObjectName("DangerButton")
        self.stop.setEnabled(False)
        self.export = QPushButton("导出当前数据")
        layout.addWidget(self.context)
        layout.addWidget(self.product)
        layout.addStretch()
        layout.addWidget(self.start)
        layout.addWidget(self.stop)
        layout.addWidget(self.export)
        return toolbar

    def _worker(self) -> None:
        self.thread = QThread(self)
        self.worker = AcquisitionWorker()
        self.worker.moveToThread(self.thread)
        self.start_worker.connect(self.worker.start_device)
        self.stop_worker.connect(self.worker.stop)
        self.worker.data_ready.connect(self.consume)
        self.worker.connected.connect(self._connected)
        self.worker.stream_started.connect(self._started)
        self.worker.stream_stopped.connect(self._stopped)
        self.worker.warning.connect(self.logs.add)
        self.worker.error.connect(self._error)
        self.thread.start()

    def _connected(self, device: dict) -> None:
        self.device.setText(f"{device['model']} · {device['serial']} · {device.get('connection', '')}")
        self.connection.set_state("ready", "设备已连接")

    def _page(self, index: int) -> None:
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        self.context.setText(f"当前任务：{self.nav.item(index).text()}")
        if self.stack.currentWidget() is self.history:
            self.history.refresh()

    def _product_changed(self, serial: str) -> None:
        self.product.setText(f"产品编号：{serial.strip() or '临时测量'}")

    def begin(self) -> None:
        config = self.settings.acquisition_config()
        ok, message = validate_acquisition_config(config)
        if not ok:
            QMessageBox.warning(self, "采集配置无效", message)
            return
        self.start.setEnabled(False)
        self.connection.set_state("warning", "正在连接")
        self.active_config = config
        self.start_worker.emit(config)

    def halt(self) -> None:
        self.stop_worker.emit()

    def _started(self, rate: float) -> None:
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.connection.set_state("ready", "采集中")
        self.production.set_running(True)
        mode = "NI-9234 加速度 + LabJack 压电/转速" if getattr(self, "active_config", None) and self.active_config.backend == "ni9234" else "LabJack 压电 + 转速"
        self.statusBar().showMessage(f"采集中 · {rate:,.0f} S/s/ch · {mode} · 丢失 0")
        self.logs.add("采集已启动")

    def _stopped(self) -> None:
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        self.connection.set_state("neutral", "设备已停止")
        self.production.set_running(False)
        self.statusBar().showMessage("采集已停止 · 数据可导出")
        self.logs.add("采集已安全停止")

    def _error(self, message: str) -> None:
        self.logs.add(message)
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        self.connection.set_state("danger", "采集错误")
        self.statusBar().showMessage(message)
        QMessageBox.critical(self, "采集失败", message)

    def consume(self, chunk) -> None:
        self.last = chunk
        for page in (self.production, self.balance, self.spectrum_page, self.calibration):
            page.update_chunk(chunk)

    def export_data(self) -> None:
        if self.last is None:
            QMessageBox.information(self, "暂无数据", "请先完成一次采集。")
            return
        out = self.root / "exports"
        out.mkdir(exist_ok=True)
        serial = re.sub(r"[^0-9A-Za-z_-]+", "_", self.production.serial.text().strip()) or "temporary"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out / f"{serial}_{stamp}.csv"
        channels = [key for key in ("AIN0", "AIN1", "TACH") if key in self.last.channels]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_s", *[f"{name}[{self.last.channel_units.get(name, '')}]" for name in channels]])
            # NI-DAQmx and LabJack are read by separate drivers in hybrid mode.
            # A read can occasionally return a slightly different number of
            # scans, so export only the common complete interval.
            count = min(len(self.last.channels[name]) for name in channels)
            for i in range(count):
                writer.writerow([i / self.last.scan_rate, *[self.last.channels[name][i] for name in channels]])
        record_mode = "加速度/压电组合" if "g" in self.last.channel_units.values() else "双路压电"
        for channel in ("AIN0", "AIN1"):
            _, _, metrics = spectrum(self.last.channels[channel], self.last.scan_rate)
            self.repo.add(record_mode, channel, metrics, str(path))
        self.logs.add(f"当前数据已导出：{path}")
        QMessageBox.information(self, "导出完成", str(path))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F3 and self.start.isEnabled():
            self.begin()
            return
        if event.key() == Qt.Key.Key_Delete and self.stop.isEnabled():
            self.halt()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.worker, "stop", Qt.ConnectionType.BlockingQueuedConnection)
        self.thread.quit()
        self.thread.wait(2000)
        event.accept()
