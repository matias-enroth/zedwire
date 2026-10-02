#!/usr/bin/env python3
"""Exercise the ZX81-side machine code without a ZX81.

Runs pingpong/pingpong.bin and service/service.bin in a Z80 emulator with
the ZXpand+ serial ports faked, and checks what they send, receive and
print. Needs the 'z80' package, e.g. in a venv:

    python3 -m venv .venv && .venv/bin/pip install z80

    python3 tools/simulate.py
"""
import os
import sys

try:
    import z80
except ImportError:
    sys.exit("needs the z80 emulator package. From the repo root:\n"
             "  python3 -m venv .venv && .venv/bin/pip install z80\n"
             "then run make test again (it uses .venv automatically)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZX = ' ??????????"£$:?()><=+-*/;,.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
RET_TRAP = 0x7000


class FakeZXpand:
    """Minimal model of the ZXpand+ serial port API on I/O port $07."""

    def __init__(self, m, rx, trickle=3, polls_per_frame=20):
        self.m, self.rx, self.trickle, self.ppf = m, list(rx), trickle, polls_per_frame
        self.tx, self.xbuf, self.printed = [], [], []
        self.result, self.polls, self.frames = 0, 0, 0x7FFF
        m.set_output_callback(self.out)
        m.set_input_callback(self.inp)
        self._store_frames()

    def _store_frames(self):
        self.m.set_memory_block(16436, bytes([self.frames & 0xFF, self.frames >> 8]))

    def out(self, port, val):
        lo, hi = port & 0xFF, port >> 8
        if lo == 0x99:                          # our RST $10 stub
            self.printed.append(val)
            return
        assert lo == 0x07, hex(port)
        if hi == 0x00:
            self.xbuf.clear()                   # reset transfer buffer
        elif hi == 0x40:
            self.xbuf.append(val)               # push data byte
        elif hi == 0xE0:
            if val == 0xC6:
                self.tx.append(self.xbuf[0])    # write byte to serial
            elif val == 0xC5:
                self.result = min(len(self.rx), self.trickle)   # bytes waiting
            elif val == 0xC7:
                self.result = self.rx.pop(0)    # read byte
            else:
                raise AssertionError(f'unexpected command {val:#x}')

    def inp(self, port):
        if port & 0xFF == 0x17:                 # status: never busy; tick FRAMES now and then
            self.polls += 1
            if self.polls % self.ppf == 0:
                self.frames = (self.frames - 1) & 0x7FFF
                self._store_frames()
            return 0x00
        return self.result


def machine(binfile):
    m = z80.Z80Machine()
    m.set_memory_block(16514, open(os.path.join(ROOT, binfile), 'rb').read())
    m.set_memory_block(0x0010, b'\xD3\x99\xC9')          # RST $10 -> OUT ($99),A : RET
    m.set_memory_block(RET_TRAP, b'\x76')                 # HALT
    return m


def usr(m, addr):
    m.halted = False
    m.set_memory_block(0x7EFE, bytes([RET_TRAP & 0xFF, RET_TRAP >> 8]))
    m.sp, m.pc = 0x7EFE, addr
    for _ in range(5000):
        m.ticks_to_stop = 1_000_000
        m.run()
        if m.pc in (RET_TRAP, RET_TRAP + 1):
            return m.bc
    raise AssertionError(f'USR {addr} never returned')


def peek16(m, a):
    return m.memory[a] | m.memory[a + 1] << 8


def zx_text(codes):
    return ''.join('\n' if c == 0x76 else ZX[c] for c in codes)


failures = 0


def check(name, cond, detail=''):
    global failures
    print(f"{'ok  ' if cond else 'FAIL'} {name} {detail}")
    failures += not cond


def framed(text):
    return bytes([len(text) & 0xFF, len(text) >> 8]) + text


def main():
    # pingpong: send PING\n, read until newline
    m = machine('pingpong/pingpong.bin')
    fz = FakeZXpand(m, b'PONG!\n')
    n = usr(m, 16514)
    check('pingpong sends PING\\n', bytes(fz.tx) == b'PING\n', repr(bytes(fz.tx)))
    check('pingpong receives 6 bytes', n == 6 and bytes(m.memory[16640:16646]) == b'PONG!\n')

    # service: length-prefixed receive + print
    cases = [
        ('normal reply', framed(b"It's 21:15:03\nhello, world!"), 27, 27, 'IT"S 21:15:03\nHELLO, WORLD?'),
        ('10-byte reply (length byte = \\n)', framed(b'0123456789'), 10, 10, '0123456789'),
        ('short read', bytes([50, 0]) + b'ONLY THIS', 9, 50, 'ONLY THIS'),
        ('no reply (timeout)', b'', 0, 0, ''),
    ]
    for name, rx, want_n, want_len, want_text in cases:
        m = machine('service/service.bin')
        fz = FakeZXpand(m, rx)
        n = usr(m, 16514)
        usr(m, 16517)
        text = zx_text(fz.printed)
        check(f'service {name}',
              n == want_n and peek16(m, 16520) == want_len and peek16(m, 16522) == want_n
              and text == want_text,
              f'N={n} RLEN={peek16(m, 16520)} text={text!r}')

    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
