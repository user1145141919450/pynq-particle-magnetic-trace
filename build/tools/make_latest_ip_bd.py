# -*- coding: utf-8 -*-
"""make_latest_ip_bd.py —— 生成“不锁 IP 版本”的 BD 脚本（2026.2 迁移用）

背景：`bd/m1a_capture.tcl` 里的 Xilinx IP 是**锁版本**的（processing_system7:5.5 等），
      这是 2023.2 的已知好版本，保证 2023.2 逐字节复现。
      换到 2026.2 时，这些老版本可能已从 IP 目录消失 → create_bd_cell 报
      "IP definition not found"。本工具把 `xilinx.com:ip:NAME:x.y` 的版本号**去掉**，
      让 Vivado 选当前版本可用的最新 IP；生成 `bd/m1a_capture_latest.tcl`。
      **Digilent 的两个 IP（axi_dynclk:1.2 / rgb2dvi:1.4）保留版本** —— 它们是
      工程自带 ip_repo 里的固定组件。

用法（仓库根）：
    python tools/make_latest_ip_bd.py
然后在 2026.2 环境里：
    BD_TCL=m1a_capture_latest.tcl PS7_STRICT=1 <vivado> -mode batch \
        -source build_project.tcl -tclargs bd_only
（PS7_STRICT=1 会让 PS7 参数应用失败时**硬停**，提示哪些参数在新版里没了。）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'vivado', 'ov5640_pynq', 'bd', 'm1a_capture.tcl')
DST = os.path.join(ROOT, 'vivado', 'ov5640_pynq', 'bd',
                   'm1a_capture_latest.tcl')

PAT = re.compile(r'(-vlnv\s+xilinx\.com:ip:[A-Za-z0-9_]+):[0-9][0-9.]*')


def main():
    if not os.path.isfile(SRC):
        print('找不到 %s' % SRC)
        return 1
    s = open(SRC, encoding='utf-8').read()
    out, n = PAT.subn(r'\1', s)
    header = ('// ★ 自动生成，别手改：tools/make_latest_ip_bd.py\n'
              '// 来源：m1a_capture.tcl（2023.2 锁版本版，逐字节复现用）\n'
              '// 用途：2026.2 迁移 —— Xilinx IP 不锁版本，由 Vivado 选最新；'
              'Digilent IP 仍锁 1.2/1.4\n')
    # 插在第一个注释块之后更稳：直接放最前面（Tcl 里 // 不是注释，必须用 #）
    header = header.replace('//', '#')
    with open(DST, 'w', encoding='utf-8') as f:
        f.write(header + out)
    print('生成 %s（改了 %d 处 Xilinx IP 版本号）' % (DST, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
