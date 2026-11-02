# -*- coding: utf-8 -*-
"""切到 addball 页读当前页控件，再切回 index（只读+切页，不改内容）。"""
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
    os.write(fd,s.encode('gbk')+b'\xff\xff\xff'); time.sleep(0.6)

def get(fd,name,wait=0.30):
    os.write(fd,('get %s.txt'%name).encode('gbk')+b'\xff\xff\xff')
    out=b''; end=time.time()+wait
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.04)
        if r:
            try: out+=os.read(fd,256)
            except BlockingIOError: pass
    p=out.find(b'\x70'); q=out.find(b'\xff\xff\xff',p+1) if p>=0 else -1
    if p>=0 and q>p:
        try: return out[p+1:q].decode('gbk',errors='replace')
        except Exception: return out[p+1:q].hex()
    return '--'

def main():
    fd=open_port()
    print('index.t38 =',repr(get(fd,'index.t38')),flush=True)
    print('addball.t12 (inactive) =',repr(get(fd,'addball.t12')),flush=True)
    print('switch -> addball ...',flush=True)
    raw(fd,'page addball'); time.sleep(0.8)
    print('current t12 =',repr(get(fd,'t12')),flush=True)
    print('current t8  =',repr(get(fd,'t8')),flush=True)
    print('current t13 =',repr(get(fd,'t13')),flush=True)
    print('current t14 =',repr(get(fd,'t14')),flush=True)
    print('addball.t12 (active) =',repr(get(fd,'addball.t12')),flush=True)
    print('switch -> index ...',flush=True)
    raw(fd,'page index'); time.sleep(0.8)
    print('back index.t12 =',repr(get(fd,'index.t12')),flush=True)
    os.close(fd)

if __name__=='__main__': main()
