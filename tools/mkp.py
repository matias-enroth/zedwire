#!/usr/bin/env python3
"""Tiny ZX81 .p builder: BASIC text + machine code in a REM line.

    tools/mkp.py code.bin program.bas OUT.P

program.bas is plain text, one "NUMBER STATEMENT" per line. A line whose
statement is just @MC becomes a REM holding code.bin; make it the first
line (10) so the code starts at 16514, the usual REM address.

Keywords are tokenised (ZXPAND is the LPRINT token, $E1, which the
ZXpand+ ROM runs as its ZXPAND command). Numbers get the ROM's hidden
5-byte float. Deliberately small: integer literals only, no inverse or
graphics characters, and the program does not auto-run on LOAD.
"""
import sys, re, struct
CH = ' ??????????"£$:?()><=+-*/;,.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
KW = {'RND':0x40,'INKEY$':0x41,'PI':0x42,'AT':0xC1,'TAB':0xC2,'CODE':0xC4,'VAL':0xC5,'LEN':0xC6,
 'INT':0xCF,'PEEK':0xD3,'USR':0xD4,'STR$':0xD5,'CHR$':0xD6,'NOT':0xD7,'OR':0xD9,'AND':0xDA,
 '<=':0xDB,'>=':0xDC,'<>':0xDD,'THEN':0xDE,'TO':0xDF,'STEP':0xE0,'ZXPAND':0xE1,'LPRINT':0xE1,
 'STOP':0xE3,'SLOW':0xE4,'FAST':0xE5,'CLS':0xFB,'REM':0xEA,'FOR':0xEB,'GOTO':0xEC,'GOSUB':0xED,
 'INPUT':0xEE,'LOAD':0xEF,'LIST':0xF0,'LET':0xF1,'PAUSE':0xF2,'NEXT':0xF3,'POKE':0xF4,'PRINT':0xF5,
 'RUN':0xF7,'SAVE':0xF8,'RAND':0xF9,'IF':0xFA,'RETURN':0xFE}
# '?' also fills the graphics slots (codes 1-10) in CH, so look it up by hand:
# CH.index('?') would give code 1, a block graphic, not the real '?' (15).
def zc(ch): return 15 if ch == '?' else CH.index(ch)
def zfloat(n):
    if n == 0: return bytes(5)
    assert n == int(n) and 0 < n < 2**31
    n = int(n); e = n.bit_length()
    m = (n << (32 - e)) & 0x7FFFFFFF
    return bytes([128 + e]) + struct.pack('>I', m)
def tok(src):
    out = bytearray(); i = 0
    while i < len(src):
        c = src[i]
        if c == '"':
            j = src.index('"', i + 1)
            out.append(0x0B); out += bytes(zc(x) for x in src[i+1:j]); out.append(0x0B); i = j + 1
        elif c == ' ': i += 1
        elif src[i:i+2] in ('<=', '>=', '<>'): out.append(KW[src[i:i+2]]); i += 2
        elif c.isdigit():
            m = re.match(r'\d+', src[i:]).group(0)
            out += bytes(zc(x) for x in m); out.append(0x7E); out += zfloat(int(m)); i += len(m)
        elif c.isalpha():
            m = re.match(r'[A-Z][A-Z0-9]*\$?', src[i:]).group(0)
            if m in KW: out.append(KW[m])
            else: out += bytes(zc(x) for x in m)
            i += len(m)
        else: out.append(zc(c)); i += 1
    return bytes(out)
def build(lines, mc):
    prog = bytearray()
    for num, text in lines:
        body = (bytes([0xEA]) + mc) if text == '@MC' else tok(text)
        body += b'\x76'
        prog += struct.pack('>H', num) + struct.pack('<H', len(body)) + body
    d_file = 16509 + len(prog)
    dfile = b'\x76' * 25
    vars_ = d_file + len(dfile)
    e_line = vars_ + 1
    sv = bytearray(116)
    def w(addr, v): struct.pack_into('<H', sv, addr - 16393, v)
    def b(addr, v): sv[addr - 16393] = v
    b(16393, 0); w(16394, 0); w(16396, d_file); w(16398, d_file + 1); w(16400, vars_)
    w(16402, 0); w(16404, e_line); w(16406, e_line - 1); w(16408, 0); w(16410, e_line); w(16412, e_line)
    b(16414, 0); w(16415, 16477); b(16418, 2); w(16419, 0); w(16421, 0xFFFF); b(16423, 0)
    b(16424, 55); w(16425, d_file); w(16427, 0); b(16429, 0); w(16430, 0); w(16432, 0x0C8D)
    w(16434, 0); w(16436, 0xFFFF); w(16438, 0); b(16440, 0xBC); b(16441, 33); b(16442, 24)
    b(16443, 0x40); b(16444 + 32, 0x76)
    return bytes(sv) + bytes(prog) + dfile + b'\x80'
if __name__ == '__main__':
    mc = open(sys.argv[1], 'rb').read()
    lines = []
    for L in open(sys.argv[2]):
        L = L.rstrip('\n')
        if not L.strip(): continue
        n, t = L.split(' ', 1); lines.append((int(n), t))
    open(sys.argv[3], 'wb').write(build(lines, mc))
