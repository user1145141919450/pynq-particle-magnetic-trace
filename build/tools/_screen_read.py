# -*- coding: utf-8 -*-
"""读回 TJC 串口屏当前文本（get 命令；只读，不改屏）。
用法（板上 root）：sudo /usr/bin/python3 /tmp/_screen_read.py
"""
import os
import select
import termios
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200
NAMES = ('t0', 't1', 't2', 't16', 't17',
         't4', 't5', 't6', 't9', 't10', 't11',
         't12', 't13', 't14', 't15',
         't38', 't42', 't40', 't41', 't39',
         't44', 't48', 't46', 't47', 't45')


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
    for cmd in ('get %s.txt' % name, 'get index.%s.txt' % name):
        os.write(fd, cmd.encode('gbk') + b'\xff\xff\xff')
        out = b''
        end = time.time() + 0.28
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.04)
            if r:
                try:
                    out += os.read(fd, 256)
                except BlockingIOError:
                    pass
        # TJC 文本返回：0x70 + ASCII(GBK) + FF FF FF
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
        print('%s=%s' % (n, get(fd, n)), flush=True)
    os.close(fd)


if __name__ == '__main__':
    main()
