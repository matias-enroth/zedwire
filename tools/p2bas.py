#!/usr/bin/env python3
"""List the BASIC in a ZX81 .p file as text.

    tools/p2bas.py service/SERVICE.P

REM lines holding machine code are shown as a byte count rather than
dumped. The ZXpand+ ZXPAND command is stored as the LPRINT token ($E1)
and is listed as ZXPAND here.
"""
import sys

CH = ' ??????????"£$:?()><=+-*/;,.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
TOK = {0x40: 'RND', 0x41: 'INKEY$', 0x42: 'PI', 0xC0: '""', 0xC1: 'AT', 0xC2: 'TAB',
       0xC4: 'CODE', 0xC5: 'VAL', 0xC6: 'LEN', 0xC7: 'SIN', 0xC8: 'COS', 0xC9: 'TAN',
       0xCA: 'ASN', 0xCB: 'ACS', 0xCC: 'ATN', 0xCD: 'LN', 0xCE: 'EXP', 0xCF: 'INT',
       0xD0: 'SQR', 0xD1: 'SGN', 0xD2: 'ABS', 0xD3: 'PEEK', 0xD4: 'USR', 0xD5: 'STR$',
       0xD6: 'CHR$', 0xD7: 'NOT', 0xD8: '**', 0xD9: 'OR', 0xDA: 'AND', 0xDB: '<=',
       0xDC: '>=', 0xDD: '<>', 0xDE: 'THEN', 0xDF: 'TO', 0xE0: 'STEP', 0xE1: 'ZXPAND',
       0xE2: 'LLIST', 0xE3: 'STOP', 0xE4: 'SLOW', 0xE5: 'FAST', 0xE6: 'NEW', 0xE7: 'SCROLL',
       0xE8: 'CONT', 0xE9: 'DIM', 0xEA: 'REM', 0xEB: 'FOR', 0xEC: 'GOTO', 0xED: 'GOSUB',
       0xEE: 'INPUT', 0xEF: 'LOAD', 0xF0: 'LIST', 0xF1: 'LET', 0xF2: 'PAUSE', 0xF3: 'NEXT',
       0xF4: 'POKE', 0xF5: 'PRINT', 0xF6: 'PLOT', 0xF7: 'RUN', 0xF8: 'SAVE', 0xF9: 'RAND',
       0xFA: 'IF', 0xFB: 'CLS', 0xFC: 'UNPLOT', 0xFD: 'CLEAR', 0xFE: 'RETURN', 0xFF: 'COPY'}


def char(b, prev):
    if b in TOK:
        word = TOK[b]
        if not word[0].isalpha():
            return word
        return ('' if prev in ('', ' ', '(') else ' ') + word + ' '
    if b < 64:
        return CH[b]
    if 128 <= b < 192:
        return '%' + CH[b - 128]          # inverse video
    return '{%02X}' % b


def listing(data):
    d_file = data[3] | data[4] << 8
    i, end = 16509 - 16393, d_file - 16393
    while i < end:
        num = data[i] << 8 | data[i + 1]
        length = data[i + 2] | data[i + 3] << 8
        body = data[i + 4:i + 4 + length - 1]      # drop trailing $76
        i += 4 + length
        if body[:1] == b'\xEA' and any(b >= 64 and b not in TOK for b in body[1:]):
            yield f'{num} REM [{len(body) - 1} bytes of machine code]'
            continue
        out, j = '', 0
        while j < len(body):
            if body[j] == 0x7E:                     # hidden 5-byte float after a number
                j += 6
                continue
            out += char(body[j], out[-1:])
            j += 1
        yield f'{num} ' + out.rstrip()


if __name__ == '__main__':
    for line in listing(open(sys.argv[1], 'rb').read()):
        print(line)
