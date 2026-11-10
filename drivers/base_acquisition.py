"""Common contract for the two mutually-exclusive T7 acquisition modes."""

from abc import ABC, abstractmethod

from models.data import AcquisitionChunk


class AcquisitionSource(ABC):
    @abstractmethod
    def open(self, mode: str = "balancing") -> dict:
        """Start the five-item or dual-piezo stream, with FIO3 tachometer."""

    @abstractmethod
    def read(self) -> AcquisitionChunk:
        """Read one chunk from the currently active stream mode."""

    @abstractmethod
    def close(self) -> None:
        """Stop the stream and close the T7 handle."""
