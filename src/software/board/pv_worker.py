# -*- coding: utf-8 -*-
"""pv_worker.py —— 瞄准精确虚线的**独立后台计算进程**（不碰 PYNQ / PL / MMIO）。

为什么单开进程（2026-10-29）：
  · `v3_fixedpoint.lane_fixed` 是纯 Python，一次 200~400ms；
  · 放在主线程 = 调角度时每 0.3s 冻一下（用户："一卡一卡"）；
  · 放在线程 = 抢 GIL，板端实测把主循环拖到 2.0fps（更糟）；
  · 放在**独立进程** = 第二颗 A9 核上算，主循环只收发 JSON，不冻不抢。

协议（stdin/stdout，每行一个 JSON）：
  请求：{'id':N,'bx','by','ar','sp','q','steps','nb','fld':[[x0,y0,x1,y1,b],...],
         'hol':[{...},...],'oa':[[cx,cy,hw,hh,ang],...],'pa':[[x,y,pair],...]}
  回复：{'id':N,'pts':[[x,y],...] 或 null,'goal':bool}
  停止：{'stop':true} 或关 stdin（父进程退出时管道自动关，本进程跟着退）。
"""
import json
import sys

import v3_fixedpoint as vf


def _run(req):
    rr = vf.lane_fixed(
        float(req['bx']), float(req['by']), float(req['ar']),
        float(req['sp']), int(req['q']),
        [tuple(float(v) for v in f) for f in req.get('fld') or []],
        [dict(h) for h in req.get('hol') or []], 0,
        steps=int(req.get('steps') or 2000),
        max_bounce=int(req.get('nb') or 2),
        trail_gap=6.0, max_pts=4000,
        obstacles=tuple(tuple(float(v) for v in o)
                        for o in req.get('oa') or []),
        portals=tuple(tuple(float(v) for v in p)
                      for p in req.get('pa') or []))
    pts = list(rr['pts'])
    if rr['res'] == 2 and len(pts) >= 2:
        _qx, _qy = pts[-1]
        _px, _py = pts[-2]
        _qd = ((_qx - _px) ** 2 + (_qy - _py) ** 2) ** 0.5
        if _qd > 1e-6:
            pts.append((_qx + (_qx - _px) / _qd * 70.0,
                        _qy + (_qy - _py) / _qd * 70.0))
    return {'id': req.get('id'), 'pts': pts, 'goal': rr['res'] == 'goal'}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get('stop'):
            break
        try:
            out = _run(req)
        except Exception:
            out = {'id': req.get('id'), 'pts': None, 'goal': False}
        sys.stdout.write(json.dumps(out, separators=(',', ':')) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
