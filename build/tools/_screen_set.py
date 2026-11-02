# -*- coding: utf-8 -*-
"""串口屏假数据测试：GBK + tX.txt + FF FF FF。
第一组不带页面前缀；第二组带 index. 前缀（当前工程页面名 index）。
用法（板上 root）：sudo /usr/bin/python3 /tmp/_screen_set.py
"""
import os
import termios
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200

G1 = [
    ('t0', '第2/10局'),
    ('t1', '局前准备'),
    ('t2', '倒计时 7秒'),
    ('t16', '局分 1:0'),
    ('t17', '积分 30:20'),
    ('t4', '控球 2号'),
    ('t5', '角度 20°'),
    ('t6', '速度 1200'),
    ('t9', '控球 1号'),
    ('t10', '角度 -15°'),
    ('t11', '速度 1000'),
    ('t12', '红球 2'),
    ('t13', '蓝球 1'),
    ('t14', '红球 3'),
    ('t15', '蓝球 0'),
]
G2 = [
    ('t0', '第3/10局'),
    ('t1', '对局进行中'),
    ('t2', '倒计时 3秒'),
    ('t16', '局分 2:1'),
    ('t17', '积分 50:30'),
    ('t4', '控球 1号'),
    ('t5', '角度 -30°'),
    ('t6', '速度 900'),
    ('t9', '控球 2号'),
    ('t10', '角度 15°'),
    ('t11', '速度 1100'),
    ('t12', '红球 1'),
    ('t13', '蓝球 0'),
    ('t14', '红球 2'),
    ('t15', '蓝球 1'),
]


def open_port():
    speed = getattr(termios, 'B%d' % BAUD, termios.B115200)
    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = 0
    a[1] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[3] = 0
    a[4] = speed
    a[5] = speed
    a[6][termios.VMIN] = 0
    a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def send(fd, cmd):
    data = cmd.encode('gbk') + b'\xff\xff\xff'
    os.write(fd, data)
    time.sleep(0.06)
    print('SEND %s (gbk %d bytes)' % (cmd, len(data) - 3))


def main():
    fd = open_port()
    # 确保在 index 页（页面名取自编辑器页面列表）
    send(fd, 'page index')
    time.sleep(0.4)
    print('--- group 1: tX.txt ---')
    for name, text in G1:
        send(fd, '%s.txt="%s"' % (name, text))
    time.sleep(3.0)
    print('--- group 2: index.tX.txt ---')
    for name, text in G2:
        send(fd, 'index.%s.txt="%s"' % (name, text))
    time.sleep(0.5)
    os.close(fd)
    print('DONE')


if __name__ == '__main__':
    main()
