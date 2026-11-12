"""Background writer for one fixed-duration vibration measurement."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from models.data import AcquisitionChunk
from services.piezo_record_service import PiezoRecordService


class PiezoRecordWorker(QObject):
    started = Signal(str)
    stopped = Signal(str)
    error = Signal(str)

    def __init__(self, export_directory: Path) -> None:
        super().__init__()
        self.service = PiezoRecordService(export_directory)

    @Slot(object)
    def start(self, metadata: dict) -> None:
        try:
            self.started.emit(str(self.service.start(metadata)))
        except Exception as exc:
            self.error.emit("双路压电记录启动失败：%s" % exc)

    @Slot(object)
    def append_waveform(self, chunk: AcquisitionChunk) -> None:
        try:
            if chunk.piezo is not None:
                complete = self.service.append_waveform(
                    chunk.piezo,
                    chunk.hardware_frequencies.get("motor_hall", 0.0),
                    chunk.hardware_raw_frequencies.get(
                        "motor_hall", chunk.hardware_frequencies.get("motor_hall", 0.0)
                    ),
                )
                if complete:
                    directory = self.service.stop()
                    self.stopped.emit(str(directory) if directory else "")
        except Exception as exc:
            self.error.emit("双路压电原始信号记录失败：%s" % exc)

    @Slot(object)
    def append_spectrum(self, analysis: object) -> None:
        try:
            self.service.append_spectrum(analysis)
        except Exception as exc:
            self.error.emit("双路压电频谱记录失败：%s" % exc)

    @Slot(object)
    def append_one_x(self, result: object) -> None:
        try:
            self.service.append_one_x(result)
        except Exception as exc:
            self.error.emit("压电 1X 记录失败：%s" % exc)

    @Slot()
    def stop(self) -> None:
        try:
            directory = self.service.stop()
            self.stopped.emit(str(directory) if directory else "")
        except Exception as exc:
            self.error.emit("双路压电记录停止失败：%s" % exc)
