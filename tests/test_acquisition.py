import numpy as np

from models.data import AcquisitionConfig, StreamChunk
from ui.pages.dashboard import diagnostics
from workers.acquisition_backends import NI9234Backend, validate_acquisition_config


def test_ni_353b15_single_left_configuration_is_valid():
    config = AcquisitionConfig(
        backend="ni9234",
        scan_rate=30_000,
        samples_per_read=6_000,
        left_sensor_type="iepe_accel",
        right_sensor_type="none",
        ni_left_sensitivity_mv_g=10.0,
        ni_iepe_current_ma=2.0,
    )
    assert validate_acquisition_config(config)[0]


def test_requires_at_least_one_installed_sensor():
    config = AcquisitionConfig(
        backend="labjack_usb",
        left_sensor_type="none",
        right_sensor_type="none",
    )
    ok, message = validate_acquisition_config(config)
    assert not ok
    assert "不能同时" in message


def test_ni_single_channel_read_shape():
    matrix = NI9234Backend._matrix([1.0, 2.0, 3.0], 1)
    assert matrix.shape == (1, 3)


def test_accelerometer_without_tach_uses_candidate_peak_but_no_fake_rpm():
    fs = 30_000.0
    t = np.arange(6_000) / fs
    left = 2.0 * np.sin(2 * np.pi * 125.0 * t)
    chunk = StreamChunk(
        channels={"AIN0": left, "AIN1": np.zeros_like(left)},
        scan_rate=fs,
        channel_units={"AIN0": "g", "AIN1": "g"},
        channel_sources={"AIN0": "cDAQ1Mod1/ai0", "AIN1": "未安装"},
        acquisition_backend="ni9234",
    )
    result = diagnostics(chunk)
    assert abs(result.reference_hz - 125.0) < 5.0
    assert np.isnan(result.rpm)
    assert result.left_unit == "g"
    assert result.left_active
    assert not result.right_active
    assert not result.has_tach_reference


def test_labjack_defaults_use_ain0_ain3_and_fio3():
    config = AcquisitionConfig()
    assert (config.labjack_left_channel, config.labjack_right_channel, config.labjack_tach_channel) == (
        "AIN0", "AIN3", "FIO3"
    )


def test_one_accelerometer_and_one_piezo_selects_hybrid_contract():
    config = AcquisitionConfig(
        backend="ni9234",
        left_sensor_type="iepe_accel",
        right_sensor_type="piezo",
    )
    ok, message = validate_acquisition_config(config)
    assert ok
    assert "NI-9234" in message and "LabJack" in message


def test_hybrid_read_merges_ni_acceleration_labjack_piezo_and_tach():
    config = AcquisitionConfig(
        backend="ni9234",
        scan_rate=30_000,
        samples_per_read=4,
        left_sensor_type="iepe_accel",
        right_sensor_type="piezo",
    )
    backend = NI9234Backend(config)

    class FakeTask:
        def read(self, **_kwargs):
            return [1.0, 2.0, 3.0, 4.0]

    class FakeLabJack:
        actual_scan_rate = 30_000.0

        def read(self):
            return {
                "AIN1": np.array([0.1, 0.2, 0.3, 0.4]),
                "TACH": np.array([0.0, 1.0, 0.0, 1.0]),
            }, 2, 3, 0

        def close(self):
            pass

    backend.task = FakeTask()
    backend.labjack = FakeLabJack()
    backend.actual_scan_rate = 30_000.0
    chunk = backend.read()
    assert chunk.channel_units["AIN0"] == "g"
    assert chunk.channel_units["AIN1"] == "V"
    assert np.allclose(chunk.channels["AIN0"], [1, 2, 3, 4])
    assert np.allclose(chunk.channels["AIN1"], [.1, .2, .3, .4])
    assert chunk.reference_scan_rate == 30_000.0
    assert not chunk.channels_synchronized
