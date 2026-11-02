# -*- coding: utf-8 -*-
"""run_selfcheck.py — M1a 上板一键自检：下载 bit 到 PL → 驱动自检出图

用法（串口，PYNQ 板上）：
    sudo -E python3 /boot/run_selfcheck.py            # 单独跑：下 bit + 自检
    sudo -E python3 /boot/run_selfcheck.py noprog     # 接力跑：不下 bit

★ 第十三版推荐：传感器状态逐次下载随机（1/5 健康），下载 = 重新掷骰子。
  上板验证链只掷一次骰子，其余 noprog 接力：
    dvp_probe.py（含 ctrl[4] 自动修复）→ stream_probe.py noprog
    → line_count.py noprog → 本脚本 noprog

流程：
1. 加载 /boot/ov5640_m1a_wrapper.bit（pynq Overlay，旁边同名 .hwh 供解析；
   失败自动退化为 Bitstream 直下——驱动用固定寄存器地址，不依赖 hwh 解析）；
2. 等 2s（MMCM 锁定 + OV5640 上电时序 + 304 条 SCCB 配置启动）；
3. 调 vdma_capture._selfcheck()：连拍 10 帧，存 /boot/vdma_capture_check.png
   （FAT 分区，拔卡在 Windows 直接看），打印帧率与 DMASR 状态。

注意：PL 里 LD0(R14) 是 MMCM 锁定灯，跑本脚本前先肉眼确认 LD0 亮；
不亮 = 时钟没锁，capture 必然超时（查供电/杜邦线，别浪费时间等 5s）。
"""

import time

from pynq import Overlay, Bitstream

BIT = '/boot/ov5640_m1a_wrapper.bit'


def load():
    try:
        ol = Overlay(BIT)                       # 构造函数默认即下载
        print(f'[加载] overlay 已下载（hwh 解析到 {len(ol.ip_dict)} 个 IP）')
        return ol
    except Exception as e:
        msg = str(e).replace('\n', ' ')[:160]   # hwh 解析失败时错误巨长，截断打印
        print(f'[加载] Overlay 方式失败（{msg}...），退化为 Bitstream 直下')
        print('[加载] 已知无害：pynq 的 hwh 解析器对 axi_vdma 6.3 的 S2MM 端口结构不兼容，'
              '本驱动走裸 MMIO 不受影响')
        Bitstream(BIT).download()
        print('[加载] bitstream 已直下')


if __name__ == '__main__':
    import sys
    if 'noprog' in sys.argv:
        print('[加载] noprog：跳过 bit 下载，沿用当前已加载的 bit')
        print('[加载]   （勿单独跑 —— 先跑 dvp_probe.py 让它自动修复到健康，再接力本脚本）')
    else:
        load()
        print('[加载] 等 2s：MMCM 锁定 + OV5640 上电配置...')
        time.sleep(2)
    from vdma_capture import _selfcheck
    _selfcheck()
