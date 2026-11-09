# 动平衡测试系统

PySide6 上位机，按 UI / worker / hardware backend / service / model / repository 分层实现。界面使用白色主背景和红色品牌辅助色。采集端支持 LabJack T7，以及 Windows 下的 NI cDAQ-9174 + NI-9234。

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py app.py
```

## 已实现界面与流程

- 生产测量：产品编号、模板、转向、双路波形、双路频谱、流程状态。
- 双面动平衡：A/B 面原始 1X 信号矢量、极坐标和相对相位。
- 频谱诊断：双通道时域/FFT、默认 300 Hz 上限、前 5 个峰和频段表。
- 试重标定：基准、左面试重、右面试重的分步记录界面。
- 测量模板与 QC：目标转速、稳定容差、左右阈值、平均次数和启动延时。
- 测试记录、系统设置、用户与权限、系统日志。
- CSV 原始数据导出和 SQLite 历史索引。

偏重质量、机械配重角、双面影响系数和动平衡等级公式尚未启用。当前只显示可验证的原始信号、频率和相位，避免把未确认算法当作正式结果。

## 采集设备

- 左右传感器均为压电陶瓷时只启动 LabJack T7；默认左 AIN0、右 AIN3、转速 FIO3。
- 任意一侧选择 IEPE 加速度传感器时，NI-9234 采集加速度，LabJack T7 同时采集 FIO3 转速以及仍为压电陶瓷的另一侧。
- 左右均选择 IEPE 时，NI-9234 同步采集两个加速度通道，LabJack 只采转速。
- 353B15 默认灵敏度 10 mV/g，NI-9234 使用 AC 耦合和 2.0 mA IEPE 激励，返回单位为 g。
- 一侧 NI、另一侧 LabJack 时，两路没有共用硬件采样时钟，软件不输出左右相对相位；RPM 与各自 1X 幅值仍可测量。

### Windows 上使用 NI-9234

1. 安装 NI-DAQmx，并在 NI MAX 中确认 cDAQ-9174 和 NI-9234 自检通过。
2. 在项目目录创建虚拟环境并执行 `pip install -r requirements.txt`。
3. 将 353B15 接到 NI-9234 的 `ai0` BNC；将光电转速接到 LabJack FIO3。
4. 在“系统设置 / 传感器”把实际安装的一侧选择为“IEPE 加速度传感器（PCB 353B15）”，另一侧按实物选择压电陶瓷或未安装。程序会自动启用所需设备。
5. NI 模块名称填写 NI MAX 显示的名称（通常为 `cDAQ1Mod1`），灵敏度填写传感器校准证书值；没有证书时先用标称 `10 mV/g`。

## 待确认的真机参数

- 光电零位与双路模拟量真正同步读取方案。
- 相位基准、角度正方向、左右校正面几何参数及影响系数算法。
- 压电放大板量程、增益和标定比例。
- QC 合格标准和外部 PLC/串口通信协议。
- LabJack 目标机需要安装 LabJack LJM 驱动。

## 测试

采集与频谱共有 12 项自动测试，覆盖已知正弦主频、配置校验、纯 LabJack、NI + LabJack 混合采集、单位与同步状态。
