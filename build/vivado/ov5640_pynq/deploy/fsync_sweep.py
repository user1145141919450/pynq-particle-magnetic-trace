# -*- coding: utf-8 -*-
"""fsync_sweep.py — M1a 根因实锤：扫 DMACR[6:5] FSYNCSEL 四个取值，看哪个能出帧

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/fsync_sweep.py

★ 不用重新综合、不用换 bit。FSYNCSEL 是 DMACR 里的**运行时可写位**，
  当前 SD 卡上这份 bitstream 就能测——这是本次定位最省时间的一点。

------------------------------------------------------------------
根因（axi_vdma_v6_3_rfs.vhd:53016 的 S2MM FSYNC_CROSSBAR，原文照抄）：

    case fsync_src_select is
        when "00" =>   -- primary fsync (default)
            frame_sync_i <= fsync and run_stop;
        when "01" =>   -- other channel fsync
            frame_sync_i <= othrchnl_fsync and run_stop;
        when "10" =>   -- tuser fsync  (used only by s2mm)
            frame_sync_i <= tuser_fsync and run_stop;
        when others =>
            frame_sync_i <= '0';
    end case;

fsync_src_select 接的是 s2mm_dmacr(6 downto 5)（同文件 73702 行），而
s2mm_dmacr 的复位值是 **(others => '0')**（73764 行）——即默认 "00"。
本设计只有 S2MM、没有 MM2S 通道，所以：
  "00" primary fsync 没有来源  → 永不脉冲
  "01" othrchnl_fsync 没有对端 → 永不脉冲（恒 0）
只有 "10" tuser fsync 是"让 S2MM 直接拿视频流里的 SOF 当帧同步"，
正是单通道采集该用的那一档（IP 源码注释原文就写着 used only by s2mm）。

s2mm_fsync_out_i 不脉冲 → chnl_ready 恒 0（rfs.vhd 72995 起）→
s_axis_s2mm_tvalid_int = tvalid and chnl_ready 恒 0 → 一个字节都进不了
datamover → 垂直计数器不递减、帧计数冻结 → 同时 SOF_LATE 条件
(chnl_ready='0' and tvalid='1' and tuser='0') 每拍成立、错误位反复复现。
上板看到的"帧计数停在 255、DMASR=0x00ff4000、115 次错误事件"就是这一条。

------------------------------------------------------------------
判读：每档扫 2.5 秒。帧计数是**倒计数器**（见 vdma_capture.py 坑 3），
完成 1 帧减 1，故 帧数 = (起始值 - 结束值) mod 256，另用 5ms 轮询数
跳变次数作交叉验证。帧数 > 0 = 这档就是对的。
"""

import time

VDMA_BASE = 0x43000000
S2MM_DMACR = 0x30
S2MM_DMASR = 0x34
S2MM_VSIZE = 0xA0
S2MM_HSIZE = 0xA4
S2MM_STRIDE = 0xA8
S2MM_START = 0xAC
VDMA_RANGE = 0x10000

DMACR_RS = 0x00000001
DMACR_CRCLPRK = 0x00000002
DMACR_RESET = 0x00000004
DMACR_IRQTHRESH_SHIFT = 16
DMACR_IRQTHRESH = 0xFF

SR_HALTED = 0x00000001
SR_ERR_MASK = 0x00000FF0
SR_FRMCNT_SHIFT = 16

BIT = '/boot/ov5640_m1a_wrapper.bit'

# 先试 "10"（理论正确值），再试其余三档作对照
ORDER = (2, 0, 1, 3)
NAME = {0: 'primary fsync（复位默认）', 1: 'other channel fsync',
        2: 'tuser fsync（单通道 S2MM 专用）', 3: '保留（恒 0，必不出帧）'}

ERR_NAME = ((4, 'dma_interr'), (5, 'dma_slverr'), (6, 'dma_decerr'),
            (7, 'fsize_err'), (8, 'lsize_err'), (9, 'sg_slverr'),
            (10, 'sg_decerr'), (11, 'fsize_more_or_sof_late'))


def err_names(sr):
    n = [f'{nm}(bit{b})' for b, nm in ERR_NAME if sr & (1 << b)]
    return '+'.join(n) if n else '无'


def load_bit():
    from pynq import Bitstream
    Bitstream(BIT).download()
    print('[加载] bitstream 已直下（PL 未变，只改 DMACR 的运行位）')


def probe(cap, vc, sel, seconds=2.5, dt=0.005):
    """把通道配成 FSYNCSEL=sel 跑一段，返回 (帧数, 交叉验证帧数, 错误事件数, 结束DMASR)。"""
    vc.CR_FSYNCSEL = sel
    cap.stop()
    cap.reset()
    # 软复位只清 DMACR/DMASR，地址类寄存器按 PG020 也一并重写一遍最稳
    cap.mmio.write(S2MM_VSIZE, cap.height)
    cap.mmio.write(S2MM_HSIZE, cap.stride)
    cap.mmio.write(S2MM_STRIDE, cap.stride)
    for i, b in enumerate(cap.bufs):
        cap.mmio.write(S2MM_START + 4 * i, int(b.physical_address))
    cap.start()

    dmacr = cap.mmio.read(S2MM_DMACR)
    first = (cap.mmio.read(S2MM_DMASR) >> SR_FRMCNT_SHIFT) & 0xFF
    last, hops, nerr = first, 0, 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        sr = cap.mmio.read(S2MM_DMASR)
        if sr & SR_ERR_MASK:
            nerr += 1
            cap.mmio.write(S2MM_DMASR, sr & SR_ERR_MASK)   # W1C 清掉，别粘连
        cnt = (sr >> SR_FRMCNT_SHIFT) & 0xFF
        if cnt != last:
            hops += 1
            last = cnt
        time.sleep(dt)
    sr = cap.mmio.read(S2MM_DMASR)
    frames = (first - last) % 256          # 倒计数：起始-结束 = 完成帧数（mod 256）
    return frames, hops, nerr, sr, dmacr


def main():
    load_bit()
    print('[加载] 等 2s：MMCM 锁定 + OV5640 上电配置...')
    time.sleep(2)

    import vdma_capture as vc

    cap = vc.S2mmCapture(1024, 720)
    print(f'[扫描] {cap.num_bufs} 帧缓冲 @ {[hex(int(b.physical_address)) for b in cap.bufs]}')
    sr0 = cap.mmio.read(S2MM_DMASR)
    print(f'[扫描] 启动前 DMASR=0x{sr0:08x} 帧计数={(sr0 >> 16) & 0xFF}')

    good = []
    print('=' * 68)
    for sel in ORDER:
        try:
            frames, hops, nerr, sr, dmacr = probe(cap, vc, sel)
        except Exception as e:
            print(f'[扫描] FSYNCSEL="{sel:02b}" 异常：{e}')
            continue
        tag = '出帧' if frames > 0 else '无帧'
        print(f'[扫描] FSYNCSEL="{sel:02b}" ({sel}) {NAME[sel]}')
        print(f'        DMACR=0x{dmacr:08x}  帧数={frames} (跳变 {hops} 次)  '
              f'2.5s 内错误事件 {nerr} 次  结束 DMASR=0x{sr:08x}')
        print(f'        halted={bool(sr & SR_HALTED)}  错误位：{err_names(sr)}  -> {tag}')
        if frames > 0:
            good.append((sel, frames, hops))
        print('-' * 68)

    print('=' * 68)
    if not good:
        print('[结论] 四档都不出帧 —— 说明问题不在 fsync 源选择，'
              '而是**源侧根本没给出 SOF(tuser)**。')
        print('[结论] 下一步必须上流侧探针（数 tuser/tlast/tvalid 各多少），'
              '看 v_vid_in_axi4s 到底有没有在帧首拉 tuser。')
        cap.stop()
        return

    sel, frames, hops = good[0]
    fps = frames / 2.5
    print(f'[结论] FSYNCSEL="{sel:02b}" ({sel}) 出帧！{frames} 帧 / 2.5s ≈ {fps:.1f} fps')
    print(f'[结论] 这一档是：{NAME[sel]}')
    print(f'[结论] 要固化进驱动：vdma_capture.py 的 CR_FSYNCSEL = {sel}')
    if fps < 25:
        print('[结论] 注意帧率偏低：扫描期间每 5ms 读一次寄存器会干扰 PS，'
              '正常采集应接近 30fps')

    # 用这一档跑完整的驱动自检，顺便出图
    vc.CR_FSYNCSEL = sel
    cap.stop()
    print('=' * 68)
    print(f'[验证] 用 FSYNCSEL={sel} 跑完整驱动自检（连拍 10 帧 + 存 PNG）...')
    vc._selfcheck()


if __name__ == '__main__':
    main()
