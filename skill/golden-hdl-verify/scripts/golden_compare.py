#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""golden_compare.py — compare two text dumps value by value (default tolerance 0).

Typical use:
    python golden_compare.py expected.txt actual.txt
    python golden_compare.py expected.txt actual.txt --tol 0 --max-report 20

Both files are whitespace-separated records. Blank lines and lines starting
with '#' are ignored. Numeric tokens may be decimal, 0x..., 0b..., or float.

Self-check (no FPGA, no numpy needed):
    python golden_compare.py --selftest

Exit codes:
    0 = identical (within tolerance)
    1 = differences found
    2 = file / format error
"""
import argparse
import math
import os
import sys
import tempfile


def read_tokens(path):
    rows = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for lineno, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            rows.append((lineno, s.split()))
    return rows


def parse_num(tok):
    """Return (value, kind) or (None, 'str')."""
    try:
        if tok.lower().startswith(('0x', '-0x', '+0x')):
            return int(tok, 16), 'int'
        if tok.lower().startswith(('0b', '-0b', '+0b')):
            return int(tok, 2), 'int'
        return int(tok, 10), 'int'
    except ValueError:
        pass
    try:
        return float(tok), 'float'
    except ValueError:
        return None, 'str'


def _selftest():
    """用临时文件把 7 条判据跑一遍：错的一定要判错，对的一定要判对。"""
    def run(exp_text, act_text, tol=0.0):
        d = tempfile.mkdtemp(prefix='gc_selftest_')
        pe = os.path.join(d, 'exp.txt')
        pa = os.path.join(d, 'act.txt')
        with open(pe, 'w', encoding='utf-8') as f:
            f.write(exp_text)
        with open(pa, 'w', encoding='utf-8') as f:
            f.write(act_text)
        try:
            return main([pe, pa, '--tol', str(tol)])
        finally:
            for p in (pe, pa):
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(d)
            except OSError:
                pass

    cases = [
        ('完全一致',        run('1 2 3\n4 5 6\n', '1 2 3\n4 5 6\n'), 0),
        ('差 1 且 tol=0',   run('1 2 3\n', '1 2 4\n'), 1),
        ('差 1 且 tol=1',   run('1 2 3\n', '1 2 4\n', tol=1.0), 0),
        ('十六进制等于十进制', run('0x10 0b101\n', '16 5\n'), 0),
        ('注释与空行被忽略', run('# hdr\n\n1 2\n', '1 2\n'), 0),
        ('字段数不同',      run('1 2 3\n', '1 2\n'), 1),
        ('记录数不同',      run('1 2\n', '1 2\n3 4\n'), 1),
    ]
    bad = 0
    for name, got, want in cases:
        ok = (got == want)
        bad += 0 if ok else 1
        print('[selftest] %-20s %s' % (name, 'OK' if ok else
                                       'FAIL got=%r want=%r' % (got, want)))
    print('[selftest] %d/%d 通过' % (len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if '--selftest' in argv:
        return _selftest()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('expected', nargs='?')
    ap.add_argument('actual', nargs='?')
    ap.add_argument('--tol', type=float, default=0.0,
                    help='absolute tolerance for numeric fields (default 0)')
    ap.add_argument('--max-report', type=int, default=10,
                    help='max mismatches to print (default 10)')
    args = ap.parse_args(argv)
    if not args.expected or not args.actual:
        ap.error('需要 expected 与 actual 两个文件（或 --selftest）')

    try:
        exp = read_tokens(args.expected)
        act = read_tokens(args.actual)
    except OSError as e:
        print('ERROR: %s' % e)
        return 2

    if len(exp) != len(act):
        print('ERROR: record count differs: expected %d, actual %d'
              % (len(exp), len(act)))
        return 1

    n_values = 0
    bad = []
    for ri, ((eline, etoks), (aline, atoks)) in enumerate(zip(exp, act)):
        if len(etoks) != len(atoks):
            bad.append((ri, eline, aline, 'field-count',
                        '%d fields' % len(etoks), '%d fields' % len(atoks)))
            continue
        for ci, (e, a) in enumerate(zip(etoks, atoks)):
            n_values += 1
            ek, _ = parse_num(e)
            ak, _ = parse_num(a)
            if ek is None or ak is None:
                if e != a:
                    bad.append((ri, eline, aline, 'field[%d]' % ci, e, a))
            else:
                if abs(ek - ak) > args.tol:
                    bad.append((ri, eline, aline, 'field[%d]' % ci,
                                '%r' % ek, '%r' % ak))

    if not bad:
        print('PASS: %d records, %d values identical (tol=%g)'
              % (len(exp), n_values, args.tol))
        return 0

    print('FAIL: %d mismatch(es) in %d records / %d values'
          % (len(bad), len(exp), n_values))
    for ri, eline, aline, where, e, a in bad[:args.max_report]:
        print('  rec %d (exp line %d / act line %d) %s: expected=%s actual=%s'
              % (ri, eline, aline, where, e, a))
    if len(bad) > args.max_report:
        print('  ... %d more' % (len(bad) - args.max_report))
    return 1


if __name__ == '__main__':
    sys.exit(main())
