# -*- coding: utf-8 -*-
"""读回 TJC 串口屏 addball 页控件文本（只读，不改屏）。
用法（板上 root）：sudo /usr/bin/python3 /tmp/_screen_probe_ab.py
"""
import os
import select
import termios
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200
NAMES = ('addball.t8', 'addball.t9', 'addball.t10',
         'addball.t12', 'addball.t13', 'addball.t14',
         'addball.b0', 'addball.b1', 'addball.b2',
         'index.t38', 'index.t44')


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


def get(fd, name):
    cmd = 'get %s.txt' % name
    os.write(fd, cmd.encode('gbk') + b'\xff\xff\xff')
    out = b''
    end = time.time() + 0.30
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.04)
        if r:
            try:
                out += os.read(fd, 256)
            except BlockingIOError:
                pass
    p = out.find(b'\x70')
    q = out.find(b'\xff\xff\xff', p + 1) if p >= 0 else -1
    if p >= 0 and q > p:
        try:
            return out[p + 1:q].decode('gbk', errors='replace')
        except Exception:
            return out[p + 1:q].hex()
    return '--'


def main():
    fd = open_port()
    for n in NAMES:
        print('%s = %r' % (n, get(fd, n)), flush=True)
    os.close(fd)


if __name__ == '__main__':
    main()
