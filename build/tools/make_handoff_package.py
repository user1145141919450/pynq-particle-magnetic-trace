# -*- coding: utf-8 -*-
"""make_handoff_package.py —— 一键生成交接压缩包（2026-10-31 立）

产物（与项目以前的手动备份格式一致）：
    D:/Desktop/PYNQ/backups/pynq-magnetic-field-demo_<日期>_<标签>/
    D:/Desktop/PYNQ/backups/pynq-magnetic-field-demo_<日期>_<标签>.zip
内容：工程根镜像（排除构建缓存/日志/临时大文件）+ claude-memory/ +
      板上标定 v3_calib_handoff.json + BACKUP_INFO.md + .verify.json（逐文件 md5 双向校验）

用法：
    python tools/make_handoff_package.py 2026-10-31_demo-fallback
"""
import hashlib
import json
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEM = (r"C:\Users\24487\.claude\projects"
       r"\d--Desktop-PYNQ-pynq-magnetic-field-demo\memory")
BACKUPS = r"D:\Desktop\PYNQ\backups"

SKIP_DIRS = {
    '.git', '__pycache__', '.Xil', 'proj', 'logs', 'handoff-context',
    'xsim.dir', 'NA', 'probe_fmt', 'probe_hdmi', 'backup', '.claude',
    # ★ 2026-11-01：重复包/构建中间件不进交接包；report/photos 是交付图，保留
    'dist',
}
SKIP_SUFFIX = (
    '.wdb', '.jou', '.pb', '.log', '.pyc', '.backup.log',
    '.backup.jou', '.TIMING_FAILED',
)
SKIP_NAMES = {'rufus-4.15.exe', 'vivado-boards-master.zip', 'diag_e.wdb',
              'tmp_dbg_ball.py', 'tmp_bridge_check.py',
              '_fig_test.png', 'shop_prompt_test.png'}
# 今天的有用临时脚本/补丁（虽然叫 _tmp_ / _fp_，但交接要带上）
KEEP_NAMES = {
    '_tmp_impl_retry.tcl', '_board_accept.sh', '_deploy_fp.sh',
    '_fp_accept.sh', '_fp_prof.py', '_pack_bench.py', '_remap_bench.py',
    '_wip_frame_pump_v22.patch', '_wip_A_preview_cache.patch',
}


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def keep(rel, name):
    if name in KEEP_NAMES:
        return True
    # ★ 2026-11-01：report/ 里 md_to_pdf 的中间 HTML（design_report.build_*）
    if '.build_' in name and name.endswith('.html'):
        return False
    if name.startswith('_tmp_'):
        return False
    if name in SKIP_NAMES:
        return False
    if name.startswith('r18_') or name.startswith('frame_raw_') \
            or name.startswith('vmap_'):
        return False
    return not name.lower().endswith(SKIP_SUFFIX)


def main(argv):
    tag = argv[1] if len(argv) > 1 else '2026-10-31_demo-fallback'
    base = 'pynq-magnetic-field-demo_%s' % tag
    dst = os.path.join(BACKUPS, base)
    zpath = dst + '.zip'
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)
    n = 0
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        rel = os.path.relpath(dp, ROOT)
        for fn in fns:
            if not keep(rel, fn):
                continue
            src = os.path.join(dp, fn)
            if not os.path.isfile(src):
                continue
            d = os.path.join(dst, rel, fn) if rel != '.' else os.path.join(dst, fn)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(src, d)
            n += 1

    # 交接说明：docs/handoff_info_<日期>.md -> 包根 BACKUP_INFO.md
    info = os.path.join(ROOT, 'docs',
                        'handoff_info_%s.md' % tag.split('_')[0])
    if os.path.isfile(info):
        shutil.copy2(info, os.path.join(dst, 'BACKUP_INFO.md'))
        n += 1
    # 板上标定（交接用）
    cal = os.path.join(ROOT, '_tmp_v3_calib.json')
    if os.path.isfile(cal):
        shutil.copy2(cal, os.path.join(dst, 'v3_calib_handoff.json'))
        n += 1
    # Claude 记忆（可选，给用 Claude 接手的队友）
    if os.path.isdir(MEM):
        md = os.path.join(dst, 'claude-memory')
        os.makedirs(md, exist_ok=True)
        for fn in os.listdir(MEM):
            p = os.path.join(MEM, fn)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(md, fn))
                n += 1

    # 逐文件 md5 双向校验（源 vs 包内；记忆/标定单独算）
    bad = 0
    for dp, dns, fns in os.walk(dst):
        for fn in fns:
            if fn in ('BACKUP_INFO.md', '.verify.json'):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, dst)
            if rel.startswith('claude-memory') or rel == 'v3_calib_handoff.json':
                continue
            s = os.path.join(ROOT, rel)
            if os.path.isfile(s) and md5(s) != md5(p):
                bad += 1
                print('  [BAD] %s' % rel)
    with open(os.path.join(dst, '.verify.json'), 'w', encoding='utf-8') as f:
        json.dump({'n': n, 'bad': bad}, f)
    print('files=%d bad=%d -> %s' % (n, bad, dst))

    # zip
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dp, _, fns in os.walk(dst):
            for fn in fns:
                p = os.path.join(dp, fn)
                z.write(p, os.path.join(base, os.path.relpath(p, dst)))
    print('zip: %s (%.1f MB)' % (zpath, os.path.getsize(zpath) / 1e6))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
