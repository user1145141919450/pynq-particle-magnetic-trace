#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_submission_package.py — assemble a clean competition submission tree + zip.

Why this exists:
  The development repo contains build caches, logs, waveforms, temp scripts and
  internal handoff notes. Zipping it as-is is both huge and risky (absolute paths,
  stale binaries, non-ASCII filenames). This tool copies ONLY the submission
  deliverables into a new directory, checks the result, and writes a zip.

Usage:
    python tools/make_submission_package.py
    python tools/make_submission_package.py --out dist/my_submission
    python tools/make_submission_package.py --no-zip
    python tools/make_submission_package.py --include-maintainer

Default output:
    dist/pynq-magnetic-field-demo_submission_<YYYYMMDD>/
    dist/pynq-magnetic-field-demo_submission_<YYYYMMDD>.zip

Exit codes: 0 = ok, 1 = check failed, 2 = copy/zip error.

NOTE: This script never modifies the source tree; it only reads it and writes
under dist/ (or --out). Always inspect the generated tree before uploading.
"""
import argparse
import datetime
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories copied wholesale (with per-tree filters).
INCLUDE_DIRS = [
    'report',
    'skill',
    'software',
    'sim',
    'tools',
    'docs',
    'vivado/ov5640_pynq',
]

# Root files always copied if present.
ROOT_FILES = ['README.md', 'LICENSE', 'LICENSE.md']

# Optional maintainer files (excluded by default: they contain internal notes).
MAINTAINER_FILES = ['CLAUDE.md', 'HANDOFF.md']

EXCLUDE_DIR_NAMES = {
    '.git', '__pycache__', '.Xil', 'proj', 'xsim.dir', '.claude',
    'handoff-context', 'logs', 'NA', 'dist', 'backups', 'backup',
    '.venv-pc', 'probe_fmt', 'probe_hdmi', 'vb',
}

EXCLUDE_SUFFIXES = (
    '.wdb', '.jou', '.pb', '.pyc', '.TIMING_FAILED', '.log',
    '.exe', '.zip', '.bin', '.bmp', '.jpg', '.jpeg', '.png',
    '.gif', '.rar', '.7z', '.iso', '.img', '.html',
)

EXCLUDE_NAME_PREFIXES = ('_tmp', '_wip', '_fp_', '_build_', '_board_accept', '_internal')
EXCLUDE_NAME_EXACT = {
    'rufus-4.15.exe', 'vivado-boards-master.zip', 'diag_e.wdb',
    'frame_raw_d.bin', 'v3axi.wdb', 'v3sim.wdb',
}

# In docs/, keep only submission-relevant docs (skip internal handoff notes and
# superseded drafts). docs/reports/ is copied in full.
DOCS_ALLOWED_SUFFIXES = ('.md', '.svg', '.pdf', '.txt')
DOCS_REPORTS_DIR = os.path.join('docs', 'reports')
DOCS_ALLOWED_FILES = {
    'build_and_migrate_2026.x.md',
    'demo_script_3min.md',
    'demo_shoot_script.md',
    'ext_board_schematic.md',
    'game_rules_quickref.md',
    'project_intro_2026-10-25_final.md',
    'vision_ip_design.md',
}

# In tools/, keep scripts/text/json; binaries are excluded by suffix above.
TOOLS_ALLOWED_SUFFIXES = (
    '.py', '.sh', '.tcl', '.json', '.txt', '.md', '.ps1', '.xdc',
)

CRED_PATTERNS = [
    re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),
    re.compile(r'\bghp_[A-Za-z0-9]{20,}'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bsk-[A-Za-z0-9]{24,}'),
]
ABS_PATH = re.compile(r'(?<![A-Za-z0-9_])[A-Za-z]:[\\/]')

REQUIRED = [
    'README.md',
    'report/design_report.md',
    'report/llm_collaboration.md',
    'report/README.md',
    'skill/README.md',
    'vivado/ov5640_pynq/build_project.tcl',
    'vivado/ov5640_pynq/build_after_patch.tcl',
    'vivado/ov5640_pynq/timing_gate.tcl',
    'vivado/ov5640_pynq/bd/m1a_capture.tcl',
    'software/board/load_bit.py',
    'docs/reports/README.md',
]


def skip_dir(rel):
    parts = rel.replace('\\', '/').split('/')
    return any(p in EXCLUDE_DIR_NAMES for p in parts)


def keep_file(rel, name):
    """Decide whether a file under an included dir should be copied."""
    if name.startswith('.'):
        return False
    if name in EXCLUDE_NAME_EXACT:
        return False
    if any(name.startswith(p) for p in EXCLUDE_NAME_PREFIXES):
        return False
    reln = rel.replace('\\', '/')
    # Report figures/photos are deliverables: keep images and vectors here.
    if reln.startswith('report/photos/') or reln.startswith('report/figures/'):
        return True
    # Do not ship superseded report drafts (design_report_v13/v14/...).
    if reln.startswith('report/') and re.match(r'design_report_v\d+\.(pdf|docx)$', name):
        return False
    low = name.lower()
    if low.endswith(EXCLUDE_SUFFIXES):
        return False
    if any(ord(c) > 127 for c in name):
        return False          # competition rule: ASCII filenames only
    reln = rel.replace('\\', '/')
    if reln.startswith('docs/'):
        if reln.startswith(DOCS_REPORTS_DIR.replace('\\', '/') + '/'):
            return True
        if not low.endswith(DOCS_ALLOWED_SUFFIXES):
            return False
        return name in DOCS_ALLOWED_FILES
    if reln.startswith('tools/'):
        return low.endswith(TOOLS_ALLOWED_SUFFIXES)
    return True


def copy_tree(dst_root, include_maintainer=False):
    copied = []
    skipped_nonascii = []
    for rel_dir in INCLUDE_DIRS:
        src_dir = os.path.join(ROOT, rel_dir)
        if not os.path.isdir(src_dir):
            print('WARN: missing dir %s' % rel_dir)
            continue
        for dp, dns, fns in os.walk(src_dir):
            rel_dp = os.path.relpath(dp, ROOT)
            if skip_dir(rel_dp):
                dns[:] = []
                continue
            dns[:] = [d for d in dns
                      if d not in EXCLUDE_DIR_NAMES
                      and not any(ord(c) > 127 for c in d)]
            for fn in sorted(fns):
                src = os.path.join(dp, fn)
                rel = os.path.relpath(src, ROOT)
                if not keep_file(rel, fn):
                    if any(ord(c) > 127 for c in fn):
                        skipped_nonascii.append(rel)
                    continue
                dst = os.path.join(dst_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)

    for fn in ROOT_FILES:
        src = os.path.join(ROOT, fn)
        if os.path.isfile(src):
            dst = os.path.join(dst_root, fn)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(fn)

    if include_maintainer:
        for fn in MAINTAINER_FILES:
            src = os.path.join(ROOT, fn)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dst_root, fn))
                copied.append(fn)

    if skipped_nonascii:
        print('NOTE: skipped non-ASCII filenames (competition rule):')
        for r in skipped_nonascii[:20]:
            print('  - %s' % r)
    return copied


def check_tree(dst_root):
    errs = []
    print('\n[1] required files')
    for rel in REQUIRED:
        p = os.path.join(dst_root, rel)
        if os.path.isfile(p):
            print('  [OK]   %s' % rel)
        else:
            print('  [ERR]  missing %s' % rel)
            errs.append('missing ' + rel)

    print('\n[2] forbidden dirs / non-ASCII names')
    for dp, dns, fns in os.walk(dst_root):
        for d in dns:
            if d in EXCLUDE_DIR_NAMES:
                print('  [ERR]  forbidden dir %s'
                      % os.path.relpath(os.path.join(dp, d), dst_root))
                errs.append('forbidden dir ' + d)
        for f in fns:
            if any(ord(c) > 127 for c in f):
                print('  [ERR]  non-ASCII filename %s'
                      % os.path.relpath(os.path.join(dp, f), dst_root))
                errs.append('non-ascii name ' + f)

    print('\n[3] credentials / absolute paths in text files')
    text_ext = ('.py', '.sh', '.tcl', '.v', '.sv', '.vh', '.xdc', '.md',
                '.txt', '.json', '.ps1')
    abs_warn = []
    for dp, dns, fns in os.walk(dst_root):
        for f in fns:
            if not f.lower().endswith(text_ext):
                continue
            p = os.path.join(dp, f)
            try:
                s = open(p, 'r', encoding='utf-8', errors='replace').read()
            except OSError:
                continue
            for pat in CRED_PATTERNS:
                if pat.search(s):
                    rel = os.path.relpath(p, dst_root)
                    print('  [ERR]  credential-like string in %s' % rel)
                    errs.append('credential ' + rel)
            for m in ABS_PATH.finditer(s):
                rel = os.path.relpath(p, dst_root)
                # Most hits are comments/docstrings with example paths. Treat
                # as WARN so the package can still be built; review them.
                if rel.endswith(('.py', '.sh', '.tcl', '.ps1')):
                    abs_warn.append('%s: %s' % (rel, m.group(0)))
                    break
    if abs_warn:
        print('  [WARN] absolute paths in scripts (review; usually comments):')
        for w in abs_warn[:30]:
            print('         %s' % w)
        if len(abs_warn) > 30:
            print('         ... %d more' % (len(abs_warn) - 30))

    print('\n[4] size')
    total = 0
    n = 0
    for dp, dns, fns in os.walk(dst_root):
        for f in fns:
            total += os.path.getsize(os.path.join(dp, f))
            n += 1
    print('  %d files, %.1f MB' % (n, total / 1e6))
    return errs


def write_manifest(dst_root, copied, errs, include_maintainer):
    lines = [
        '# Submission package manifest',
        '',
        '- Generated: %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        '- Source root: repository root (development tree)',
        '- Included files: %d' % len(copied),
        '- Maintainer files included: %s' % ('yes' if include_maintainer else 'no'),
        '',
        '## What is inside',
        '',
        '| Path | Content |',
        '|---|---|',
        '| `README.md` | project intro, directory mapping, quick start |',
        '| `report/` | design report + LLM collaboration record + figures |',
        '| `skill/` | reusable skill package (PYNQ safe overlay, Vivado gate, golden verify, LLM workflow, pitfalls, templates) |',
        '| `vivado/ov5640_pynq/` | RTL, BD Tcl, constraints, IP repo, deploy package, build/gate scripts |',
        '| `software/` | board scripts + PC tools |',
        '| `sim/` | simulation scripts |',
        '| `tools/` | build acceptance / IP baseline / submission check |',
        '| `docs/` | design docs, known issues, reports (raw timing/utilization/acceptance logs) |',
        '',
        '## Checks',
        '',
        '- Required files: %s' % ('PASS' if not errs else 'FAIL'),
        '- Non-ASCII filenames: checked',
        '- Credential scan: checked',
        '- Absolute paths in scripts: checked',
        '',
        '## Excluded on purpose',
        '',
        'Build caches (`proj/`, `.cache/.gen/.runs`, `xsim.dir`), logs, waveforms,',
        'temp scripts (`_tmp*`, `_wip*`), backups, internal handoff notes',
        '(`HANDOFF.md`, `CLAUDE.md` unless --include-maintainer), binary test data,',
        'and non-ASCII filenames.',
        '',
    ]
    if errs:
        lines += ['## ERRORS', ''] + ['- %s' % e for e in errs] + ['']
    with open(os.path.join(dst_root, 'submission_manifest.md'), 'w',
              encoding='utf-8') as f:
        f.write('\n'.join(lines))


def make_zip(dst_root):
    zip_path = dst_root.rstrip(os.sep) + '.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for dp, dns, fns in os.walk(dst_root):
            for f in fns:
                p = os.path.join(dp, f)
                arc = os.path.relpath(p, os.path.dirname(dst_root))
                z.write(p, arc)
    return zip_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default=None,
                    help='output directory (default: dist/<name>_<date>)')
    ap.add_argument('--no-zip', action='store_true', help='do not create zip')
    ap.add_argument('--include-maintainer', action='store_true',
                    help='also include HANDOFF.md / CLAUDE.md')
    args = ap.parse_args(argv)

    date = datetime.date.today().strftime('%Y%m%d')
    out = args.out or os.path.join(
        ROOT, 'dist', 'pynq-magnetic-field-demo_submission_%s' % date)
    if os.path.exists(out):
        print('ERROR: output already exists: %s' % out)
        print('       remove it or pass --out <new-dir>')
        return 2
    os.makedirs(out)
    print('Output: %s' % out)

    try:
        copied = copy_tree(out, args.include_maintainer)
    except Exception as e:
        print('ERROR: copy failed: %s' % e)
        return 2

    errs = check_tree(out)
    write_manifest(out, copied, errs, args.include_maintainer)

    zip_path = None
    if not args.no_zip:
        try:
            zip_path = make_zip(out)
            print('\nZIP: %s (%.1f MB)'
                  % (zip_path, os.path.getsize(zip_path) / 1e6))
        except Exception as e:
            print('ERROR: zip failed: %s' % e)
            return 2

    if errs:
        print('\nRESULT: FAIL (%d error(s)) — fix before submitting.' % len(errs))
        return 1
    print('\nRESULT: PASS — inspect the tree/zip, then submit.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
