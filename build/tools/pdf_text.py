# -*- coding: utf-8 -*-
"""pdf_text.py — 最小 PDF 文本提取器（无第三方库，zlib + 正则 + Tm 矩阵分行列）

用法：
    python pdf_text.py <pdf> 23-25         打印第 23~25 页（PDF 页序）
    python pdf_text.py <pdf> search Pmod   搜索含关键词的页并打印
    python pdf_text.py <pdf>               打印总页数
"""
import re
import sys
import zlib

PATH = sys.argv[1]
data = open(PATH, 'rb').read()

# ---------- 对象收集与流解压 ----------
objs = {}
for m in re.finditer(rb'(\d+)\s+(\d+)\s+obj(.*?)endobj', data, re.S):
    objs[int(m.group(1))] = m.group(3)

def stream_of(objnum):
    body = objs[objnum]
    sm = re.search(rb'stream\r?\n(.*?)\r?\nendstream', body, re.S)
    if not sm:
        return b''
    raw = sm.group(1)
    if b'FlateDecode' in body:
        try:
            return zlib.decompress(raw)
        except Exception:
            return raw
    return raw

# ---------- 页面树 ----------
def ref_of(objnum, key):
    m = re.search(rb'/' + key + rb'\s+(\d+)\s+(\d+)\s+R', objs[objnum])
    return int(m.group(1)) if m else None

root = None
for num, body in objs.items():
    if re.search(rb'/Type\s*/Catalog', body):
        root = num
        break

pages = []
if root is not None:
    stack = [ref_of(root, b'Pages')]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        body = objs.get(cur, b'')
        if re.search(rb'/Type\s*/Pages', body):
            for km in re.finditer(rb'(\d+)\s+(\d+)\s+R', body):
                stack.append(int(km.group(1)))
        elif re.search(rb'/Type\s*/Page[^s]', body):
            pages.append(cur)
else:
    for num in sorted(objs):
        if re.search(rb'/Type\s*/Page[^s]', objs[num]):
            pages.append(num)

# ---------- 内容流 tokenizer + 文本矩阵解释 ----------
TOKEN_RE = re.compile(
    rb'\[\s*(?:\((?:[^()\\]|\\.)*\)[^]]*)*\]|'          # TJ 数组（本文件不用）
    rb'\((?:[^()\\]|\\.)*\)|'                            # 字符串
    rb'<[0-9A-Fa-f\s]+>|'                                # 十六进制串
    rb'/[A-Za-z0-9_.+-]*|'                               # 名字（字体等）
    rb'-?\d+(?:\.\d+)?|'                                 # 数字
    rb'[A-Za-z\'"*]+'                                    # 操作符
)

def decode_str(txt):
    txt = txt[1:-1]
    txt = txt.replace(b'\\(', b'(').replace(b'\\)', b')').replace(b'\\\\', b'\\')
    txt = re.sub(rb'\\([0-7]{1,3})', lambda m: bytes([int(m.group(1), 8)]), txt)
    txt = txt.replace(b'\\n', b'\n').replace(b'\\r', b'')
    return txt.decode('latin-1', 'replace')

ARR_RE = re.compile(rb'\((?:[^()\\]|\\.)*\)|-?\d+(?:\.\d+)?')

def layout_records(stream):
    """返回 [(x, y, text)]，x/y 为 Tm 平移分量，y 向下取反便于排序"""
    recs = []
    fs = 10.0                       # 默认字号
    tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    lead = 0.0
    toks = TOKEN_RE.findall(stream)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == b'BT':
            tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        elif t == b'Tm' and i >= 6:
            nums = [float(x) for x in toks[i-6:i]]
            tm = nums
        elif t == b'Td' and i >= 6:
            nums = [float(x) for x in toks[i-6:i]]
            tm[4] += nums[4]
            tm[5] += nums[5]
        elif t == b'TD' and i >= 6:
            nums = [float(x) for x in toks[i-6:i]]
            tm[4] += nums[4]
            tm[5] += nums[5]
            lead = -nums[5]
        elif t == b'T*':
            tm[4] = 0.0
            tm[5] -= lead
        elif t == b'Tf' and i >= 2:
            fs = float(toks[i-1])
        elif t in (b'Tj',) and i >= 1 and toks[i-1].startswith(b'('):
            s = decode_str(toks[i-1])
            if s.strip():
                recs.append((tm[4], tm[5], s))
            tm[4] += len(s) * fs * 0.5
        elif t in (b"'",) and i >= 1 and toks[i-1].startswith(b'('):
            s = decode_str(toks[i-1])
            if s.strip():
                recs.append((tm[4], tm[5], s))
            tm[4] = 0.0
            tm[5] -= lead
        elif t == b'TL' and i >= 1:
            lead = float(toks[i-1])
        elif t.startswith(b'[') and i + 1 < len(toks) and toks[i+1] in (b'TJ', b'Tj'):
            # TJ 数组：[(A) -5 (B) 12 (C)] TJ，数字为千分字宽微调
            for part in ARR_RE.findall(t):
                if part.startswith(b'('):
                    s = decode_str(part)
                    if s.strip():
                        recs.append((tm[4], tm[5], s))
                    tm[4] += len(s) * fs * 0.5
                else:
                    tm[4] += float(part) / 1000.0 * fs
            i += 1          # 跳过 TJ 操作符本身
        i += 1
    return recs

def page_records(pageno):
    body = objs[pageno]
    refs = re.findall(rb'/Contents\s+(\d+)\s+(\d+)\s+R', body)
    out = []
    for ref in refs:
        out.extend(layout_records(stream_of(int(ref[0]))))
    return out

# ---------- 行列聚类 ----------
def render_page(recs):
    recs = [r for r in recs if r[2].strip()]
    if not recs:
        return '(空页)'
    # 按 y 聚类成行（容差 2pt）
    lines = {}
    for x, y, s in recs:
        placed = False
        for ly in lines:
            if abs(ly - y) < 2.0:
                lines[ly].append((x, s))
                placed = True
                break
        if not placed:
            lines[y] = [(x, s)]
    out_lines = []
    for y in sorted(lines, reverse=True):
        items = sorted(lines[y])
        parts = []
        prev_x = None
        for x, s in items:
            if prev_x is not None:
                gap = x - prev_x
                if gap > 14.0:
                    parts.append('  ║  ')
                elif gap > 2.0:
                    parts.append(' ')
            parts.append(s)
            prev_x = x
        out_lines.append(''.join(parts))
    return '\n'.join(out_lines)

def page_text(idx):
    if idx - 1 >= len(pages):
        return '(页不存在，共 %d 页)' % len(pages)
    return render_page(page_records(pages[idx - 1]))

# ---------- 主流程 ----------
def main():
    if len(sys.argv) < 2:
        print('用法: python pdf_text.py <pdf> [页码|search 关键词]')
        return
    mode = sys.argv[2] if len(sys.argv) > 2 else 'count'

    if mode == 'count' or len(sys.argv) == 2:
        print(f'总页数约 {len(pages)}')
    elif mode == 'search':
        kw = sys.argv[3].lower()
        hits = []
        for idx, p in enumerate(pages, 1):
            recs = page_records(p)
            text = ' '.join(s for _, _, s in recs)
            if kw in text.lower():
                hits.append(idx)
        print('含关键词的页:', hits)
        for h in hits:
            print(f'===== PAGE {h} =====')
            print(page_text(h))
    else:
        for p in mode.split(','):
            if '-' in p:
                a, b = p.split('-')
                for i in range(int(a), int(b) + 1):
                    print(f'===== PAGE {i} =====')
                    print(page_text(i))
            else:
                print(f'===== PAGE {p} =====')
                print(page_text(int(p)))

if __name__ == '__main__':
    main()
