"""Background writer for optional laser waveform/spectrum recording."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from services.laser_record_service import LaserRecordService
from models.data import AcquisitionChunk


class LaserRecordWorker(QObject):
    started = Signal(str)
    stopped = Signal(str)
    error = Signal(str)
    report_saved = Signal(str)

    def __init__(self, export_directory: Path) -> None:
        super().__init__()
        self.service = LaserRecordService(export_directory)

    @Slot(object)
    def start(self, metadata: dict = None) -> None:
        try:
            self.started.emit(str(self.service.start(metadata)))
        except Exception as exc:
            try:
                self.service.stop()
            except OSError:
                pass
            self.error.emit("激光频谱记录启动失败：%s" % exc)

    @Slot(object)
    def append_waveform(self, chunk: object) -> None:
        try:
            if isinstance(chunk, AcquisitionChunk):
                if chunk.laser is not None:
                    self.service.append_waveform(chunk.laser, chunk.hardware_frequencies)
            else:
                self.service.append_waveform(chunk)
        except Exception as exc:
            self.error.emit("激光原始信号记录失败：%s" % exc)

    @Slot(object)
    def append_spectrum(self, analysis: object) -> None:
        try:
            self.service.append_spectrum(analysis)
        except Exception as exc:
            self.error.emit("激光频谱记录失败：%s" % exc)

    @Slot(object)
    def append_measurement(self, record: dict) -> None:
        try:
            self.service.append_measurement(record)
        except Exception as exc:
            self.error.emit("激光动平衡记录失败：%s" % exc)

    @Slot(object)
    def save_balance_report(self, report: dict) -> None:
        try:
            self.report_saved.emit(str(self.service.export_balance_report(report)))
        except Exception as exc:
            self.error.emit("标定报告保存失败：%s" % exc)

    @Slot()
    def stop(self) -> None:
        try:
            directory = self.service.stop()
            self.stopped.emit(str(directory) if directory is not None else "")
        except Exception as exc:
            self.error.emit("激光频谱记录停止失败：%s" % exc)
