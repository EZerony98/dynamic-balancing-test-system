from __future__ import annotations
from PySide6.QtCore import QObject, Signal, Slot, QTimer

from models.data import AcquisitionConfig
from workers.acquisition_backends import AcquisitionBackend, AcquisitionError, create_backend

class AcquisitionWorker(QObject):
    connected = Signal(dict); disconnected = Signal(); stream_started = Signal(float)
    data_ready = Signal(object); warning = Signal(str); error = Signal(str); stream_stopped = Signal()
    def __init__(self):
        super().__init__(); self.timer = None; self.backend: AcquisitionBackend | None = None; self.running = False
    @Slot(object)
    def start_device(self, config: AcquisitionConfig):
        if self.running: return
        try:
            self.backend = create_backend(config)
            device = self.backend.start()
        except Exception as exc:
            self.backend = None
            self.error.emit(str(exc))
            return
        self.running = True
        self.connected.emit(device)
        self.timer = QTimer(self); self.timer.timeout.connect(self._read); self.timer.start(0)
        self.stream_started.emit(self.backend.actual_scan_rate)
    @Slot()
    def _read(self):
        if not self.running or self.backend is None:
            return
        try:
            self.data_ready.emit(self.backend.read())
        except Exception as exc:
            self.error.emit(str(exc))
            self.stop()
    @Slot()
    def stop(self):
        self.running = False
        if self.timer: self.timer.stop(); self.timer.deleteLater(); self.timer = None
        if self.backend:
            self.backend.close(); self.backend = None
        self.stream_stopped.emit(); self.disconnected.emit()
