#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bit_freshness.py — "部署目录里的这块 bit，是不是这次构建出来的？"

为什么需要它（本队三次真实事故）：
  · 2026-09-30：交付包里的 bit 是 **09-25 的旧产物**（那一版还没有 HDMI IP），
    而所有软件侧检查都是绿的 —— 屏幕上全黑，没人怀疑到 bit 上。
  · 一块 bit 在 125.6s 被下进去，127.8s 就被板级脚本换掉，随后读寄存器 SIGBUS。
  · 重建 BD 时静默丢掉了两个 IP，**构建全绿、台架全绿**，直到上板才发现。
    判据只有一个：**部署包与 impl_1 产物逐字节相同**。

判据（按可靠性排序）：
  1. 内容 md5（**不用 mtime**：Windows/NTFS 与 FAT 的时间粒度会骗人）
  2. 边车文件 `<name>.bit.md5`（格式 `<hex> *<文件名>`，由生成 bit 的那一步写）
  3. 三者必须一致：impl_1 产物 == 部署副本 == 边车里记的

用法：
  python3 bit_freshness.py --built build/impl_1/top.bit --deployed deploy/top.bit
  python3 bit_freshness.py --built a.bit --deployed b.bit --stage
退出码：0 一致 / 1 不一致（别上板）/ 2 判定不了（缺文件）
"""

import argparse
import hashlib
import os
import sys
import tempfile
import time

RC_OK = 0
RC_STALE = 1
RC_UNKNOWN = 2


def md5_of(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_sidecar(bit_path):
    """返回 (md5 或 None, 说明)。兼容 `md5sum` 的 `<hex> *<file>` 与 `<hex>  <file>`。"""
    side = bit_path + '.md5'
    if not os.path.isfile(side):
        return None, '没有边车 %s' % side
    try:
        with open(side) as f:
            tok = f.read().split()
    except Exception as exc:
        return None, '边车读不了（%s）' % exc
    if not tok:
        return None, '边车是空的 %s' % side
    return tok[0].strip().lower(), side


def check(built, deployed, write_sidecar=False):
    """返回 (退出码, 报告行列表)。"""
    lines = []
    for p, tag in ((built, '构建产物'), (deployed, '部署副本')):
        if not os.path.isfile(p):
            lines.append('[BIT] %s 不存在：%s —— 判定不了' % (tag, p))
            return RC_UNKNOWN, lines

    mb, md = md5_of(built), md5_of(deployed)
    sb, sb_src = read_sidecar(built)
    sbd, sbd_src = read_sidecar(deployed)

    lines.append('[BIT] 构建产物 %s' % built)
    lines.append('[BIT]   md5 = %s   size = %d   mtime = %s'
                 % (mb, os.path.getsize(built),
                    time.strftime('%Y-%m-%d %H:%M:%S',
                                  time.localtime(os.path.getmtime(built)))))
    lines.append('[BIT] 部署副本 %s' % deployed)
    lines.append('[BIT]   md5 = %s   size = %d   mtime = %s'
                 % (md, os.path.getsize(deployed),
                    time.strftime('%Y-%m-%d %H:%M:%S',
                                  time.localtime(os.path.getmtime(deployed)))))

    rc = RC_OK
    if mb != md:
        lines.append('[BIT] ★ 不一致：部署的不是这次构建出来的那块（别上板）')
        rc = RC_STALE

    for tag, val, src in (('构建产物', sb, sb_src),
                          ('部署副本', sbd, sbd_src)):
        if val is None:
            lines.append('[BIT] %s 没有可用边车（%s）' % (tag, src))
        elif val != (mb if tag == '构建产物' else md):
            lines.append('[BIT] ★ %s 的边车 md5 与文件不符（边车=%s）' % (tag, val[:8]))
            rc = RC_STALE

    if write_sidecar:
        text = '%s *%s\n' % (md, os.path.basename(deployed))
        with open(deployed + '.md5', 'w') as f:
            f.write(text)
        lines.append('[BIT] 已重写边车 %s.md5 = %s' % (deployed, md[:8]))

    if rc == RC_OK:
        lines.append('[BIT] => 一致：这块 bit 就是本次构建产物')
    return rc, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description='核对部署 bit 是否为本次构建产物')
    ap.add_argument('--built', help='构建输出（如 proj.runs/impl_1/x.bit）')
    ap.add_argument('--deployed', help='部署副本（如 deploy/x.bit）')
    ap.add_argument('--stage', action='store_true',
                    help='一致时顺手重写部署副本的边车 md5')
    ap.add_argument('--selftest', action='store_true', help='PC 上干跑')
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.built or not args.deployed:
        ap.error('--built 与 --deployed 都是必填的（除非 --selftest）')

    rc, lines = check(args.built, args.deployed, args.stage)
    for line in lines:
        print(line)
    return rc


def _selftest():
    tmp = tempfile.mkdtemp(prefix='bitfresh_')
    a = os.path.join(tmp, 'impl.bit')
    b = os.path.join(tmp, 'deploy.bit')
    with open(a, 'wb') as f:
        f.write(b'A' * 100)
    with open(b, 'wb') as f:
        f.write(b'A' * 100)

    bad = 0
    rc, _ = check(a, b)
    cases = [(rc, RC_OK, '内容相同 -> 0')]

    with open(b, 'wb') as f:
        f.write(b'B' * 100)
    rc, _ = check(a, b)
    cases.append((rc, RC_STALE, '内容不同 -> 1'))

    with open(a + '.md5', 'w') as f:
        f.write('%s *impl.bit\n' % md5_of(a))
    rc, _ = check(a, b)
    cases.append((rc, RC_STALE, '边车对但文件不同 -> 1'))

    with open(b, 'wb') as f:          # 恢复成与 a 相同
        f.write(b'A' * 100)
    with open(b + '.md5', 'w') as f:
        f.write('%s *deploy.bit\n' % md5_of(b))
    rc, _ = check(a, b)
    cases.append((rc, RC_OK, '两边一致且有边车 -> 0'))

    rc, _ = check(a, os.path.join(tmp, 'missing.bit'))
    cases.append((rc, RC_UNKNOWN, '缺文件 -> 2'))

    for got, want, name in cases:
        ok = got == want
        bad += 0 if ok else 1
        print('[selftest] %-30s %s' % (name, 'OK' if ok else
                                       'FAIL got=%r want=%r' % (got, want)))
    print('[selftest] %d/%d 通过' % (len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
