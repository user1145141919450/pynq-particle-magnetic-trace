# -*- coding: utf-8 -*-
"""切到 addball 页，监听 30 秒触摸帧（只读+切页），再切回 index。"""
import os, select, termios, time
PORT='/dev/ttyUSB0'; BAUD=115200

def open_port():
    sp=getattr(termios,'B%d'%BAUD,termios.B115200)
    fd=os.open(PORT,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    a=termios.tcgetattr(fd); a[0]=0; a[1]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL
    a[3]=0; a[4]=sp; a[5]=sp; a[6][termios.VMIN]=0; a[6][termios.VTIME]=0
    termios.tcsetattr(fd,termios.TCSANOW,a); termios.tcflush(fd,termios.TCIOFLUSH)
    return fd

def raw(fd,s):
    os.write(fd,s.encode('gbk')+b'\xff\xff\xff')

def main():
    fd=open_port()
    raw(fd,'page addball'); time.sleep(0.8)
    print('LISTEN_START（60 秒内请点取消/红/蓝）',flush=True)
    end=time.time()+60; buf=b''
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.1)
        if not r: continue
        try: data=os.read(fd,256)
        except BlockingIOError: continue
        buf+=data
        while True:
            q=buf.find(b'\xff\xff\xff')
            if q<0: break
            frame=buf[:q]; buf=buf[q+3:]
            print('FRAME %r' % bytes(frame),flush=True)
    print('LISTEN_END',flush=True)
    raw(fd,'page index'); time.sleep(0.3)
    os.close(fd)

if __name__=='__main__': main()
