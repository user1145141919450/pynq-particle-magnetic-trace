# -*- coding: utf-8 -*-
"""map_detect_pl.py —— PL 检测 v0（缩略图颜色分类）的驱动 / 上板探针

对应硬件：map_detect_axi（BD 地址 0x40002000），tap 并联在 vision_core 的
map 写口上；每帧末自动处理，PS 只读结果。

寄存器图（**字节偏移**；与 rtl/map_detect/map_detect_axi.v 一一对应）：
  0x00 W CTRL   [0]=en（0=核心保持复位） [1]=soft_rst（电平） [2]=clr_done（W1C）
  0x00 R STATUS [0]=busy [1]=done_sticky [2]=ovf [3]=en
  0x04 R SEQ    已处理帧数
  0x08 R MEDVHI {vhi[15:8], med[7:0]}
  0x0C R CNT_RED
  0x10 R CNT_BLUE
  0x14 R CNT_BLACK
  0x18 R N_CAND（v0 恒 0）
  0x1C R VERSION（v0 = 0x00010000）

用法（板上；要 root + 登录 shell，因为 /dev/mem）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \
      /usr/local/share/pynq-venv/bin/python3 -u map_detect_pl.py probe"
  map_detect_pl.py on | off | reset | read
  map_detect_pl.py watch --sec 10      # 看每秒处理帧数（应≈相机帧率）

退出码：0=正常；1=自检失败；9=环境问题（mmap/版本不对）。
"""
import argparse
import sys
import time

BASE = 0x40002000
VER_V0 = 0x00010000     # 第一版：只有 V 中位/三色掩码计数
VER_V1 = 0x00020000     # 第二版：+ 形态学/CCL 候选（area/Σx/Σy）
VER_EXPECT = VER_V0

R_CTRL = 0x00
R_STATUS = 0x00
R_SEQ = 0x04
R_MEDVHI = 0x08
R_RED = 0x0C
R_BLUE = 0x10
R_BLACK = 0x14
R_NCAND = 0x18          # [5:0]=n_cand [6]=cand_ovf [7]=label_ovf
R_VER = 0x1C
R_CAND_IDX = 0x20       # 写：选候选（0..31）
R_CAND_W0 = 0x24        # 读：cand_data[31:0]
R_CAND_W1 = 0x28        # 读：cand_data[56:32]（低 25 位）


class MapDetect(object):
    def __init__(self, base=BASE):
        from fastmem import FastMem
        self.m = FastMem(base, 0x1000)

    def close(self):
        self.m.close()

    # ---------------- 控制 ----------------
    def enable(self):
        self.m.wr_u32(R_CTRL, 0x1)

    def disable(self):
        self.m.wr_u32(R_CTRL, 0x0)

    def soft_reset(self):
        """核心保持复位 50ms 再放行（清 hist/帧缓存/ovf）。"""
        self.m.wr_u32(R_CTRL, 0x3)
        time.sleep(0.05)
        self.m.wr_u32(R_CTRL, 0x1)

    def clear_done(self):
        # 保持 en=1，同时 CTRL[2]=1（W1C）
        self.m.wr_u32(R_CTRL, 0x5)

    # ---------------- 读 ----------------
    def status(self):
        s = self.m.rd_u32(R_STATUS)
        return dict(busy=(s >> 0) & 1, done=(s >> 1) & 1,
                    ovf=(s >> 2) & 1, en=(s >> 3) & 1)

    def read(self):
        seq = self.m.rd_u32(R_SEQ)
        mv = self.m.rd_u32(R_MEDVHI)
        nc = self.m.rd_u32(R_NCAND)
        return dict(seq=seq, med=mv & 0xFF, vhi=(mv >> 8) & 0xFF,
                    red=self.m.rd_u32(R_RED) & 0xFFFF,
                    blue=self.m.rd_u32(R_BLUE) & 0xFFFF,
                    black=self.m.rd_u32(R_BLACK) & 0xFFFF,
                    n_cand=nc & 0x3F,
                    cand_ovf=(nc >> 6) & 1,
                    label_ovf=(nc >> 7) & 1,
                    version=self.m.rd_u32(R_VER))

    def read_candidates(self, retries=4):
        """读 PL 候选（v1+）。返回 (list, ovf, label_ovf, seq)。

        一致性：SEQ 读前读后相等才算数（候选数组在下一帧末才会被改写）。
        候选字段：cls 0=red 1=blue 2=black；area；sx/sy（整数统计，块坐标）。
        """
        for _ in range(retries):
            s0 = self.m.rd_u32(R_SEQ)
            nc = self.m.rd_u32(R_NCAND)
            n = nc & 0x3F
            out = []
            for i in range(n):
                self.m.wr_u32(R_CAND_IDX, i)
                lo = self.m.rd_u32(R_CAND_W0)
                hi = self.m.rd_u32(R_CAND_W1) & 0x1FFFFFF
                raw = (hi << 32) | lo
                out.append(dict(cls=(raw >> 55) & 0x3,
                                area=(raw >> 41) & 0x3FFF,
                                sx=(raw >> 20) & 0x1FFFFF,
                                sy=raw & 0xFFFFF))
            s1 = self.m.rd_u32(R_SEQ)
            if s0 == s1:
                return out, (nc >> 6) & 1, (nc >> 7) & 1, s0
        return out, (nc >> 6) & 1, (nc >> 7) & 1, -1

    def wait_new_seq(self, last, timeout=2.0):
        """等 SEQ 变化（=新的一帧处理完）。返回 (新 SEQ, 是否超时)。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.m.rd_u32(R_SEQ)
            if s != last:
                return s, False
            time.sleep(0.002)
        return self.m.rd_u32(R_SEQ), True


def cmd_probe(md):
    st = md.status()
    rd = md.read()
    print("[PL检测] STATUS busy=%d done=%d ovf=%d en=%d"
          % (st['busy'], st['done'], st['ovf'], st['en']))
    print("[PL检测] VERSION=0x%08X（v0=0x%08X / v1=0x%08X）"
          % (rd['version'], VER_V0, VER_V1))
    print("[PL检测] SEQ=%d MED=%d VHI=%d RED=%d BLUE=%d BLACK=%d N_CAND=%d%s%s"
          % (rd['seq'], rd['med'], rd['vhi'], rd['red'], rd['blue'],
             rd['black'], rd['n_cand'],
             " CAND_OVF" if rd['cand_ovf'] else "",
             " LABEL_OVF" if rd['label_ovf'] else ""))
    bad = 0
    if rd['version'] not in (VER_V0, VER_V1):
        print("!! VERSION 不对：bit 里没有这版 PL 检测（或地址不对）")
        bad = 1
    if not st['en']:
        print("!! en=0：先跑 `map_detect_pl.py on`")
        bad = 1
    if not st['busy'] and not st['done'] and rd['seq'] == 0:
        print("!! 既没忙过也没 done：相机链路没在出帧？")
        bad = 1
    if st['ovf']:
        print("!! ovf=1：处理期间又来帧末（丢帧）——查核心是否被卡住/时钟")
        bad = 1
    return bad


def cmd_cands(md):
    out, ovf, lovf, seq = md.read_candidates()
    print("[PL候选] SEQ=%d N=%d cand_ovf=%d label_ovf=%d"
          % (seq, len(out), ovf, lovf))
    for i, c in enumerate(out):
        print("  [%2d] cls=%d area=%d sx=%d sy=%d"
              % (i, c['cls'], c['area'], c['sx'], c['sy']))
    if seq < 0:
        print("!! 读候选期间 SEQ 一直在变（重试 4 次仍不稳）")
        return 1
    return 0


def cmd_watch(md, sec):
    md.enable()
    last, _ = md.wait_new_seq(md.m.rd_u32(R_SEQ), timeout=0.1)
    t0 = time.time()
    n = 0
    while time.time() - t0 < sec:
        s, to = md.wait_new_seq(last, timeout=0.5)
        if to:
            print("[PL检测] %.2fs 内没有新帧（SEQ=%d）" % (time.time() - t0, last))
            continue
        n += 1
        last = s
    dt = time.time() - t0
    print("[PL检测] %d 帧 / %.2fs = %.2f fps（SEQ=%d）" % (n, dt, n / dt if dt else 0, last))
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "on", "off", "reset", "read", "watch", "cands"])
    ap.add_argument("--sec", type=float, default=10.0)
    ap.add_argument("--base", type=lambda x: int(x, 0), default=BASE)
    a = ap.parse_args(argv)

    try:
        md = MapDetect(a.base)
    except Exception as e:
        print("!! mmap 失败：%s（要 root + /dev/mem）" % e)
        return 9

    try:
        if a.cmd == "on":
            md.enable()
            print("[PL检测] en=1")
            return 0
        if a.cmd == "off":
            md.disable()
            print("[PL检测] en=0（核心保持复位）")
            return 0
        if a.cmd == "reset":
            md.soft_reset()
            print("[PL检测] 软复位完成，en=1")
            return 0
        if a.cmd == "read":
            print(md.read())
            return 0
        if a.cmd == "watch":
            return cmd_watch(md, a.sec)
        if a.cmd == "cands":
            return cmd_cands(md)
        return cmd_probe(md)
    finally:
        md.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
