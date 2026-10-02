#!/usr/bin/env python3
"""Generate service/table.inc: the 128-entry ASCII -> ZX81 character table
used by SERVICE's print routine.

  $FF = don't print (control characters, CR)
  $FE = newline marker; the print routine turns it into $76. The table
        avoids a literal $76 byte so the REM line 10 lives in never contains
        the ZX81's end-of-line code.
Lowercase folds to uppercase, the apostrophe and backtick become '"' (the
ZX81 has no apostrophe), and anything else unprintable becomes '?'.
"""
import sys

ZX = ' ??????????"£$:?()><=+-*/;,.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def zx_code(a):
    ch = chr(a)
    if a == 10:
        return 0xFE
    if a == 9 or ch == ' ':
        return 0x00
    if a < 32:
        return 0xFF
    if ch in "'`":
        return ZX.index('"')
    if ch == '?':
        return 0x0F
    up = ch.upper()
    if up in ZX[1:] and up != '£':
        return ZX.index(up, 1)
    return 0x0F


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'service/table.inc'
    table = [zx_code(a) for a in range(128)]
    with open(out, 'w') as f:
        for i in range(0, 128, 16):
            f.write('        db ' + ','.join('$%02X' % v for v in table[i:i + 16]) + '\n')


if __name__ == '__main__':
    main()
