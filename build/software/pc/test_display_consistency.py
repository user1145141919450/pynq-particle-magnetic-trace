# -*- coding: utf-8 -*-
"""test_display_consistency.py —— 网页 /match 与串口屏文本的**同一口径**台架

由来（2026-10-31 实况）：演示时发现网页"局分 30:30"、串口屏"局分 3:3"——
同一局两边数字对不上（串口屏用了进球数，网页用 goals×GOAL_POINTS）。
根因是两套快照各自算各自的，没有任何测试钉住口径。本台架把"同一状态"
分别喂给：
  · ball_game._match_snapshot()  -> 网页 /match JSON
  · screen_out.snapshot()        -> 串口屏 t0/t1/t2/t16/t17/t12..t15/t4/t9
逐字段断言，覆盖 PLAY / 落定(grace) / PREP 三态。

跑法（PC，仓库根）：
    python software/pc/test_display_consistency.py
退出码 0 = 全过。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE)) if 'software' in HERE else HERE
BOARD = os.path.join(ROOT, 'software', 'board')
sys.path.insert(0, BOARD)

import match_flow as mf            # noqa: E402
import ball_game as bg             # noqa: E402
import screen_out as so            # noqa: E402

OK = FAIL = 0


def chk(name, cond, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  [OK]   %s %s' % (name, extra))
    else:
        FAIL += 1
        print('  [FAIL] %s %s' % (name, extra))


class FakeMF(object):
    """喂给两个 snapshot 的最小对局状态（属性与 match_flow.MatchFlow 同名）。"""
    def __init__(self, phase='PLAY', goals=None, ammo=None, points=None,
                 timer=0.0, grace=None, round=1, msg='', msg_t=1,
                 items=None, free_hole=None, sub=None, label=None):
        self.phase = phase
        self.goals = goals or {1: 0, 2: 0}
        self.ammo = ammo or {1: {1: 3, -1: 2}, 2: {1: 3, -1: 2}}
        self.points = points or {1: 30, 2: 30}
        self.timer = timer
        self.grace = grace
        self.round = round
        self.msg = msg
        self.msg_t = msg_t
        self.items = items or {1: {}, 2: {}}
        self.free_hole = free_hole or {1: 0, 2: 0}
        self.sub = sub or {1: None, 2: None}
        self._label = label

    def shop_label(self, p):
        return self._label


def compare(tag, m, a1=0.0, s1=400.0, b1=3, a2=0.0, s2=400.0, b2=4):
    print('\n== %s ==' % tag)
    web = bg._match_snapshot(m, link_n=0, mf_mod=mf)
    scr = so.snapshot(m, a1, s1, b1, a2, s2, b2)
    # 局分：网页 score 与串口屏 t16 必须同一口径（goals × GOAL_POINTS）
    chk('局分 t16 == 网页 score',
        scr['t16'] == '局%d:%d' % (web['p1']['score'], web['p2']['score']),
        '串口屏=%r 网页=%d:%d' % (scr['t16'], web['p1']['score'],
                                  web['p2']['score']))
    # 积分
    chk('积分 t17 == 网页 points',
        scr['t17'] == '积%d:%d' % (web['p1']['points'], web['p2']['points']),
        '串口屏=%r' % scr['t17'])
    # 弹药
    chk('弹药 t12-15 == 网页 red/blue',
        (scr['t12'], scr['t13'], scr['t14'], scr['t15'])
        == ('红球 %d' % web['p1']['red'], '蓝球 %d' % web['p1']['blue'],
            '红球 %d' % web['p2']['red'], '蓝球 %d' % web['p2']['blue']),
        '串口屏=%r' % ((scr['t12'], scr['t13'], scr['t14'], scr['t15']),))
    # 控球
    chk('控球 t4/t9 == 输入球号',
        scr['t4'] == '控球 %d号' % b1 and scr['t9'] == '控球 %d号' % b2,
        '串口屏=%r/%r' % (scr['t4'], scr['t9']))
    # 倒计时 / 落定（网页 timer、grace_left 都是**剩余**）
    if m.phase == 'PLAY' and m.grace is not None:
        want = '落定%d秒' % max(0, int(round(web.get('grace_left') or 0)))
        chk('落定 t2 == 网页 grace_left', scr['t2'] == want,
            '串口屏=%r 网页=%.1f' % (scr['t2'], web.get('grace_left') or 0))
    elif m.phase == 'PLAY':
        want = '倒计时%d秒' % max(0, int(round(web.get('timer') or 0)))
        chk('倒计时 t2 == 网页 timer', scr['t2'] == want,
            '串口屏=%r 网页=%.1f' % (scr['t2'], web.get('timer') or 0))
    else:
        chk('非 PLAY 倒计时为 --', scr['t2'] == '倒计时--', '串口屏=%r' % scr['t2'])
    # 局数
    chk('局数 t0', scr['t0'] == ('第%d/%d局' % (m.round, mf.MAX_ROUNDS)
                                 if m.round <= mf.MAX_ROUNDS else '加赛局'),
        '串口屏=%r' % scr['t0'])


def main():
    # 1) PLAY 普通（计时已过 18.81s -> 剩余 11.19s）
    compare('PLAY（局中，双方各 3 球）',
            FakeMF(phase='PLAY', goals={1: 3, 2: 3}, timer=18.81))
    # 2) PLAY 落定（grace 剩余 3.0s）
    compare('PLAY 落定（grace 剩余 3s）',
            FakeMF(phase='PLAY', goals={1: 3, 2: 2}, timer=19.0, grace=2.0,
                   ammo={1: {1: 0, -1: 0}, 2: {1: 0, -1: 0}}))
    # 3) PREP（局前商店）
    compare('PREP（局前）',
            FakeMF(phase='PREP', goals={1: 0, 2: 0}, timer=0.0,
                   sub={1: 'SHOP', 2: 'READY'}, label='加洞'))
    print('\n==== 小结：%d 通过 / %d 失败 ====' % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
