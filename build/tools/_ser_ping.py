# -*- coding: utf-8 -*-
"""串口屏无干扰检查：115200 发 get baud，只读回复；不改亮度、不碰屏幕。
用法（板上 root）：sudo /usr/bin/python3 /tmp/_ser_ping.py
"""
import os
import select
import termios
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200
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
os.write(fd, b'get baud\xff\xff\xff')
out = b''
end = time.time() + 0.9
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.08)
    if r:
        try:
            out += os.read(fd, 64)
        except BlockingIOError:
            pass
os.close(fd)
if out:
    print('OK baud=115200 resp=%s' % out.hex())
else:
    print('NO_RESPONSE baud=115200')
