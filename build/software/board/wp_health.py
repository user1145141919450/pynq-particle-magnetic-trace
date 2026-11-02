# -*- coding: utf-8 -*-
"""wp_health.py —— 开工前"文件体检"（板侧，纯 stdlib，只读，不碰相机/不下 bit）。

为什么要有它（2026-10-01 深夜事故）：
    pkill 掉游戏进程之后 `import pynq` 开始**段错误** —— faulthandler 实锤死在
    `_compile_bytecode`（加载 .pyc 的那一步），单 import pynq.bitstream 报 glibc
    "corrupted size vs. prev_size"；zocl 重载两次无效、dmesg 又看不到 mmc 报错。
    疑似**卡上文件损坏**（与 10-01 下午"半张卡"事件同族）。
    ⇒ 开工第一步不再是 load_bit，而是先问一句"**这张卡还好吗**"：

      A. 走读关键目录里的**每一个文件**（坏扇区在"读"的时候才会现形）
      B. 子进程 import 探针（pynq / pynq.bitstream / numpy+cv2）—— 段错误
         会以**负退出码（-11）**现形，比什么都直白
      C. dmesg 里的 mmc / ext4 / I-O error / segfault
      D. 磁盘余量

    全过（rc=0）才继续 pl_probe -> load_bit -> 游戏那条链。任何一项炸 ->
    按 docs/known_issues.md 的分级处置（重烧备卡优先，别在坏卡上硬撑）。

用法（板上，从 /home/xilinx/wp 跑）：
    /usr/local/share/pynq-venv/bin/python3 wp_health.py          # 全套（A+B+C+D）
    /usr/local/share/pynq-venv/bin/python3 wp_health.py --quick  # 只 A(限 wp)+B
    WP_PY=/usr/bin/python3 ... wp_health.py                      # 换探针解释器
    WP_TREES=/home/xilinx/wp:/其他/目录 ...                      # 换走读目录
                                                   （os.pathsep 分隔：Linux ':' / PC ';'）
退出码：0 = 没发现异常；1 = 有发现（逐条打印，别把"有发现"当"必然坏"：
        权限类条目只是记录，I/O 错误与段错误才是实锤）。
"""

import os
import subprocess
import sys
import time

PY = os.environ.get('WP_PY', '/usr/local/share/pynq-venv/bin/python3')
QUICK = '--quick' in sys.argv

# (标签, 路径) —— 走读的目录树。--quick 只走第一个。
# ★ 分隔符用 os.pathsep（Linux=':' / Windows=';'）—— 第一版写死 ':'，
#   在 PC 干跑时把盘符 "E:" 也劈开（E 与 /pynq-... 各成一条），静默错位。
if os.environ.get('WP_TREES'):
    TREES = [(os.path.basename(p) or p, p)
             for p in os.environ['WP_TREES'].split(os.pathsep) if p]
else:
    TREES = [
        ('wp（项目代码）', '/home/xilinx/wp'),
        ('pynq-venv（解释器+库）', '/usr/local/share/pynq-venv/lib'),
    ]

PROBES = [
    ('import pynq', 'import pynq; print("pynq ok")'),
    ('import pynq.bitstream', 'import pynq.bitstream; print("bitstream ok")'),
    ('import numpy, cv2', 'import numpy, cv2; print(numpy.__version__, cv2.__version__)'),
]


def read_tree(label, root, limit=None):
    """把一棵树里每个文件从头读到尾（不比对内容，只要"读得动"）。

    返回 (发现列表, 文件数, 总字节, 用时)。发现 = (kind, path, detail)，
    kind ∈ {'IO', 'PERM', 'OTHER'}，只有 IO 是坏扇区的直接证据。
    """
    found, nfiles, nbytes = [], 0, 0
    t0 = time.time()
    if not os.path.isdir(root):
        return [('MISS', root, '目录不存在（PC 干跑正常；板上则是真的缺）')], 0, 0, 0.0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            if limit is not None and nfiles >= limit:
                break
            if os.path.islink(p):
                continue
            try:
                with open(p, 'rb') as f:
                    while True:
                        blk = f.read(1 << 20)
                        if not blk:
                            break
                        nbytes += len(blk)
                nfiles += 1
            except PermissionError as e:
                found.append(('PERM', p, str(e)))
            except OSError as e:
                # ★ 这就是要抓的东西：坏扇区/介质错误会在这里以 EIO 现形
                found.append(('IO', p, str(e)))
            except Exception as e:                       # noqa: BLE001
                found.append(('OTHER', p, str(e)))
        if limit is not None and nfiles >= limit:
            print('    （%s：已到 --limit %d 个文件，提前收工）' % (label, limit))
            break
    return found, nfiles, nbytes, time.time() - t0


def probe(label, code):
    """子进程 import 探针。返回 (ok, detail)。

    段错误 = 负返回码（-11=SIGSEGV / -6=SIGABRT）—— 直白写出来，
    这是 2026-10-01 事故的主症状。
    """
    t0 = time.time()
    try:
        r = subprocess.run([PY, '-c', code], capture_output=True, text=True,
                           timeout=120)
    except FileNotFoundError:
        return None, '找不到解释器 %s（PC 干跑正常；板上检查 WP_PY）' % PY
    except subprocess.TimeoutExpired:
        return False, '超时 120s'
    ms = (time.time() - t0) * 1e3
    if r.returncode == 0:
        return True, '%s（%.0fms）' % (r.stdout.strip().splitlines()[-1]
                                       if r.stdout.strip() else 'ok', ms)
    if r.returncode < 0:
        return False, ('**信号 %d 杀死**（-11 = 段错误 / -6 = abort）—— '
                       '这就是"import pynq 段错误"事故的同款症状'
                       % -r.returncode)
    tail = (r.stderr or '').strip().splitlines()
    return False, 'rc=%d; %s' % (r.returncode, tail[-1] if tail else '(无 stderr)')


def dmesg_scan():
    """dmesg 里的存储/崩溃线索。读不到就如实说（静默失败最贵）。"""
    try:
        r = subprocess.run(['dmesg', '-T'], capture_output=True, text=True,
                           timeout=30)
    except Exception as e:                               # noqa: BLE001
        return None, 'dmesg 跑不了：%s' % e
    if r.returncode != 0:
        return None, ('dmesg rc=%d（多半是 kernel.dmesg_restrict；'
                      'root 下再跑一次）：%s'
                      % (r.returncode, (r.stderr or '').strip()[:120]))
    keys = ('mmc', 'ext4', 'i/o error', 'bad message', 'sigbus',
            'segfault', 'corrupt', 'read-only')
    hits = [ln for ln in r.stdout.splitlines()
            if any(k in ln.lower() for k in keys)]
    return (len(hits) == 0), hits[-20:]


def main():
    print('=' * 70)
    print('  wp_health —— 开工前文件体检（%s）'
          % time.strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 70)
    bad = 0

    # ---- A. 走读目录树 ----
    trees = TREES[:1] if QUICK else TREES
    print('\n[A] 走读文件（坏扇区在读的时候现形）')
    for label, root in trees:
        found, n, nbytes, dt = read_tree(label, root)
        io = [f for f in found if f[0] == 'IO']
        other = [f for f in found if f[0] != 'IO']
        print('  %-22s %d 个文件 / %.1f MB / %.1fs' %
              (label, n, nbytes / 1e6, dt))
        for kind, p, detail in io:
            print('    ★★ I/O 错误 [%s] %s -- %s' % (kind, p, detail))
            bad += 1
        for kind, p, detail in other:
            print('    （%s：%s -- %s）' % (kind, p, detail))
        if not found:
            print('    -> 全读通，没有 I/O 错误')

    # ---- B. import 探针（子进程：段错误也抓得到）----
    print('\n[B] import 探针（解释器 = %s）' % PY)
    for label, code in PROBES:
        ok, detail = probe(label, code)
        if ok is True:
            print('  [OK]   %-22s %s' % (label, detail))
        elif ok is None:
            print('  [--]   %-22s %s' % (label, detail))
        else:
            print('  [FAIL] %-22s %s' % (label, detail))
            bad += 1

    # ---- C. dmesg ----
    print('\n[C] dmesg 扫描（mmc / ext4 / I-O / segfault）')
    ok, hits = dmesg_scan()
    if ok is None:
        print('  [--]   %s' % hits)
    elif ok:
        print('  [OK]   没有相关报错')
    else:
        for ln in hits:
            print('  ★ %s' % ln)
        bad += 1

    # ---- D. 余量 ----
    print('\n[D] 磁盘余量')
    for p in ('/', '/home/xilinx', '/dev/shm'):
        try:
            st = os.statvfs(p)
            free = st.f_bavail * st.f_frsize
            print('  %-14s 可用 %.0f MB' % (p, free / 1e6))
            if p != '/dev/shm' and free < 200e6:
                print('    ★ 可用不足 200MB —— 写盘（PNG/日志）会开始失败')
                bad += 1
        except Exception as e:                           # noqa: BLE001
            print('  %-14s 查不了：%s' % (p, e))

    print('\n' + '=' * 70)
    if bad:
        print('结论：**有 %d 项发现**（见上面 ★）。I/O 错误 / 段错误 = 实锤，'
              '按 known_issues 分级处置：先别硬撑，重烧备卡优先。' % bad)
        return 1
    print('结论：体检全过 -> 继续 pl_probe -> load_bit.py -> 游戏那条链。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
