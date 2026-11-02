# -*- coding: utf-8 -*-
"""板上串口屏最小测试（不依赖 pyserial，只用 termios/select）。
用法：sudo /usr/bin/python3 _ser_test.py
"""
import os
import select
import termios
import time

PORT = '/dev/ttyUSB0'
BAUDS = (9600, 19200, 38400, 57600, 115200)


def test(baud):
    speed = getattr(termios, 'B%d' % baud, termios.B9600)
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
    # 1) get baud（TJC/Nextion 有返回）
    os.write(fd, b'get baud\xff\xff\xff')
    out = b''
    end = time.time() + 0.8
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.08)
        if r:
            try:
                out += os.read(fd, 128)
            except BlockingIOError:
                pass
    # 2) 亮度命令（链路通的话，屏会变亮/变暗，肉眼可见）
    os.write(fd, b'dim=100\xff\xff\xff')
    time.sleep(0.15)
    os.write(fd, b'dim=30\xff\xff\xff')
    time.sleep(0.15)
    os.write(fd, b'dim=100\xff\xff\xff')
    os.close(fd)
    return out


for b in BAUDS:
    try:
        r = test(b)
        print('baud=%-6d resp=%s ascii=%r (sent dim=100/30/100)' %
              (b, r.hex(), r))
    except Exception as e:
        print('baud=%-6d ERROR %s' % (b, e))
