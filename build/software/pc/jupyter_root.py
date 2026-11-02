# -*- coding: utf-8 -*-
"""jupyter_root.py — 通过板上的 Jupyter 服务（**以 root 运行**）执行命令

★ 什么时候需要它：板子的系统密码（/etc/shadow）丢了/被改了，`sudo` 用不了，
  但 Jupyter 还在跑、而且它是 root 起的（本板：`jupyter-notebook --allow-root`）。
  Jupyter 自己的口令存在它自己的配置文件里，跟 /etc/shadow **互不相干** ——
  所以系统密码废了，Jupyter 往往还是进得去的。这条路 = 用 Jupyter 当"root 后门"
  把系统密码改回来，或者直接干需要 root 的活。

  2026-09-26 实测就是栽在这上面：`verify_onboard.sh` 全程 sudo 失效。
  查 auth.log 才发现之前那些 sudo 成功**根本不是密码对**，而是吃了 sudo 的
  **缓存票据**（日志里只有 `session opened`、没有 `pam_unix(sudo:auth)` 那一行）；
  票据一过期 / 一重启，真密码才被要求，而 `xilinx` 是错的。

★ 为什么不用 Node / websocket 库：Jupyter 的内核只能通过 **WebSocket** 执行代码，
  而 WS 握手要带 `Cookie` 头 —— Node 内建的 WebSocket（WHATWG 规范）不允许自定义
  请求头，Python 也没有内建 WS 客户端。为了不引依赖，这里**手写最小 WS 客户端**
  （握手 + 文本帧 + 掩码），总共几十行，够用就行。

用法（PC 上，Git Bash）：
    python software/pc/jupyter_root.py -c "id"                     # 跑一条 shell 命令
    python software/pc/jupyter_root.py -c "echo xilinx:xilinx | chpasswd"   # 例子：改回密码
    python software/pc/jupyter_root.py --py "import os; print(os.geteuid())"
    python software/pc/jupyter_root.py -c "id" --password xilinx --host 192.168.2.99:9090

参数：默认口令 xilinx、默认 192.168.2.99:9090（都可用 --password/--host 覆盖）。
退出码：0 = 内核执行完（命令自身成败看它打印的 RC）；1 = 连接/协议/超时之类失败。
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time

DEFAULT_HOST = '192.168.2.99:9090'
DEFAULT_PW = 'xilinx'


# ------------------------------------------------------------------ HTTP 小工具
def _http(host, method, path, cookies=None, body=None, headers=None, timeout=20,
          ctype='application/json'):
    """极简 HTTP/1.1 客户端。返回 (状态码, 响应头 dict, 正文 bytes)。

    故意不用 urllib：这里要精确控制 Cookie/Upgrade 头，还要在同一个 socket 上
    接着做 WebSocket 升级，用标准库反而绕。
    """
    h = {'Host': host, 'Accept': '*/*'}
    if cookies:
        h['Cookie'] = '; '.join('%s=%s' % (k, v) for k, v in cookies.items())
    if body is not None:
        h['Content-Type'] = ctype
        h['Content-Length'] = str(len(body))
    if headers:
        h.update(headers)
    req = '%s %s HTTP/1.1\r\n' % (method, path)
    req += ''.join('%s: %s\r\n' % (k, v) for k, v in h.items())
    req += '\r\n'

    s = socket.create_connection((host.split(':')[0], int(host.split(':')[1])),
                                 timeout=timeout)
    s.sendall(req.encode() + (body if body else b''))
    buf = b''
    while b'\r\n\r\n' not in buf:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    head, _, rest = buf.partition(b'\r\n\r\n')
    lines = head.decode('utf-8', 'replace').split('\r\n')
    status = int(lines[0].split()[1])
    hdr = {}
    for ln in lines[1:]:
        if ':' in ln:
            k, v = ln.split(':', 1)
            hdr.setdefault(k.strip().lower(), []).append(v.strip())
    # 正文：按 Content-Length 收够
    n = int(hdr.get('content-length', ['0'])[0] or 0)
    while len(rest) < n:
        c = s.recv(65536)
        if not c:
            break
        rest += c
    s.close()
    return status, hdr, rest[:n] if n else rest


def _merge_cookies(jar, hdr):
    for sc in hdr.get('set-cookie', []):
        kv = sc.split(';', 1)[0]
        if '=' in kv:
            k, v = kv.split('=', 1)
            jar[k.strip()] = v.strip()
    return jar


def login(host, password, timeout=20):
    """走 /login 表单登录（不是 token），拿回 cookie 罐和 _xsrf。"""
    jar = {}
    st, hdr, body = _http(host, 'GET', '/login', timeout=timeout)
    _merge_cookies(jar, hdr)
    html = body.decode('utf-8', 'replace')
    xsrf = ''
    marker = 'name="_xsrf" value="'
    if marker in html:
        xsrf = html.split(marker, 1)[1].split('"', 1)[0]
    if not xsrf:
        # ★ 2026-10-01 补：板上有"登录页 500、但照样下发 _xsrf cookie"的状态
        #   （实测 GET /login -> 500 空正文 + Set-Cookie: _xsrf=2|...）。
        #   tornado 的 csrf 校验是**双提交**（表单里的值 == cookie 里的值），
        #   所以 cookie 值原样带回表单即可通过 —— 实测拿到 302 + username- 会话 cookie。
        #   （那台 500 的根因待查；先把恢复通道在这种状态下打通。）
        xsrf = jar.get('_xsrf', '')
        if not xsrf:
            raise RuntimeError('登录页里找不到 _xsrf，cookie 里也没有（Jupyter 版本不同？）')
    # ★ 必须**表单编码**，不能发 JSON：Jupyter 的登录处理器用 get_argument('password')
    #   取口令，只认 application/x-www-form-urlencoded。发 JSON 它读到空串 -> 401
    #   （第一版就是这么被拒的，而 curl 用 -d 发的是表单，所以我先前 curl 能过）。
    from urllib.parse import urlencode
    payload = urlencode({'password': password, '_xsrf': xsrf}).encode()
    st, hdr, body = _http(host, 'POST', '/login', cookies=jar, body=payload,
                          headers={'X-XSRFToken': xsrf}, timeout=timeout,
                          ctype='application/x-www-form-urlencoded')
    _merge_cookies(jar, hdr)
    if st not in (200, 302):
        raise RuntimeError('登录失败：HTTP %d（口令不对？）' % st)
    # 登录成功的标志：cookie 里多出了 username-... 那一项
    if not any(k.startswith('username-') for k in jar):
        raise RuntimeError('登录没拿到会话 cookie（口令不对？HTTP %d）' % st)
    return jar, xsrf


# ------------------------------------------------------------------ 最小 WS 客户端
def _ws_handshake(host, path, cookies, timeout=25):
    """做完 WebSocket 升级，返回 (socket, 残留字节)。"""
    key = base64.b64encode(os.urandom(16)).decode()
    req = ('GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n'
           'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
           'Sec-WebSocket-Version: 13\r\nOrigin: http://%s\r\n'
           'Cookie: %s\r\n\r\n'
           % (path, host, key, host,
              '; '.join('%s=%s' % (k, v) for k, v in cookies.items())))
    s = socket.create_connection((host.split(':')[0], int(host.split(':')[1])),
                                 timeout=timeout)
    s.sendall(req.encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        c = s.recv(65536)
        if not c:
            raise RuntimeError('WS 握手时连接被关掉')
        buf += c
    head, _, rest = buf.partition(b'\r\n\r\n')
    if b' 101 ' not in head.split(b'\r\n')[0]:
        raise RuntimeError('WS 握手被拒：%s' % head.split(b'\r\n')[0].decode('utf-8', 'replace'))
    return s, rest


def _ws_send(sock, payload):
    """发一个掩码文本帧（客户端必须掩码）"""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        hdr = struct.pack('!BB', 0x81, 0x80 | n)
    elif n < 65536:
        hdr = struct.pack('!BBH', 0x81, 0x80 | 126, n)
    else:
        hdr = struct.pack('!BBQ', 0x81, 0x80 | 127, n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(hdr + mask + masked)


def _ws_recv_msg(sock, buf, timeout=30):
    """收一条完整 WS 文本消息（处理分片、ping/pong），返回 (文本, 新的残留字节)"""
    sock.settimeout(timeout)
    chunks = []
    fin = False
    while not fin:
        # 先把帧头收齐
        while len(buf) < 2:
            buf += sock.recv(65536)
        b0, b1 = buf[0], buf[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        ln = b1 & 0x7F
        pos = 2
        if ln == 126:
            while len(buf) < pos + 2:
                buf += sock.recv(65536)
            ln = struct.unpack('!H', buf[pos:pos + 2])[0]
            pos += 2
        elif ln == 127:
            while len(buf) < pos + 8:
                buf += sock.recv(65536)
            ln = struct.unpack('!Q', buf[pos:pos + 8])[0]
            pos += 8
        if masked:
            while len(buf) < pos + 4:
                buf += sock.recv(65536)
            mkey = buf[pos:pos + 4]
            pos += 4
        while len(buf) < pos + ln:
            buf += sock.recv(65536)
        data = buf[pos:pos + ln]
        buf = buf[pos + ln:]
        if masked:
            data = bytes(b ^ mkey[i % 4] for i, b in enumerate(data))

        if opcode == 0x8:                      # close
            raise RuntimeError('对端发了 close 帧')
        if opcode == 0x9:                      # ping -> 回一个掩码 pong
            mkey = os.urandom(4)
            ech = bytes(b ^ mkey[i % 4] for i, b in enumerate(data))
            sock.sendall(b'\x8a' + bytes([0x80 | len(data)]) + mkey + ech)
            continue
        if opcode == 0xA:                      # pong
            continue
        chunks.append(data)
    return b''.join(chunks).decode('utf-8', 'replace'), buf


# ------------------------------------------------------------------ 内核执行
def create_kernel(host, jar, xsrf, timeout=30):
    st, hdr, body = _http(host, 'POST', '/api/kernels', cookies=jar,
                          body=json.dumps({'name': 'python3'}).encode(),
                          headers={'X-XSRFToken': xsrf}, timeout=timeout)
    if st not in (200, 201):
        raise RuntimeError('建内核失败：HTTP %d %s' % (st, body[:200]))
    return json.loads(body)['id']


def delete_kernel(host, jar, xsrf, kid, timeout=20):
    try:
        _http(host, 'DELETE', '/api/kernels/' + kid, cookies=jar,
              headers={'X-XSRFToken': xsrf}, timeout=timeout)
    except Exception:
        pass     # 清理失败无所谓，别让收尾掩盖真正的结果


def terminal_alive(host, jar, name, timeout=10):
    """那个终端还在吗（用 REST 查，**不碰 WS** —— 这是唯一安全的存活探测）"""
    try:
        st, hdr, body = _http(host, 'GET', '/api/terminals/' + name, cookies=jar,
                              timeout=timeout)
        return st == 200
    except Exception:
        return False


def run_as_root(host, password, code, timeout=60, quiet=False, max_bytes=524288):
    r"""核心：登录 -> 建**终端** -> 通过 WS 驱动这个 root shell -> 打印输出 -> 删终端

    ★ 为什么用"终端"而不是"内核"（2026-09-26 实测）：
      本板的 python3 内核对**起不来** —— `/api/kernels` 返回 201、状态卡在 `starting`
      约 20 秒后直接变 None。而 Jupyter 的 WS 处理器在 `open()` 里要**等内核就绪**，
      内核起不来 -> 那头永远不发 101 -> 客户端**静默超时**（第一版就是这么卡住的，
      看着像网络问题，其实是内核死了）。终端（/api/terminals）开出来就是一个现成的
      root PTY，**没有启动过程**，而且给的是一条真正的 shell。
      （Jupyter 是 root 起的：`jupyter-notebook --allow-root`。）

    ★★★ 客户端把 Jupyter **整个搞死过**，所以这一版处处设防（2026-09-26 晚）：
      Jupyter Server 是**单线程 Tornado**，一个 handler 出未捕获异常就可能带走整个
      服务。当时发生的事：我先试了错路径 `/api/terminals/websocket/<name>`（404），
      正确路径是 `/terminals/websocket/<name>`；等我用正确路径连上时，那个终端
      **已经自己关掉了**（日志：`EOF on FD 34; stopping reading` / `Terminal 1 closed`），
      我再往里发消息 -> terminado `assert self.terminal is not None` 抛**未捕获异常**
      -> Jupyter 死掉，而且它 systemd 是 `Type=forking` 没有 PIDFile，systemd 全程
      以为它活着（`is-active` 说 active，端口 9090 却没人听）。
      从此定的规矩（都在下面代码里）：
        ① 路径先试**对的**那个（/terminals/websocket/<name>），别拿 404 当热身；
        ② 连 WS **之前**先用 REST 确认终端还活着，并静置 1.5s 看它稳不稳；
        ③ **只发一条消息**（不 set_size、不 stty），发得越少越安全；
        ④ 收发全程有界：单次 recv 超时、总字节上限、总墙钟上限；
        ⑤ 终端一死立刻放弃（绝不"再试一次发消息"）；
        ⑥ 任何一步出错都先 DELETE 终端再抛，不留垃圾。
      还有一条"哨兵"技巧：命令末尾 `echo __DONE_<hex>_$?__`，**PTY 回显的是字面
      `$?`、真正执行输出的是数字**，所以用正则 `__DONE_<hex>_(\d+)__` 匹配就不会
      被回显那一行误命中 —— 这样连"关回显"都不需要。

    返回 0 = 命令跑完且拿到退出码（命令自身的成败看打印的 RC=...）。
    """
    import re
    import uuid
    jar, xsrf = login(host, password)
    if not quiet:
        print('[Jupyter] 登录成功（%s）' % host)

    last_err = None
    for attempt in (1, 2):
        name = create_terminal(host, jar, xsrf)
        if not quiet:
            print('[Jupyter] 终端 %s 已开（第 %d 次尝试）' % (name, attempt))
        # ② 静置后先确认它还活着 —— 本板实测终端会"开出来就自己关掉"
        time.sleep(1.5)
        if not terminal_alive(host, jar, name):
            print('[Jupyter] ★ 终端刚开出来就没了（本板见过这毛病），换一个')
            last_err = '终端开出来就自己关了'
            continue

        # ① 正确路径优先；另一个只是兜底
        sock, buf = None, b''
        for tmpl in ('/terminals/websocket/%s', '/api/terminals/websocket/%s'):
            try:
                sock, buf = _ws_handshake(host, tmpl % name, jar)
                if not quiet:
                    print('[Jupyter] WS 连上：%s' % (tmpl % name))
                break
            except Exception as e:
                last_err = e
                sock = None
        if sock is None:
            delete_terminal(host, jar, xsrf, name)
            print('[Jupyter] ★ 终端 WS 握手失败：%s' % str(last_err)[:120])
            continue

        # ②b 临发之前**再**查一次（REST，不碰 WS）。上一次在 1.5s 睡眠之前，
        #     这一次就在发送前 —— 把"终端死了我们还往里发"的竞态窗口压到最小。
        #     这个窗口正是把 Jupyter 搞死过的那件事（terminal 已 close，on_message
        #     里 assert self.terminal is not None 炸掉 -> 整个 notebook 服务退出）。
        if not terminal_alive(host, jar, name):
            print('[Jupyter] ★ 握手刚成功、终端就没了（竞态），不发命令，换一个')
            sock.close()
            delete_terminal(host, jar, xsrf, name)
            last_err = '终端在握手后死掉'
            continue

        # ③ 只发这一条：命令 + 哨兵（回显里的 $? 不会被正则命中）
        hexid = uuid.uuid4().hex[:8]
        pattern = re.compile(r'__DONE_%s_(\d+)__' % hexid)
        try:
            _ws_send(sock, json.dumps(
                ['stdin', '%s\necho __DONE_%s_$?__\n' % (code, hexid)]))
        except Exception as e:
            print('[Jupyter] ★ 发命令失败：%s' % str(e)[:120])
            sock.close()
            delete_terminal(host, jar, xsrf, name)
            continue

        # ④ 有界地收，直到哨兵出现
        out, rc, total = [], None, 0
        deadline = time.time() + timeout
        while time.time() < deadline and total < max_bytes:
            try:
                txt, buf = _ws_recv_msg(sock, buf,
                                        timeout=min(10, max(1, deadline - time.time())))
            except Exception as e:
                print('[Jupyter] 收输出停住了：%s' % str(e)[:120])
                break
            total += len(txt)
            try:
                m = json.loads(txt)
            except ValueError:
                continue
            if isinstance(m, list) and m and m[0] == 'stdout':
                out.append(m[1])
                joined = ''.join(out)
                hit = pattern.search(joined)
                if hit:
                    rc = hit.group(1)
                    sys.stdout.write(joined[:hit.start()])
                    sys.stdout.flush()
                    break
            elif isinstance(m, list) and m and m[0] in ('disconnect', 'error'):
                print('[Jupyter] 终端断了：%s' % m)
                break
        else:
            if total >= max_bytes:
                print('[Jupyter] ★ 输出超过 %d 字节，主动断开（防把板子拖垮）' % max_bytes)
            else:
                print('[Jupyter] ★ 等哨兵超时（%ds）—— 命令多半没跑完' % timeout)

        # ⑥ 收尾
        try:
            sock.close()
        except Exception:
            pass
        delete_terminal(host, jar, xsrf, name)
        if rc is not None:
            print('RC=%s' % rc)
            if not quiet:
                print('[Jupyter] 终端已删除（不留痕）')
            return 0
        last_err = '没等到哨兵（命令没跑完/终端中途没了）'
        print('[Jupyter] ★ %s' % last_err)

    raise RuntimeError('两次尝试都没成功：%s' % str(last_err)[:160])


def create_terminal(host, jar, xsrf, timeout=20):
    st, hdr, body = _http(host, 'POST', '/api/terminals', cookies=jar, body=b'{}',
                          headers={'X-XSRFToken': xsrf}, timeout=timeout)
    if st not in (200, 201):
        raise RuntimeError('开终端失败：HTTP %d %s' % (st, body[:200]))
    return json.loads(body)['name']


def delete_terminal(host, jar, xsrf, name, timeout=20):
    try:
        _http(host, 'DELETE', '/api/terminals/' + name, cookies=jar,
              headers={'X-XSRFToken': xsrf}, timeout=timeout)
    except Exception:
        pass     # 清理失败无所谓，别让收尾掩盖真正的结果


def run_code_via_kernel(host, password, code, timeout=60, quiet=False):
    """备用路径：通过**内核**执行 Python 代码。

    ★ 本板暂时用不上（python3 内核起不来，见 run_as_root 的说明），留着是因为
      内核能跑纯 Python（不经过 shell），换块板子/换台机器可能就它能用。
    """
    jar, xsrf = login(host, password)
    kid = create_kernel(host, jar, xsrf)
    if not quiet:
        print('[Jupyter] 内核已建：%s' % kid)
    session = base64.b64encode(os.urandom(16)).hex()
    sock, buf = _ws_handshake(host, '/api/kernels/%s/channels?session_id=%s'
                              % (kid, session), jar)
    req = {
        'header': {'msg_id': 'exec-%s' % session[:12], 'username': 'root',
                   'session': session, 'msg_type': 'execute_request',
                   'version': '5.3',
                   'date': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())},
        'parent_header': {}, 'metadata': {}, 'channel': 'shell', 'buffers': [],
        'content': {'code': code, 'silent': False, 'store_history': False,
                    'user_expressions': {}, 'allow_stdin': False,
                    'stop_on_error': True},
    }
    _ws_send(sock, json.dumps(req))
    rc = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            txt, buf = _ws_recv_msg(sock, buf,
                                    timeout=min(20, max(1, deadline - time.time())))
        except Exception as e:
            print('[Jupyter] 收消息出错：%s' % str(e)[:150])
            break
        for line in txt.split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            mt = (m.get('header') or {}).get('msg_type')
            c = m.get('content') or {}
            if mt == 'stream':
                sys.stdout.write(c.get('text', ''))
            elif mt == 'error':
                print('[Jupyter] 内核里抛异常：%s' % c.get('ename'))
            elif mt == 'execute_reply':
                rc = c.get('status')
                break
    try:
        sock.close()
    except Exception:
        pass
    delete_kernel(host, jar, xsrf, kid)
    return 0 if rc == 'ok' else 1


def _wrap_shell(cmd):
    """把一条 shell 命令包成"在 root 内核里跑、并把退出码打印出来"的 Python 代码

    ★ 用字符串拼接而不是 % 格式化：生成出来的代码里自己有 %d，
      一用 % 就会跟外面的占位符打架（第一版就栽在这）。
    """
    return (
        'import subprocess, sys\n'
        'r = subprocess.run(' + repr(cmd) + ', shell=True, capture_output=True)\n'
        'sys.stdout.write(r.stdout.decode("utf-8", "replace"))\n'
        'sys.stderr.write(r.stderr.decode("utf-8", "replace"))\n'
        'print("RC=" + str(r.returncode))\n')


def main():
    ap = argparse.ArgumentParser(description='通过板上 Jupyter（root 运行）执行命令')
    ap.add_argument('-c', '--cmd', help='要执行的 shell 命令')
    ap.add_argument('--py', help='直接执行的 Python 代码')
    ap.add_argument('--host', default=DEFAULT_HOST, help='默认 ' + DEFAULT_HOST)
    ap.add_argument('--password', default=DEFAULT_PW, help='Jupyter 口令，默认 xilinx')
    ap.add_argument('--timeout', type=int, default=60)
    a = ap.parse_args()

    if not a.cmd and not a.py:
        ap.error('要么给 -c "<shell 命令>"，要么给 --py "<python 代码>"')
    if a.py:
        # 终端是 shell，不是内核：把 Python 代码 base64 递过去再解回来，
        # 免得代码里的引号/换行被 shell 和 PTY 行规程啃掉。
        code = ('echo %s | base64 -d | python3 -'
                % base64.b64encode(a.py.encode()).decode())
    else:
        code = a.cmd
    try:
        return run_as_root(a.host, a.password, code, timeout=a.timeout)
    except Exception as e:
        print('★ 失败：%s' % e)
        return 1


if __name__ == '__main__':
    sys.exit(main())
