# -*- coding: utf-8 -*-
"""stream_probe.py — 读流侧探针，判"源到底有没有给数据 / 有没有被复位按死"

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/stream_probe.py            # 单独跑：下 bit + 测
    sudo -E PYTHONIOENCODING=gbk python3 /boot/stream_probe.py noprog     # 接力跑：不下 bit

★ 第十三版推荐：传感器状态逐次下载随机（1/5 健康），下载 = 重新掷骰子。
  上板验证链只掷一次骰子，其余 noprog 接力：
    dvp_probe.py（含 ctrl[4] 自动修复）→ 本脚本 noprog → line_count.py noprog
    → run_selfcheck.py noprog

背景（详见 rtl/debug/axis_probe.v 与 rtl/ov5640/dvp2axis_bridge.v 文件头）：

  第十一轮 dvp_probe 决定性测量证明传感器每次运行都健康（PCLK 83.3MHz、
  30fps、722 行/帧、SCCB 304 条全写完），而 v_vid_in_axi4s 每次运行随机坏。
  第十二版已把该 IP 换成自研桥 dvp2axis_bridge（href 门控打包进异步 FIFO，
  tuser=帧首、tlast=行尾，单 SOF 由构造保证）。本脚本验三件事：
    ① 桥有没有在推数（tvalid ~44.4M/s = 2048 字节 × 722 行 × 30fps）
    ② 桥的 SOF/EOL 对不对（tuser ~30/s、tlast ~21660/s）
    ③ 桥的 FIFO 出没出过事（溢出/下溢 sticky 位，w3 bit13/14）

探针字（BD m1a_capture.tcl 里 axis_probe_0 接 axi_gpio_probe4/5）：
  0x43A40000+0x00  w0  源 tvalid 拍数（健康时 ~44.4M/s）
  0x43A40000+0x08  w1  源 tuser 上升沿数 ★ 帧首 SOF：健康时 ~30/s
  0x43A50000+0x00  w2  源 tlast 上升沿数 ★ 行尾 EOL：健康时 ~21660/s
  0x43A50000+0x08  w3  状态字（第十三版起与桥配套）：
        bit0 见过 tvalid  bit1 见过 tuser  bit2 见过 tlast  bit3 见过 tready
        bit4 live tvalid  bit5 live tlast  bit6 live tuser bit7 live tready
        bit8/9/12 恒 0（原 vsync/sof_armed/vid_reset 观测，随 IP 一起退役）
        ★ bit10 live 桥写侧溢出   bit11 live 桥读侧下溢
        ★ bit13 见过溢出          bit14 见过下溢
        bit15 送给 VDMA 的 tuser 电平
        ★★ bit31-28 桥写侧内部观测（第十三版，坏状态解剖）：
             bit31 sof武装（vsync 已武装帧首还没用掉）
             bit30 丢行中  bit29 FIFO复位忙  bit28 视频域复位中

  另有 ctrl 寄存器（dvp_ctrl，0x43A30000 通道1）：
        bit3 = 1 → 软件强制复位视频域（默认 0 = 不复位 = 正常工作）
        bit4 = 1 → 传感器复位重跑上电+SCCB（第十三版，dvp_probe 自动修复用）

判读表：
  tvalid ≈44.4M/s 且 tuser ≈30/s 且 tlast ≈21660/s -> ★ 桥完全健康
  tvalid > 0 但 tuser 不对                        -> 桥在推数但 SOF 结构错（查 vsync 接线/极性）
  tvalid = 0 且 见过溢出（bit13）                 -> 读侧没取数（VDMA 没 ready），写侧整行丢弃
  tvalid = 0 且无溢出/下溢                        -> 桥没拿到 DVP 数据（跑 dvp_probe 查 href/pclk）
"""

import time

GPIO3 = 0x43A30000      # +0x00 ctrl(输出)  +0x08 w6(dvp_probe)
GPIO4 = 0x43A40000      # +0x00 w0          +0x08 w1
GPIO5 = 0x43A50000      # +0x00 w2          +0x08 w3
GPIO_RANGE = 0x10000

BIT = '/boot/ov5640_m1a_wrapper.bit'

CTRL_VID_RST_BIT = 3    # ctrl[3] = 强制复位视频域

W3_LATCH = ((0, '见过tvalid'), (1, '见过tuser'), (2, '见过tlast'), (3, '见过tready'),
            (13, '见过溢出'), (14, '见过下溢'))
W3_LIVE = ((4, 'tvalid'), (5, 'tlast'), (6, 'tuser'), (7, 'tready'),
           (10, '桥溢出'), (11, '桥下溢'),
           (28, 'vid复位'), (29, 'FIFO忙'), (30, '丢行中'), (31, 'sof武装'))


def load_bit():
    from pynq import Bitstream
    Bitstream(BIT).download()
    print('[加载] bitstream 已直下')


def snapshot(mm3, mm4, mm5):
    """读探针：返回 (w0, w1, w2, w3, ctrl)。

    注意：pynq 的 MMIO.read(offset, length) 直接返回 int（不是数组），
    写成 read(...)[0] 会 TypeError: 'int' object is not subscriptable。
    """
    return (mm4.read(0x00, 4), mm4.read(0x08, 4),
            mm5.read(0x00, 4), mm5.read(0x08, 4),
            mm3.read(0x00, 4))


def show_w3(w3):
    lat = [n for b, n in W3_LATCH if w3 & (1 << b)]
    live = ['%s=%d' % (n, (w3 >> b) & 1) for b, n in W3_LIVE]
    print(f'        状态字 0x{w3:08x}: 见过[{",".join(lat) if lat else "无"}]')
    print(f'                  电平[{" ".join(live)}]  VDMA侧tuser={(w3 >> 15) & 1}')


def measure(mm3, mm4, mm5, T=2.0, tag=''):
    """观察 T 秒，返回 (增量三元组, 末次读到的 w3, ctrl)。"""
    w0a, w1a, w2a, w3a, ca = snapshot(mm3, mm4, mm5)
    if tag:
        print(f'[{tag}] 起始  tvalid={w0a}  tuser={w1a}  tlast={w2a}')
        show_w3(w3a)
    time.sleep(T)
    w0b, w1b, w2b, w3b, cb = snapshot(mm3, mm4, mm5)
    d0 = (w0b - w0a) & 0xFFFFFFFF
    d1 = (w1b - w1a) & 0xFFFFFFFF
    d2 = (w2b - w2a) & 0xFFFFFFFF
    if tag:
        print(f'[{tag}] 结束  tvalid={w0b}  tuser={w1b}  tlast={w2b}')
        show_w3(w3b)
        print(f'[{tag}] {T:.0f}s 增量: tvalid {d0} 拍 ({d0 / T / 1e6:.1f} M/s)  '
              f'tuser {d1} 个 ({d1 / T:.1f} /s)  tlast {d2} 个 ({d2 / T:.1f} /s)')
    return (d0, d1, d2), w3b, cb


# 判"源到底在不在推数据"的最低速率（拍/秒）。满速约 22M/s、44.2M/s 量级。
# ★ 这个阈值是踩过坑才加的：第七轮用 `d0 > 0` 判活，结果 60 拍/2s（=30Hz，
#   正好是 vsync 率，就是每帧抖一下）被误判成"源活着"，**脚本因此跳过了
#   后面那个最有价值的 ctrl[3] 强制复位实验**。抖动不是活性。
ALIVE_RATE = 1e5


def verdict(d0, d1, d2, w3, T=2.0, prefix='[结论]'):
    """按判读表给结论，返回 True 表示源是活的。"""
    rate = d0 / T
    if rate >= ALIVE_RATE:
        print(f'{prefix} ★ 源**活着**：tvalid {d0 / T / 1e6:.1f}M/s，数据在往外推。')
        if d1 > 0:
            print(f'{prefix} 且有 SOF：tuser {d1 / T:.1f}/s（约 30/s 是正常帧率）。')
        else:
            print(f'{prefix} 但没有 SOF（tuser 恒 0）—— 桥没产出 SOF，查 vsync 是否真到桥。')
        if d2 > 0:
            print(f'{prefix} 且有 EOL：tlast {d2 / T:.1f}/s（约 21600/s 是正常行数）。')
        return True

    seen_ovf = (w3 >> 13) & 1
    seen_unf = (w3 >> 14) & 1
    if seen_ovf:
        print(f'{prefix} 源 tvalid 恒 0，**且桥的写侧 FIFO 溢出过**（bit13=1）。')
        print(f'{prefix} 读侧一直没取数（VDMA 没 ready？），写侧灌满后整行丢弃。')
    elif seen_unf:
        print(f'{prefix} 源 tvalid 恒 0，没见过溢出但**桥读侧下溢过**（bit14=1）—— 另查。')
    else:
        print(f'{prefix} 源 tvalid 恒 0，桥也没出过溢出/下溢。')
        print(f'{prefix} 问题在桥的更上游：跑 dvp_probe 查 href/pclk 是否真到桥。')
    return False


def main():
    import sys
    from pynq import MMIO
    if 'noprog' in sys.argv:
        print('[加载] noprog：跳过 bit 下载，沿用当前已加载的 bit')
        print('[加载]   （勿单独跑 —— 先跑 dvp_probe.py 让它自动修复到健康，再接力本脚本）')
    else:
        load_bit()
        print('[加载] 等 2s：MMCM 锁定 + 传感器上电配置 + PCLK 起振（vid_rst_ctrl 需 43.7ms PCLK 才放复位）...')
        time.sleep(2)

    mm3 = MMIO(GPIO3, GPIO_RANGE)
    mm4 = MMIO(GPIO4, GPIO_RANGE)
    mm5 = MMIO(GPIO5, GPIO_RANGE)

    print('=' * 66)
    print('[第一轮] 上电后自然状态（新复位逻辑生效后的样子）')
    print('=' * 66)
    (d0, d1, d2), w3, ctrl = measure(mm3, mm4, mm5, 2.0, '基线')
    alive = verdict(d0, d1, d2, w3, 2.0)

    if not alive:
        # ------------------------------------------------------------------
        # 判决性实验：软件强制复位视频域一次，看能不能把死锁解开
        # 这是黑盒验证 —— 如果 overflow 把视频域锁死了，重来一次应该能活
        # ------------------------------------------------------------------
        print('=' * 66)
        print('[第二轮] 实验：用 ctrl[3] 强制复位视频域一次，看死锁能否解开')
        print('=' * 66)
        print(f'[实验] 当前 ctrl = 0x{ctrl:08x}')
        mm3.write(0x00, ctrl | (1 << CTRL_VID_RST_BIT))
        print(f'[实验] 写入 ctrl[3]=1（强制复位）-> 0x{(ctrl | (1 << CTRL_VID_RST_BIT)):08x}，按住 0.3s')
        time.sleep(0.3)
        w3r = mm5.read(0x08, 4)
        print(f'[实验] 复位期间状态字 0x{w3r:08x}（第十二版起 w3 不再观测复位电平，此数仅供参考）')
        mm3.write(0x00, ctrl & ~(1 << CTRL_VID_RST_BIT))
        print(f'[实验] 写入 ctrl[3]=0（撤复位）-> 0x{(ctrl & ~(1 << CTRL_VID_RST_BIT)):08x}，再观察 2s')
        time.sleep(1.0)
        (d0b, d1b, d2b), w3b, ctrlb = measure(mm3, mm4, mm5, 2.0, '复复位后')
        alive2 = verdict(d0b, d1b, d2b, w3b, 2.0, prefix='[实验结论]')
        if alive2:
            print('[实验结论] ★★ 强制复位把视频域解开了 —— 证实"溢出锁死"假说，')
            print('[实验结论]    而且说明新复位逻辑放出来之后是**能工作**的。')
        else:
            print('[实验结论] 强制复位也没救活 —— 死锁假说被否，问题在更上游。')

    print('=' * 66)
    print('[验证] 接着跑完整驱动自检（连拍 10 帧 + 存 PNG）...')
    import vdma_capture as vc
    vc._selfcheck()


if __name__ == '__main__':
    main()
