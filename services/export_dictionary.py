"""File-name and field-dictionary helpers for measurement exports."""

import csv
import re
from pathlib import Path
from typing import Mapping

from models.sensors import SensorProfile


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *("COM%d" % number for number in range(1, 10)),
    *("LPT%d" % number for number in range(1, 10)),
}


def safe_product_filename(product_id: str) -> str:
    """Return a Windows-safe product token while preserving useful Unicode."""
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", str(product_id).strip())
    value = re.sub(r"\s+", "_", value).strip(" ._")[:80]
    if not value:
        return "NO_PRODUCT_ID"
    if value.upper() in _WINDOWS_RESERVED_NAMES:
        value += "_PRODUCT"
    return value


def _engineering_rows(
    filename: str,
    profiles: Mapping[str, SensorProfile],
    spectrum: bool,
):
    for side, chinese_side in (("left", "左侧"), ("right", "右侧")):
        profile = profiles[side]
        if spectrum:
            yield (
                filename,
                "%s_amplitude_v" % side,
                "%s传感器电压的单边 FFT 峰值幅值" % chinese_side,
                "V peak",
                "Hann 窗、去直流、相干增益归一化",
            )
            if profile.sensor_type == "accelerometer":
                yield (
                    filename, "%s_amplitude_g" % side,
                    "%s加速度单边 FFT 峰值幅值" % chinese_side, "g peak",
                    "abs(amplitude_v)×1000/灵敏度(mV/g)",
                )
                yield (
                    filename, "%s_amplitude_m_s2" % side,
                    "%s加速度单边 FFT 峰值幅值" % chinese_side, "m/s² peak",
                    "amplitude_g×9.80665",
                )
            elif profile.sensor_type == "piezo":
                yield (
                    filename, "%s_amplitude_mv" % side,
                    "%s压电通道单边 FFT 峰值幅值" % chinese_side, "mV peak",
                    "abs(amplitude_v)×1000",
                )
        else:
            yield (
                filename, "%s_voltage_v" % side,
                "%s采集卡输入端的原始瞬时电压" % chinese_side, "V",
                "采集卡原始值；始终保留，不做单位替换",
            )
            if profile.sensor_type == "accelerometer":
                yield (
                    filename, "%s_acceleration_g" % side,
                    "%s加速度瞬时值" % chinese_side, "g",
                    "voltage_v×1000/灵敏度(mV/g)×极性",
                )
                yield (
                    filename, "%s_acceleration_m_s2" % side,
                    "%s加速度瞬时值" % chinese_side, "m/s²",
                    "acceleration_g×9.80665",
                )
            elif profile.sensor_type == "piezo":
                yield (
                    filename, "%s_piezo_voltage_mv" % side,
                    "%s压电通道瞬时电压" % chinese_side, "mV",
                    "voltage_v×1000×极性",
                )


def write_field_dictionary(
    path: Path,
    profiles: Mapping[str, SensorProfile],
    filenames: Mapping[str, str],
    include_recording_streams: bool = False,
) -> Path:
    """Write a human-readable dictionary for every exported measurement field."""
    raw_name = filenames["raw"]
    fft_name = filenames["spectrum"]
    raw_rows = [
        (raw_name, "time_s", "本次保存片段起点后的相对时间", "s",
         "保存片段内从 0 开始，sample_offset/scan_rate_hz"),
        (raw_name, "sample_index", "采集会话中的连续原始样本序号", "sample",
         "采集线程给出的 start_sample + 当前样本偏移"),
        *_engineering_rows(raw_name, profiles, spectrum=False),
        (raw_name, "fio3_state", "与振动样本同流时的 FIO3 光电数字电平", "0/1",
         "纯 LabJack 时同步；NI/LabJack 异步模式可能为空"),
    ]
    fft_rows = []
    if include_recording_streams:
        fft_rows.append(
            (fft_name, "frame", "记录期间 FFT 帧的顺序编号", "index", "从 0 递增")
        )
    fft_rows.extend([
        (fft_name, "frequency_hz", "FFT 频率坐标", "Hz",
         "rfftfreq(有效样本数, 1/scan_rate_hz)"),
        *_engineering_rows(fft_name, profiles, spectrum=True),
    ])
    rows = raw_rows + fft_rows
    if include_recording_streams:
        tach_name = filenames["tach"]
        rows.extend([
            (tach_name, "start_sample", "该原始数据块首样本的会话序号", "sample", "采集线程"),
            (tach_name, "sample_count", "该数据块实际写入的样本数", "sample", "固定时长末块可能被裁切"),
            (tach_name, "scan_rate_hz", "振动通道采样率", "sample/s", "采集硬件实际采样率"),
            (tach_name, "received_timestamp", "该数据块到达程序时的 Unix 时间戳", "s since epoch", "系统时钟"),
            (tach_name, "fio3_corrected_frequency_hz", "经设备修正后的转子光电频率", "Hz", "FIO3 原始频率/optical_frequency_divisor"),
            (tach_name, "fio3_raw_frequency_hz", "LabJack FIO3 直接读到的原始边沿频率", "Hz", "DIO-EF 连续测频"),
            (tach_name, "dropped_samples", "截至该块报告的丢失样本数", "sample", "采集驱动诊断"),
        ])
        one_x_name = filenames["one_x"]
        one_x_fields = [
            ("frequency_hz", "本次选定/拟合的 1X 频率", "Hz"),
            ("reference_hz", "用于选峰的平滑光电参考频率", "Hz"),
            ("rpm", "转子转速", "RPM"),
            ("rpm_variation", "分析窗内转速相对波动", "ratio"),
            ("reference_min_hz", "参考频率窗口最小值", "Hz"),
            ("reference_max_hz", "参考频率窗口最大值", "Hz"),
            ("reference_held_fraction", "参考频率被保持/替代的时间占比", "ratio"),
            ("synchronous_reference_hz", "同流 FIO3 边沿算得的同步参考频率", "Hz"),
            ("synchronous_period_variation", "同步 FIO3 周期相对波动", "ratio"),
            ("reference_edge_count", "分析窗内有效 FIO3 参考边沿数量", "count"),
            ("repaired_saturation_samples", "为分析而插值修复的孤立饱和点数量", "sample"),
            ("shaft_angle_synchronous", "相位是否与 FIO3 同步且通过质量门", "boolean"),
            ("preview_only", "结果是否仅为候选预览而非有效 1X", "boolean"),
            ("relative_electrical_phase_degrees", "右路相位减左路相位并折算到 ±180°", "degree"),
            ("status", "本次 1X 质量判断说明", "text"),
        ]
        for field, meaning, unit in one_x_fields:
            rows.append((one_x_name, field, meaning, unit, "1X 分析结果"))
        for side, chinese_side in (("left", "左侧"), ("right", "右侧")):
            side_fields = [
                ("valid", "%s 1X 是否通过有效性判断" % chinese_side, "boolean"),
                ("preview", "%s是否只有候选峰预览" % chinese_side, "boolean"),
                ("amplitude_v", "%s 1X 拟合正弦峰值幅值" % chinese_side, "V peak"),
                ("snr", "%s 1X 峰相对邻频噪声的信噪比" % chinese_side, "ratio"),
                ("reason", "%s无效或预览原因" % chinese_side, "text"),
                ("fio3_relative_phase_degrees", "%s相对 FIO3 下降沿零位的同步相位" % chinese_side, "degree"),
            ]
            for field, meaning, unit in side_fields:
                rows.append((one_x_name, "%s.%s" % (side, field), meaning, unit, "1X 拟合/质量判断"))
            profile = profiles[side]
            if profile.sensor_type == "accelerometer":
                rows.extend([
                    (one_x_name, "%s.%s_amplitude_g" % (side, side),
                     "%s 1X 峰值加速度" % chinese_side, "g peak",
                     "abs(amplitude_v)×1000/灵敏度(mV/g)"),
                    (one_x_name, "%s.%s_amplitude_m_s2" % (side, side),
                     "%s 1X 峰值加速度" % chinese_side, "m/s² peak",
                     "%s_amplitude_g×9.80665" % side),
                ])
            elif profile.sensor_type == "piezo":
                rows.append(
                    (one_x_name, "%s.%s_amplitude_mv" % (side, side),
                     "%s 1X 峰值压电电压" % chinese_side, "mV peak",
                     "abs(amplitude_v)×1000")
                )
    with Path(path).open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(("data_file", "field", "meaning", "unit", "source_or_formula"))
        writer.writerows(rows)
    return Path(path)
