#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md_to_pdf.py — Markdown -> styled HTML -> DOCX/PDF (Microsoft Word COM).

Why this exists:
  The default HTML -> Word import ignores most <style> blocks and produces a
  plain, cramped document. This script writes every style inline (Word honours
  inline CSS), adds a cover page and page breaks, then asks Word to style
  tables, headers/footers and export both DOCX and PDF.

Usage:
    python tools/md_to_pdf.py report/design_report.md
    python tools/md_to_pdf.py report/design_report.md -o report/design_report.pdf
    python tools/md_to_pdf.py report/design_report.md --html-only
    python tools/md_to_pdf.py report/design_report.md --no-docx

Supports the subset used by this project's reports: headings, paragraphs,
tables (pipes inside backticks are handled), lists, blockquotes, fenced code,
inline code/bold/italic/links/images, horizontal rules.

Exit codes: 0 ok, 1 conversion/browser error, 2 bad input.
"""
import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

FONT = "'SimSun','Songti SC',serif"
FONT_HEAD = "'SimHei','Microsoft YaHei',sans-serif"
MONO = "Consolas,'Courier New',monospace"

S_BODY = ("font-family:%s;font-size:12pt;line-height:1.5;color:#000000;"
          "text-align:left;text-autospace:none;margin:0 0 0 0;" % FONT)
S_H2 = ("font-family:%s;font-size:16pt;font-weight:bold;color:#000000;"
        "margin:12pt 0 6pt 0;" % FONT_HEAD)
S_H2_BREAK = S_H2 + "page-break-before:always;"
S_H3 = ("font-family:%s;font-size:14pt;font-weight:bold;color:#000000;"
        "margin:10pt 0 4pt 0;" % FONT_HEAD)
S_H4 = ("font-family:%s;font-size:12pt;font-weight:bold;color:#000000;"
        "margin:8pt 0 3pt 0;" % FONT_HEAD)
S_TABLE = "border-collapse:collapse;width:100%;margin:6pt 0;font-size:10.5pt;"
S_TH = ("border:0.6pt solid #7F7F7F;padding:3pt 4pt;"
        "font-weight:bold;text-align:center;vertical-align:middle;")
S_TD = "border:0.6pt solid #7F7F7F;padding:3pt 4pt;text-align:center;vertical-align:middle;"
S_CODE = "font-family:%s;font-size:9pt;border:1.0pt solid #94A3B8;padding:6pt 8pt;white-space:pre-wrap;page-break-inside:avoid;" % MONO
S_ICODE = "font-family:%s;font-size:10.5pt;padding:0 2pt;" % MONO
S_QUOTE = ("border-left:3pt solid #4A6FA5;padding:4pt 8pt;"
           "margin:6pt 0;color:#000000;font-size:12pt;")
S_LI = "font-family:%s;font-size:12pt;line-height:1.5;margin:2pt 0;" % FONT
S_IMG = "display:block;margin:8pt auto;max-width:100%;"
S_HR = "border:none;border-top:0.6pt solid #BFBFBF;margin:10pt 0;"
S_CAPTION = ("font-family:%s;font-size:10.5pt;color:#333333;text-align:center;"
             "margin:2pt 0 10pt 0;" % FONT)
S_FIG = "text-align:center;margin:8pt 0;"


def is_cjk(ch):
    return '\u4e00' <= ch <= '\u9fff'


def smart_join(lines):
    """Join Markdown soft-wrapped lines without inserting spaces between CJK."""
    if not lines:
        return ''
    out = lines[0]
    for line in lines[1:]:
        if out and line and (is_cjk(out[-1]) or is_cjk(line[0])):
            out += line
        else:
            out += ' ' + line
    return out


def inline(s):
    """Inline Markdown -> HTML. Input must already be HTML-escaped."""
    def render_img(m):
        alt, src = m.group(1), m.group(2)
        attrs = ''
        for key in ('width', 'height'):
            mm = re.search(r'\|%s=(\d+(?:\.\d+)?)(cm|px)?' % key, alt)
            if mm:
                val, unit = float(mm.group(1)), mm.group(2) or 'px'
                px = int(round(val / 2.54 * 96)) if unit == 'cm' else int(round(val))
                attrs += ' %s="%d"' % (key, px)
        if attrs:
            alt = re.sub(r'\|(?:width|height)=\d+(?:\.\d+)?(?:cm|px)?', '', alt).strip()
        return '<img alt="%s" src="%s"%s style="%s">' % (alt, src, attrs, S_IMG)

    s = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', render_img, s)
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
               lambda m: '<a href="%s" style="color:#1F4E79;text-decoration:none;">%s</a>'
               % (m.group(2), m.group(1)), s)
    s = re.sub(r'`([^`]+)`',
               lambda m: '<code style="%s">%s</code>' % (S_ICODE, m.group(1)), s)
    s = re.sub(r'\*\*([^*]+)\*\*',
               lambda m: '<strong>%s</strong>' % m.group(1), s)
    s = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)',
               lambda m: '<em>%s</em>' % m.group(1), s)
    return s


def is_table_sep(line):
    s = line.strip()
    if not s.startswith('|'):
        return False
    cells = [c.strip() for c in s.strip('|').split('|')]
    return all(re.fullmatch(r':?-{3,}:?', c or '') for c in cells)


def split_row(line):
    """Split a Markdown table row on | but not inside `code` or escaped \\."""
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    cells, cur, in_code, i = [], [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == '`':
            in_code = not in_code
            cur.append(ch)
        elif ch == '\\' and i + 1 < len(s) and s[i + 1] == '|':
            cur.append('|')
            i += 1
        elif ch == '|' and not in_code:
            cells.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append(''.join(cur).strip())
    return cells


def render_table(header, rows, cover=False):
    width = "80%;margin-left:auto;margin-right:auto;" if cover else "100%;"
    fs = "10.5pt" if cover else "10.5pt"
    out = ['<table style="border-collapse:collapse;width:%sfont-size:%s;margin:6pt 0;">'
           % (width, fs)]
    out.append('<thead><tr>')
    for h in header:
        out.append('<th style="%s">%s</th>' % (S_TH, inline(html.escape(h))))
    out.append('</tr></thead><tbody>')
    for r in rows:
        out.append('<tr>')
        for c in r:
            out.append('<td style="%s">%s</td>' % (S_TD, inline(html.escape(c))))
        out.append('</tr>')
    out.append('</tbody></table>')
    return '\n'.join(out)


def parse_table(lines, i):
    """Parse a table starting at lines[i]; return (html, next_index)."""
    header = split_row(lines[i])
    j = i + 2
    rows = []
    while j < len(lines) and lines[j].strip().startswith('|'):
        rows.append(split_row(lines[j]))
        j += 1
    return render_table(header, rows), j


def render_cover(cover_md, subtitle_default='设计报告'):
    lines = cover_md.split('\n')
    title = ''
    subtitle = subtitle_default
    fields = []
    extra = []
    date = ''
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith('# '):
            title = s[2:].strip()
        elif s.startswith('[[') and s.endswith(']]'):
            extra.append(s)
        elif s == '设计报告':
            subtitle = s
        elif '：' in s and not s.startswith(('#', '>', '|')):
            label, value = s.split('：', 1)
            fields.append((label.strip(), value.strip()))
        elif not s.startswith(('#', '>', '|')):
            date = s

    out = ['<div style="text-align:center;margin-top:70pt;">']
    out.append('<div style="font-family:%s;font-size:22pt;font-weight:bold;'
               'color:#000000;line-height:1.4;">%s</div>'
               % (FONT_HEAD, html.escape(title)))
    out.append('<div style="font-family:%s;font-size:16pt;color:#000000;'
               'margin-top:14pt;">%s</div>' % (FONT_HEAD, html.escape(subtitle)))
    out.append('<div style="width:42%;height:1.2pt;background:#1F3864;'
               'margin:16pt auto;"></div>')
    out.append('<div style="width:82%%;margin:18pt auto 0 auto;font-family:%s;'
               'font-size:12pt;line-height:2.0;color:#000000;text-align:center;">'
               % FONT)
    for label, value in fields:
        if value:
            out.append('<p style="margin:2pt 0;"><span style="font-weight:bold;">'
                       '%s：</span>%s</p>'
                       % (html.escape(label), html.escape(value)))
        else:
            out.append('<p style="margin:2pt 0;">%s：%s</p>'
                       % (html.escape(label), '＿' * 18))
    out.append('</div>')
    if date:
        out.append('<div style="font-family:%s;font-size:12pt;color:#000000;'
                   'margin-top:26pt;">%s</div>' % (FONT, html.escape(date)))
    out.append('</div>')
    for x in extra:
        out.append('<p style="font-size:1pt;margin:0;">%s</p>' % html.escape(x))
    out.append('<div style="page-break-after:always;"></div>')
    return '\n'.join(out)


def render_body(md):
    lines = md.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out = []
    i, n = 0, len(lines)
    in_code = False
    code_lines = []
    list_type = None
    para = []
    first_h2 = True

    def flush_para():
        nonlocal para
        if para:
            txt = smart_join(para)
            rendered = inline(html.escape(txt))
            if txt.startswith('图 ') or txt.startswith('表 '):
                style = S_CAPTION
            elif '<img' in rendered:
                style = S_FIG
            else:
                style = S_BODY
            out.append('<p style="%s">%s</p>' % (style, rendered))
            para = []

    def close_list():
        nonlocal list_type
        if list_type:
            out.append('</%s>' % list_type)
            list_type = None

    while i < n:
        line = lines[i]
        s = line.strip()

        if s.startswith('```'):
            if not in_code:
                flush_para(); close_list()
                in_code = True
                code_lines = []
            else:
                out.append('<pre style="%s"><code style="%s">%s</code></pre>'
                           % (S_CODE, S_ICODE, html.escape('\n'.join(code_lines))))
                in_code = False
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue

        if not s:
            flush_para(); close_list()
            i += 1
            continue

        m = re.match(r'^(#{1,6})\s+(.*)$', s)
        if m:
            flush_para(); close_list()
            level = len(m.group(1))
            text = inline(m.group(2))
            if level == 1:
                out.append('<h1 style="font-family:%s;font-size:20pt;color:#1F3864;'
                           'text-align:center;margin:0 0 10pt 0;">%s</h1>' % (FONT, text))
            elif level == 2:
                raw = m.group(2).strip()
                need_break = raw.startswith('目录')
                style = S_H2_BREAK if need_break else S_H2
                out.append('<h2 style="%s">%s</h2>' % (style, text))
            elif level == 3:
                out.append('<h3 style="%s">%s</h3>' % (S_H3, text))
            else:
                out.append('<h4 style="%s">%s</h4>' % (S_H4, text))
            i += 1
            continue

        if re.fullmatch(r'-{3,}|\*{3,}', s):
            flush_para(); close_list()
            out.append('<hr style="%s">' % S_HR)
            i += 1
            continue

        if s.startswith('|') and i + 1 < n and is_table_sep(lines[i + 1]):
            flush_para(); close_list()
            table_html, i = parse_table(lines, i)
            out.append(table_html)
            continue

        if s.startswith('>'):
            flush_para(); close_list()
            q = []
            while i < n and lines[i].strip().startswith('>'):
                q.append(lines[i].strip()[1:].strip())
                i += 1
            out.append('<blockquote style="%s">%s</blockquote>'
                       % (S_QUOTE, inline(html.escape(smart_join(q)))))
            continue

        m = re.match(r'^(\s*)([-*]|\d+\.)\s+(.*)$', line)
        if m:
            flush_para()
            kind = 'ul' if m.group(2) in ('-', '*') else 'ol'
            if list_type != kind:
                close_list()
                out.append('<%s style="margin:3pt 0 3pt 18pt;">' % kind)
                list_type = kind
            out.append('<li style="%s">%s</li>'
                       % (S_LI, inline(html.escape(m.group(3)))))
            i += 1
            continue

        para.append(s)
        i += 1

    flush_para(); close_list()
    if in_code:
        out.append('<pre style="%s"><code style="%s">%s</code></pre>'
                   % (S_CODE, S_ICODE, html.escape('\n'.join(code_lines))))
    return '\n'.join(out)


def build_html(md, title, subtitle='设计报告'):
    m = re.search(r'^##\s+', md, re.M)
    if m:
        cover_md, body_md = md[:m.start()], md[m.start():]
        cover = render_cover(cover_md, subtitle)
    else:
        cover, body_md = '', md
    body = render_body(body_md)
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<title>%s</title></head><body style="margin:0;">%s%s</body></html>'
            % (html.escape(title), cover, body))


def sanitize_docx(path):
    """Remove team/user metadata from a DOCX after Word exports it."""
    if not os.path.isfile(path):
        return
    tmp = path + '.tmp'
    try:
        with zipfile.ZipFile(path, 'r') as zin,              zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename in ('docProps/core.xml', 'docProps/app.xml'):
                    txt = data.decode('utf-8', errors='replace')
                    txt = txt.replace('FIRE FLY', '')
                    txt = txt.replace('<dc:title>design_report</dc:title>',
                                      '<dc:title>PYNQ Particle Magnetic Trace System</dc:title>')
                    data = txt.encode('utf-8')
                zout.writestr(item, data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def find_word():
    cands = [
        r'C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE',
        r'C:\Program Files (x86)\Microsoft Office\root\Office16\WINWORD.EXE',
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def html_to_pdf_word(html_path, pdf_path, docx_path=None, toc=False,
                     header='PYNQ Particle Magnetic Trace System - Design Report',
                     heading_left=False):
    """Call tools/report_style.ps1: style the DOCX with Word COM, export PDF."""
    if not find_word():
        return False, 'Word not found'
    ps_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'report_style.ps1')
    if not os.path.isfile(ps_path):
        return False, 'report_style.ps1 not found'
    cmd = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
           '-File', ps_path,
           '-Html', os.path.abspath(html_path),
           '-Pdf', os.path.abspath(pdf_path),
           '-Docx', os.path.abspath(docx_path) if docx_path else '',
           '-Toc', '1' if toc else '0',
           '-Header', header,
           '-HeadingLeft', '1' if heading_left else '0']
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 0:
            if docx_path:
                sanitize_docx(docx_path)
            return True, ''
        return False, (p.stdout or '')[-800:] + (p.stderr or '')[-800:]
    except Exception as e:
        return False, str(e)


def find_edge():
    cands = [
        os.environ.get('EDGE_BIN', ''),
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    for name in ('msedge', 'chrome', 'chromium'):
        p = shutil.which(name)
        if p:
            return p
    return None


def html_to_pdf_edge(html_path, pdf_path):
    import pathlib
    edge = find_edge()
    if not edge:
        return False, 'Edge/Chrome not found'
    url = pathlib.Path(os.path.abspath(html_path)).as_uri()
    cmd = [edge, '--headless=new', '--disable-gpu',
           '--print-to-pdf=' + os.path.abspath(pdf_path), url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return False, str(e)
    if os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 0:
        return True, ''
    return False, 'PDF not produced (rc=%s)' % p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input')
    ap.add_argument('-o', '--output', default=None)
    ap.add_argument('--html-only', action='store_true')
    ap.add_argument('--no-docx', action='store_true', help='skip DOCX output')
    ap.add_argument('--engine', choices=('auto', 'word', 'edge'), default='auto',
                    help='auto = Word first, then Edge (default)')
    ap.add_argument('--subtitle', default='设计报告', help='cover subtitle (default: 设计报告)')
    ap.add_argument('--header', default='PYNQ Particle Magnetic Trace System - Design Report',
                    help='running header text')
    ap.add_argument('--toc', action='store_true', help='insert a TOC page and Roman/Arabic section page numbers')
    ap.add_argument('--heading-left', action='store_true',
                    help='left-align level-1 headings (default: centered)')
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        print('ERROR: no such file: %s' % args.input)
        return 2
    md = open(args.input, 'r', encoding='utf-8').read()
    out = args.output or os.path.splitext(args.input)[0] + '.pdf'
    title = os.path.splitext(os.path.basename(args.input))[0]

    html_path = os.path.splitext(out)[0] + '.build_%d.html' % os.getpid()
    open(html_path, 'w', encoding='utf-8').write(build_html(md, title, args.subtitle))
    print('HTML: %s' % html_path)
    if args.html_only:
        return 0

    docx_path = None if args.no_docx else os.path.splitext(out)[0] + '.docx'
    for _stale in (out, docx_path):
        if _stale and os.path.isfile(_stale):
            try: os.remove(_stale)
            except OSError: pass
    engines = []
    if args.engine in ('auto', 'word'):
        engines.append(('word', lambda h, p: html_to_pdf_word(
            h, p, docx_path, args.toc, args.header, args.heading_left)))
    if args.engine in ('auto', 'edge'):
        engines.append(('edge', html_to_pdf_edge))
    errors = []
    for name, fn in engines:
        print('ENGINE: %s' % name)
        ok, msg = fn(html_path, out)
        if ok:
            print('PDF: %s (%.1f MB)' % (os.path.abspath(out),
                                         os.path.getsize(out) / 1e6))
            if docx_path and os.path.isfile(docx_path):
                print('DOCX: %s (%.1f MB)' % (os.path.abspath(docx_path),
                                              os.path.getsize(docx_path) / 1e6))
            return 0
        errors.append('%s: %s' % (name, msg))
    print('ERROR: no PDF engine succeeded')
    for e in errors:
        print('  ' + e)
    return 1


if __name__ == '__main__':
    sys.exit(main())
