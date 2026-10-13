#!/usr/bin/env python3
"""Exercise the ZX81-side machine code without a ZX81.

Runs pingpong/pingpong.bin and service/service.bin in a Z80 emulator with
the ZXpand+ serial ports faked, and checks what they send, receive and
print. sertest/sertest.bin runs as the slave of a scripted server,
aytone/aytone.bin against a fake AY on ports $CF/$0F, and music/music.bin
against both. Needs the 'z80' package, e.g. in a venv:

    python3 -m venv .venv && .venv/bin/pip install z80

    python3 tools/simulate.py
"""
import os
import struct
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
        self.screen, self.at = None, (0, 0)
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
            if self.screen is not None:         # ... and where it lands, after PRINT AT
                y, x = self.at
                if y < 24:
                    self.screen[y][x] = val
                self.at = (y + (x + 1) // 32, (x + 1) % 32)
            return
        if lo == 0x98:                          # PRINT AT stub: line, then column
            self.at = (val, 0)
            return
        if lo == 0x97:
            self.at = (self.at[0], val)
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


class FakeAY:
    """AY on the ZXpand+: $CF selects a register (and reads it back), $0F writes it.

    present=False models no chip answering: reads float high ($FF).
    """

    MASK = [0xFF, 0x0F, 0xFF, 0x0F, 0xFF, 0x0F, 0x1F, 0xFF,
            0x1F, 0x1F, 0x1F, 0xFF, 0xFF, 0x0F, 0xFF, 0xFF]

    def __init__(self, m, present=True):
        self.present, self.sel, self.regs, self.writes = present, 0, [0] * 16, []
        m.set_output_callback(self.out)
        m.set_input_callback(self.inp)

    def out(self, port, val):
        lo = port & 0xFF
        if lo == 0xCF:
            self.sel = val & 0x0F
        elif lo == 0x0F:
            self.regs[self.sel] = val & self.MASK[self.sel]
            self.writes.append((self.sel, val))
        else:
            raise AssertionError(f'unexpected port {port:#06x}')

    def inp(self, port):
        assert port & 0xFF == 0xCF, hex(port)
        return self.regs[self.sel] if self.present else 0xFF


def zxpand_and_ay(m, rx):
    """A fake ZXpand+ serial port and a fake AY on the same machine."""
    fz = FakeZXpand(m, rx, trickle=255)
    ay = FakeAY(m)
    def out(port, val):
        (fz.out if port & 0xFF in (0x07, 0x97, 0x98, 0x99) else ay.out)(port, val)
    def inp(port):
        return ay.inp(port) if port & 0xFF == 0xCF else fz.inp(port)
    m.set_output_callback(out)
    m.set_input_callback(inp)
    return fz, ay


# the ROM's PRINT AT, as far as it matters here: line B and column C are noted (OUT $98, $97),
# and DF_CC (16398) is left pointing at the cell, here always $5000
PRINT_AT = bytes([0x78, 0xD3, 0x98, 0x79, 0xD3, 0x97, 0x21, 0x00, 0x50, 0x22, 0x0E, 0x40, 0xC9])
CELL = 0x5000


def machine(binfile):
    m = z80.Z80Machine()
    m.set_memory_block(16514, open(os.path.join(ROOT, binfile), 'rb').read())
    m.set_memory_block(0x0010, b'\xD3\x99\xC9')          # RST $10 -> OUT ($99),A : RET
    m.set_memory_block(0x08F5, PRINT_AT)                  # PRINT AT: line B, column C
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


def usr_ticking(m, fz, addr, slice_ticks=20000):
    """Like usr, but FRAMES also ticks every slice_ticks, as in SLOW mode."""
    m.halted = False
    m.set_memory_block(0x7EFE, bytes([RET_TRAP & 0xFF, RET_TRAP >> 8]))
    m.sp, m.pc = 0x7EFE, addr
    for _ in range(200_000):
        m.ticks_to_stop = slice_ticks
        m.run()
        if m.pc in (RET_TRAP, RET_TRAP + 1):
            return m.bc
        fz.frames = (fz.frames - 1) & 0x7FFF
        fz._store_frames()
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

    # service: a credit ('k') for every 64 bytes stored, so zxservice never gets 256 ahead
    for n_bytes, want_k in [(0, 0), (63, 0), (64, 1), (130, 2), (600, 9), (1100, 16)]:   # 1024 is all it stores
        m = machine('service/service.bin')
        fz = FakeZXpand(m, framed(bytes(65 + i % 26 for i in range(n_bytes))))
        n = usr(m, 16514)
        check(f'service sends {want_k} credit(s) for a {n_bytes}-byte reply',
              n == min(n_bytes, 1024) and bytes(fz.tx) == b'k' * want_k, f'N={n} tx={bytes(fz.tx)!r}')

    # sertest: hello, 'R' tests reported back byte for byte, 'Q'
    def rcmd(delay, n, flags=0):
        return b'R' + struct.pack('<HHB', delay, n, flags)

    def report(tx):
        """tx after the hello: 'r', a 'k' per 64 bytes, then 'D' and the report."""
        tx = bytes(tx)
        assert tx[:1] == b'r', tx[:8]
        d = tx.index(b'D')
        count, elap, av0, avmax, extra = struct.unpack('<HHBBH', tx[d + 1:d + 9])
        return dict(credits=tx[1:d].count(b'k'), junk=len(tx[1:d].replace(b'k', b'')),
                    count=count, av0=av0, avmax=avmax, extra=extra,
                    got=tx[d + 9:d + 9 + count], rest=tx[d + 9 + count:])

    # Everything is queued up front, so the 'Q' that follows the test bytes is
    # read as data or drained as extra; the ZX81 then times out waiting for
    # a command (BC=1).
    payload = bytes(range(250, 256)) + bytes(range(0, 200))       # 206 bytes
    for name, delay, n, flags, rx in [
        ('all bytes', 0, 206, 0, payload),
        ('stops at n, counts the extra', 0, 100, 0, payload),
        ('fewer than n (idle timeout)', 0, 300, 0, payload),
        ('n clamped to 4096', 0, 5000, 0, payload[:5]),
        ('wait before reading', 10, 206, 0, payload),
        ('ask $C5 before every byte', 0, 206, 1, payload),
    ]:
        m = machine('sertest/sertest.bin')
        fz = FakeZXpand(m, rcmd(delay, n, flags) + rx + b'Q', trickle=255)
        r = usr_ticking(m, fz, 16514)
        rep = report(fz.tx[2:])
        want = (rx + b'Q')[:n]
        extra = len(rx) + 1 - len(want)
        check(f'sertest {name}',
              r == 1 and bytes(fz.tx[:2]) == b'H\x02' and rep['got'] == want and rep['count'] == len(want)
              and rep['extra'] == extra and rep['credits'] == len(want) // 64 and rep['junk'] == 0
              and rep['av0'] == len(rx) + 1 and rep['rest'] == b'',
              f"BC={r} {dict((k, v) for k, v in rep.items() if k not in ('got', 'rest'))}")
    m = machine('sertest/sertest.bin')
    fz = FakeZXpand(m, rcmd(0, 6) + payload[:6], trickle=3)
    usr_ticking(m, fz, 16514)
    rep = report(fz.tx[2:])
    check('sertest batch mode reads 3 at a time', rep['got'] == payload[:6] and rep['av0'] == 3,
          f"av0={rep['av0']} avmax={rep['avmax']}")
    m = machine('sertest/sertest.bin')
    fz = FakeZXpand(m, b'Q')
    check('sertest quits on Q', usr_ticking(m, fz, 16514) == 0 and bytes(fz.tx) == b'H\x02')
    m = machine('sertest/sertest.bin')
    fz = FakeZXpand(m, b'Z')
    check('sertest unknown command', usr_ticking(m, fz, 16514) == 2 and m.memory[16517] == ord('Z'))
    m = machine('sertest/sertest.bin')
    fz = FakeZXpand(m, b'')
    check('sertest gives up after 30 hellos', usr_ticking(m, fz, 16514) == 1 and bytes(fz.tx) == b'H\x02' * 30,
          f'tx={len(fz.tx)}')

    # music: 15-byte frames (R0-R12, R13, checksum) played one per frame tick
    def frame(regs, r13=0xFF):
        body = bytes(regs) + bytes([r13])
        return body + bytes([-sum(body) & 0xFF])

    END = frame([0] * 13, 0xFE)
    tune = [frame([i, 1, 0, 0, 0, 0, 0, 0x3E, 15 - i, 0, 0, 0, 0], 8 if i == 2 else 0xFF)
            for i in range(5)]

    def play(rx, last_k=0xFFFF):
        m = machine('music/music.bin')
        m.set_memory_block(16421, bytes([last_k & 0xFF, last_k >> 8]))
        fz, ay = zxpand_and_ay(m, rx)
        r = usr_ticking(m, fz, 16514)
        stats = [peek16(m, a) for a in (16520, 16522, 16524, 16526)]   # played bad under late
        return r, stats, fz, ay

    r, st, fz, ay = play(b''.join(tune) + END)
    r13 = [v for reg, v in ay.writes if reg == 13]
    check('music plays 5 frames and stops at the end marker',
          r == 0 and st[:2] == [5, 0] and r13 == [8] and bytes(fz.tx) == b'kq'
          and ay.regs[8] == 0 and ay.regs[7] == 0xFF
          and [v for reg, v in ay.writes if reg == 0] == [0, 1, 2, 3, 4],
          f'BC={r} stats={st} tx={bytes(fz.tx)!r} r13={r13}')
    bad = bytearray(tune[1])
    bad[3] ^= 0x40
    r, st, fz, ay = play(tune[0] + bytes(bad) + b''.join(tune[2:]) + END)
    check('music drops a frame with a bad checksum',
          r == 0 and st[:2] == [4, 1] and [v for reg, v in ay.writes if reg == 0] == [0, 2, 3, 4],
          f'BC={r} stats={st}')
    # a lost or added byte throws the framing off by one: after two bad frames in
    # a row MUSIC slides over the stream until it finds a frame again
    long_tune = [frame([i, 1, 0, 0, 0, 0, 0, 0x3E, 15, 0, 0, 0, 0]) for i in range(60)]
    stream = b''.join(long_tune) + END

    def resync(name, data, played, bad_frames):
        r, st, fz, ay = play(data)
        r0 = [v for reg, v in ay.writes if reg == 0]
        check(f'music resyncs after {name}',
              r == 0 and st[:2] == [played, bad_frames] and r0 == sorted(set(r0)) and r0[-1] == 59
              and bytes(fz.tx) == b'k' * (len(data) // 60) + b'q' and fz.rx == [],
              f'BC={r} stats={st} last={r0[-1:]} k={bytes(fz.tx).count(b"k")} of {len(data) // 60}')
    resync('a changed byte (the frame is dropped)', stream[:15 * 20 + 7] + bytes([stream[15 * 20 + 7] ^ 0x55]) + stream[15 * 20 + 8:], 59, 1)
    resync('a lost byte', stream[:15 * 20 + 7] + stream[15 * 20 + 8:], 58, 2)
    resync('an added byte', stream[:15 * 20 + 7] + b'\x42' + stream[15 * 20 + 7:], 59, 2)
    resync('stale bytes before the tune', b'\x11' * 7 + stream, 59, 2)
    resync('two lost bytes', stream[:15 * 10 + 3] + stream[15 * 10 + 4:15 * 40 + 9] + stream[15 * 40 + 10:], 56, 4)
    # the spinner: a block that fills a quarter at a time and empties again, every 15 good
    # frames. The ROM's PRINT AT is asked once, on entry, for the address of its cell;
    # after that the steps are plain writes there. BASIC's print position is put back.
    spin_tune = [frame([i & 0xFF, 1, 0, 0, 0, 0, 0, 0x3E, 15, 0, 0, 0, 0]) for i in range(130)]

    def spin_run(n_frames):
        m = machine('music/music.bin')
        m.set_memory_block(16421, bytes([0xFF, 0xFF]))
        m.set_memory_block(16398, bytes([0x34, 0x12]))                                 # DF_CC
        m.set_memory_block(16441, bytes([33, 20]))                                     # S_POSN
        fz, ay = zxpand_and_ay(m, b''.join(spin_tune[:n_frames]) + END)
        seen = []
        m.halted = False
        m.set_memory_block(0x7EFE, bytes([RET_TRAP & 0xFF, RET_TRAP >> 8]))
        m.sp, m.pc = 0x7EFE, 16514
        for _ in range(200_000):                    # like usr_ticking, looking at the cell as it goes
            m.ticks_to_stop = 20000
            m.run()
            if not seen or seen[-1] != m.memory[CELL]:
                seen.append(m.memory[CELL])
            if m.pc in (RET_TRAP, RET_TRAP + 1):
                break
            fz.frames = (fz.frames - 1) & 0x7FFF
            fz._store_frames()
        return m, fz, seen

    m, fz, seen = spin_run(91)
    check('music spinner: fills a quarter at a time and ends empty, in the cell PRINT AT found',
          seen == [0, 1, 3, 132, 128, 129, 131, 0], f'cell={seen}')
    check('music spinner: no ROM prints per step, BASIC\'s print position is put back',
          fz.printed == [] and fz.at == (3, 16) and peek16(m, 16398) == 0x1234
          and bytes(m.memory[16441:16443]) == bytes([33, 20]),
          f'printed={fz.printed} at={fz.at} DF_CC={peek16(m, 16398):04X} S_POSN={list(m.memory[16441:16443])}')
    m, fz, seen = spin_run(120)
    check('music spinner: one full cycle (fill, empty) over 120 frames, then cleared',
          seen == [0, 1, 3, 132, 128, 129, 131, 4, 0], f'cell={seen}')

    r, st, fz, ay = play(b'\x11' * 400)
    check('music gives up when it finds no frame in 200 bytes', r == 3 and st[:2] == [0, 2] and ay.regs[8] == 0,
          f'BC={r} stats={st}')
    r, st, fz, ay = play(b'')
    check('music: nothing arrives', r == 2 and st[0] == 0 and bytes(fz.tx) == b'q', f'BC={r} stats={st}')
    r, st, fz, ay = play(b''.join(tune[:3]))
    check('music: the stream stops mid-tune', r == 4 and st[0] == 3 and st[2] == 100, f'BC={r} stats={st}')
    r, st, fz, ay = play(b''.join(tune) + END, last_k=0xFDBF)
    check('music stops on a key', r == 1 and st[0] <= 1 and ay.regs[8] == 0, f'BC={r} stats={st}')
    r, st, fz, ay = play(b''.join(tune) + END + b'junk after the end')
    check('music drains what is left after stopping', r == 0 and fz.rx == [], f'left={len(fz.rx)}')

    for name, mask, present, want in [('AY-3-8910', None, True, 1), ('YM2149', [0xFF] * 16, True, 2),
                                      ('no chip', None, False, 0)]:
        m = machine('music/music.bin')
        fz, ay = zxpand_and_ay(m, b'')
        if mask:
            ay.MASK = mask
        ay.present = present
        ay.regs[1] = 5
        r = usr(m, 16517)
        check(f'music chip detection: {name}', r == want and (ay.regs[1] == 5 or not present), f'BC={r}')

    # aytone: one tone on channel A, read back, then silence
    for period in (252, 1, 4095):
        m = machine('aytone/aytone.bin')
        ay = FakeAY(m)
        m.set_memory_block(16520, bytes([period & 0xFF, period >> 8]))
        r = usr(m, 16514)
        check(f'aytone period {period}',
              r == period and ay.regs[0] == period & 0xFF and ay.regs[1] == period >> 8
              and ay.regs[8] == 15 and ay.regs[7] == 0xFE,
              f'R={r} regs={ay.regs[:9]}')
    m = machine('aytone/aytone.bin')
    ay = FakeAY(m)
    m.set_memory_block(16520, bytes([0x34, 0x12]))     # out of range: coarse masked to 4 bits
    check('aytone masks the coarse period', usr(m, 16514) == 0x234 and ay.writes[1] == (1, 0x02))
    check('aytone quiet', usr(m, 16517) == 0 and ay.regs[8] == 0 and ay.regs[7] == 0xFF)
    m = machine('aytone/aytone.bin')
    FakeAY(m, present=False)
    check('aytone without an AY reads back 65535', usr(m, 16514) == 0xFFFF)

    # aytone chip test: an AY masks unused bits on read-back, a YM2149 doesn't
    for name, mask, want in [('AY-3-8910', None, 0x1F0F), ('YM2149', [0xFF] * 16, 0xFFFF)]:
        m = machine('aytone/aytone.bin')
        ay = FakeAY(m)
        if mask:
            ay.MASK = mask
        ay.regs[1], ay.regs[6] = 3, 7
        r = usr(m, 16526)
        check(f'aytone chip test: {name}', r == want and ay.regs[1] == 3 and ay.regs[6] == 7,
              f'BC={r:04X} R1={ay.regs[1]} R6={ay.regs[6]}')

    # aytone clock test: 10 beeps, exactly 6,500,420 T-states apart (the
    # docs and the recording analysis depend on this number)
    m = machine('aytone/aytone.bin')
    ay = FakeAY(m)
    big, vol = 10**9, []
    out = ay.out
    def timed_out(port, val):
        out(port, val)
        if port & 0xFF == 0x0F and ay.sel == 8:
            vol.append((big - m.ticks_to_stop, val))
    m.set_output_callback(timed_out)
    m.set_memory_block(0x7EFE, bytes([RET_TRAP & 0xFF, RET_TRAP >> 8]))
    m.sp, m.pc, m.ticks_to_stop = 0x7EFE, 16523, big
    while m.pc not in (RET_TRAP, RET_TRAP + 1):
        m.run()
    on = [t for t, v in vol if v]
    off = [t for t, v in vol if not v]
    check('aytone clock: 10 beeps, 6,500,420 T-states apart',
          len(on) == len(off) == 10 and {b - a for a, b in zip(on, on[1:])} == {6_500_420}
          and {f - o for o, f in zip(on, off)} == {3_250_128} and ay.regs[8] == 0,
          f'{len(on)} beeps, gaps {sorted({b - a for a, b in zip(on, on[1:])})}')

    # music browser (USR 16528): zxmusic's page is drawn into the display
    # file with the selected row inverted; a key moves the cursor, Enter
    # asks for that row and gets a tune back. Keys go in through LAST_K and
    # a stub of the ROM's DECODE that returns the character at KEYCHAR. The
    # fake server answers each request (up to its CR) with the next reply.
    sys.path.insert(0, ROOT)
    import tunelib
    rows = ['PARTIAL/                   <DIR>', 'BIONIC COMMANDO        BIONICCO+',
            'COMMANDO                COMMANDO']
    page = tunelib.page_bytes('MUSIC', rows, ['6 7 MOVE', 'Q QUIT'], 0)
    pick = tunelib.play_bytes('COMMANDO')
    framed2 = lambda b: len(b).to_bytes(2, 'little') + b

    def browser(replies, stray=b'', script=None):
        m = machine('music/music.bin')
        fz, ay = zxpand_and_ay(m, stray)
        replies = list(replies)
        out = fz.out
        def serial_out(port, val):
            out(port, val)
            if port & 0xFF == 0x07 and port >> 8 == 0xE0 and val == 0xC6 and fz.tx[-1] == 13 and replies:
                fz.rx += list(framed2(replies.pop(0)))
        m.set_output_callback(lambda port, val: (serial_out if port & 0xFF in (0x07, 0x97, 0x98, 0x99) else ay.out)(port, val))
        KEYCHAR = 0x7100
        m.set_memory_block(0x07BD, bytes([0x21, KEYCHAR & 0xFF, KEYCHAR >> 8, 0xC9]))   # DECODE
        m.set_memory_block(0x0A2A, bytes([0xC9]))                                    # CLS
        m.set_memory_block(0x08F5, bytes([0x78, 0xD3, 0x98, 0x79, 0xD3, 0x97, 0xC9]))  # PRINT AT
        m.iy = 0x4000
        fz.screen = [[0] * 32 for _ in range(24)]
        def key(ch):
            m.set_memory_block(KEYCHAR, bytes([ch or 0]))
            m.set_memory_block(16421, b'\xfe\xfe' if ch is not None else b'\xff\xff')
        key(None)
        script = script or {40: 0x71, 44: None, 80: 0x76, 84: None}
        m.halted = False
        m.set_memory_block(0x7EFE, bytes([RET_TRAP & 0xFF, RET_TRAP >> 8]))
        m.sp, m.pc = 0x7EFE, 16528
        screen = b''
        for t in range(4000):
            if t in script:
                if t == 80:
                    screen = [bytes(r) for r in fz.screen]
                key(script[t])
            m.ticks_to_stop = 20000
            m.run()
            if m.pc in (RET_TRAP, RET_TRAP + 1):
                break
            assert 16514 <= m.pc < 0x5400 or m.pc < 0x2000, f'PC {m.pc:#x}'   # our code, or a ROM stub
            fz.frames = (fz.frames - 1) & 0x7FFF
            fz._store_frames()
        buf, n = peek16(m, 16538), peek16(m, 16536)
        return m.bc, bytes(fz.tx).replace(b'k', b''), zx_text(bytes(c & 0x3F for c in m.memory[buf + 1:buf + n])), screen, \
            bytes(fz.tx)

    line = lambda scr, n: scr[n]
    r, sent, kw, scr, raw = browser([page, pick])
    check('music browser: asks for a page, then for row 2', sent == b'?\r?02\r', repr(sent))
    check('music browser: a credit (k) for every 64 bytes of a reply',
          raw == b'?\r' + b'k' * (len(page) // 64) + b'?02\r', repr(raw))
    check('music browser: picks a tune (BC=1, the keyword in the buffer)', r == 1 and kw == 'COMMANDO', f'BC={r}')
    ok = bool(scr) and all(b & 0x80 for b in line(scr, 0)) and not any(line(scr, 1)) \
        and all(b & 0x80 for b in line(scr, 3)) and not any(b & 0x80 for b in line(scr, 2)) \
        and zx_text(bytes(b & 0x7F for b in line(scr, 3))).startswith('BIONIC') \
        and all(b & 0x80 for b in line(scr, 20)) and zx_text(bytes(b & 0x7F for b in line(scr, 21)[21:31])).strip() == 'AY-3-8910'
    check('music browser: header inverse, a blank line, cursor moved down to row 2, chip in the corner', ok,
          repr(zx_text(bytes(b & 0x7F for b in line(scr, 3)))) if scr else 'no screen')
    bad = bytes([200]) + page[1:]                         # a changed row count
    stale = page[:60] + bytes(10) + page[70:]             # same length and rows, content changed
    r, sent, kw, scr, raw = browser([bad, page[:-5], stale, page, pick], stray=b'\xaa\xbb',
                               script={240: 0x71, 244: None, 280: 0x76, 284: None})
    check('music browser: drops stray bytes, throws away bad pages (row count, length, checksum)',
          r == 1 and kw == 'COMMANDO' and sent == b'?\r?\r?\r?\r?02\r', f'BC={r} sent={sent!r}')
    downs = {30 + 6 * k: (0x71 if k % 2 == 0 else None) for k in range(10)}
    r, sent, kw, scr, raw = browser([page], script={**downs, 200: 0x36, 204: None})
    check('music browser: one page - the cursor stops at the bottom, no request', r == 0 and sent == b'?\r',
          f'BC={r} {sent!r}')
    page2 = tunelib.page_bytes('MUSIC 1/2', rows, ['6 7 MOVE', 'Q QUIT'], 0, 2)
    r, sent, kw, scr, raw = browser([page2, page2], script={**downs, 200: 0x36, 204: None})
    check('music browser: two pages - running off the bottom asks for the next', r == 0 and sent == b'?\r?N\r',
          f'BC={r} {sent!r}')
    full = tunelib.page_bytes('MUSIC 1/3', ['ROW %d' % i for i in range(1, 19)], ['6 7 MOVE', 'Q QUIT'], 0, 3)
    r, sent, kw, scr, raw = browser([full], script={40: 0x36, 44: None})
    check('music browser: a full page (18 rows) is a page, not a tune pick', r == 0 and sent == b'?\r',
          f'BC={r} {sent!r}')
    r, sent, kw, scr, raw = browser([bad, bad, bad, bad])
    check('music browser: gives up after 4 bad pages (BC=2)', r == 2 and sent.count(b'?\r') == 4, f'BC={r} {sent!r}')

    # zxmusic's Z80 for .ay players: Spectrum ports, and Amstrad CPC ports
    # (data on $F4xx, then select/write on $F6xx through the PPI)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import zxmusic
    for name, code, cpc in (
            ('Spectrum', '01FDFF 3E07 ED79 06BF 3E38 ED79 C9', False),
            ('Amstrad CPC', '0107F4 3E07 ED79 06F6 3EC0 ED79 AF ED79 06F4 3E38 ED79 '
                            '06F6 3E80 ED79 AF ED79 C9', True)):
        mem = bytearray(0x10000)
        prog = bytes.fromhex(code.replace(' ', ''))
        mem[0x8000:0x8000 + len(prog)] = prog
        spec = zxmusic.Spectrum(mem)
        spec.m.sp = 0xFF00
        spec.call(0x8000)
        check(f'zxmusic .ay ports: {name} R7 write', spec.regs[7] == 0x38 and spec.cpc == cpc,
              f'R7={spec.regs[7]:02X} cpc={spec.cpc}')

    # a play routine that waits for the frame interrupt (HALT HALT), and a beeper-only tune
    prog = bytes.fromhex('7676 01FDFF 3E08 ED79 06BF 3E0F ED79 C9'.replace(' ', ''))   # HALT HALT, AY R8 = 15, RET
    mem = bytearray(0x10000)
    mem[0x38] = 0xFB                                      # EI
    mem[0x39] = 0xC9                                      # RET
    mem[0x8000:0x8000 + len(prog)] = prog
    spec = zxmusic.Spectrum(mem)
    spec.m.sp = 0xFF00
    try:
        spec.call(0x8000)
        stuck = False
    except ValueError:
        stuck = True
    spec = zxmusic.Spectrum(mem)
    spec.m.sp = 0xFF00
    log = []
    spec.call(0x8000, 50, True, log)
    check('zxmusic .ay: a routine that HALTs never returns without the frame interrupt', stuck)
    check('zxmusic .ay: with the interrupt it returns, one frame per HALT, and its AY write lands',
          len(log) == 2 and spec.regs[8] == 15, f'frames={len(log)} R8={spec.regs[8]}')
    mem = bytearray(0x10000)
    handler = bytes.fromhex('01FDFF 3E08 ED79 06BF 3E07 ED79 FB C9'.replace(' ', ''))    # IM1 handler: R8 = 7
    mem[0x38:0x38 + len(handler)] = handler
    mem[0x8000:0x8004] = bytes.fromhex('FB 76 18FD'.replace(' ', ''))                 # EI, HALT, JR back: the main loop
    spec = zxmusic.Spectrum(mem)
    spec.m.sp = 0xFF00
    log = []
    returned = spec.run_free(0x8000, 10, log)
    check('zxmusic .ay: a main-loop INIT runs free, the interrupt handler plays, one frame each',
          not returned and len(log) == 10 and log[-1][8] == 7, f'returned={returned} frames={len(log)} R8={log[-1][8] if log else None}')
    beep = bytes.fromhex('3E10 D3FE 3E00 D3FE 18F6'.replace(' ', ''))                 # OUT ($FE) in a loop
    song = dict(blocks=[(0x8000, len(beep), 0)], init=0x8000, interrupt=0, hi=0, lo=0, stack=0xFF00,
                length=100, fade=0, name='beeper')
    try:
        zxmusic.run_ay(beep, song, 100)
        msg = 'no error'
    except ValueError as e:
        msg = str(e)
    check('zxmusic .ay: a player that only drives the beeper is reported as one', msg.startswith('a beeper tune'), msg)

    # the tune library sets a beeper-only .ay aside, in unsupported/, and lists the AY one
    import tempfile, shutil

    def make_ay(code, name='Test'):
        # header(20) | songs(4) | song data(14) | points(6) | blocks(8) | strings | code
        d = bytearray(b'ZXAYEMUL' + bytes([0, 0]) + bytes(2) + bytes(8))
        def be(o, v): d[o:o + 2] = (v & 0xFFFF).to_bytes(2, 'big')
        strings = 52
        text = b'Author\0Misc\0' + name.encode() + b'\0'
        code_at = strings + len(text)
        d += bytes(strings - len(d)) + text + code
        be(12, strings - 12); be(14, strings + 7 - 14); d[16], d[17] = 0, 0
        be(18, 20 - 18)
        be(20, strings + 12 - 20); be(22, 24 - 22)             # song 1: name, data
        be(24 + 4, 100); be(24 + 6, 0); d[24 + 8], d[24 + 9] = 0, 0
        be(24 + 10, 38 - 34); be(24 + 12, 44 - 36)             # points, blocks
        be(38, 0xFF00); be(40, 0x8000); be(42, 0)              # stack, init, interrupt
        be(44, 0x8000); be(46, len(code)); be(48, code_at - 48); be(50, 0)
        return bytes(d)

    ayok = make_ay(bytes.fromhex('01FDFF 3E08 ED79 06BF 3E0F ED79 C9'.replace(' ', '')), 'Good AY')
    beep = make_ay(bytes.fromhex('3E10 D3FE 3E00 D3FE 18F6'.replace(' ', '')), 'Only Beeper')
    sample = make_ay(bytes.fromhex('11B80B 01FDFF 3E08 ED79 06BF 3E05 ED79 1B 7A B3 20EE C9'.replace(' ', '')), 'Sampled')
    import time as _time

    def put(folder, name, data, age=3600):
        path = os.path.join(folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(data)
        t = _time.time() - age
        os.utime(path, (t, t))                            # the library leaves a file alone that changed in the last minute
        return path

    tmp = tempfile.mkdtemp()
    try:
        put(tmp, 'good.ay', ayok)
        put(tmp, 'beeper.ay', beep)
        log = []
        lib = tunelib.Library(tmp, None, None, None, log=log.append)
        check('tune library: a beeper-only .ay is moved to unsupported/ and not listed',
              sorted(lib.files) == ['good.ay'] and os.listdir(os.path.join(tmp, 'unsupported')) == ['beeper.ay']
              and not os.path.exists(os.path.join(tmp, 'beeper.ay')), f'{sorted(lib.files)} {log}')
        put(tmp, 'sample.ay', sample)                      # 3,000 volume writes in INIT
        put(tmp, 'beeper.ay', beep)
        lib.scan()
        check('tune library: sampled sound (thousands of AY writes) is set aside too',
              not os.path.exists(os.path.join(tmp, 'sample.ay')) and 'sample.ay' in os.listdir(os.path.join(tmp, 'unsupported')),
              str(sorted(os.listdir(tmp))))
        check('tune library: a second beeper file with the same name is moved under a new name',
              sorted(os.listdir(os.path.join(tmp, 'unsupported'))) == ['beeper (2).ay', 'beeper.ay', 'sample.ay'],
              str(sorted(os.listdir(os.path.join(tmp, 'unsupported')))))
        check('tune library: unsupported/ is not scanned', sorted(lib.files) == ['good.ay'], str(sorted(lib.files)))

        # a file in a subfolder keeps its folder inside unsupported/
        put(tmp, os.path.join('sub', 'deep.ay'), beep)
        lib.scan()
        check('tune library: a moved file keeps its subfolder (unsupported/sub/deep.ay)',
              os.path.exists(os.path.join(tmp, 'unsupported', 'sub', 'deep.ay')), str(log[-1:]))

        # not a minute old yet: maybe still being copied, so it stays; a minute later it moves
        young = put(tmp, 'young.ay', beep, age=0)
        lib.scan()
        check('tune library: a file changed in the last minute is not moved yet',
              os.path.exists(young) and 'young.ay' not in lib.files, str(log[-1:]))
        t = _time.time() - 3600
        os.utime(young, (t, t))
        lib.scan()
        check('tune library: ... and is moved once it has settled', not os.path.exists(young), str(log[-1:]))

        # a half-copied VGM (a header, no data yet) is not moved while it is young
        hdr = bytearray(0x80)
        hdr[:4], hdr[8:12], hdr[0x34:0x38], hdr[0x74:0x78] = b'Vgm ', (0x171).to_bytes(4, 'little'), (0x4C).to_bytes(4, 'little'), (1789773).to_bytes(4, 'little')
        vgm = put(tmp, 'copying.vgm', bytes(hdr) + b'\x66', age=0)
        lib.scan()
        check('tune library: a VGM with no data yet (still being copied) stays', os.path.exists(vgm), str(log[-1:]))

        # a file music.conf names is never moved
        put(tmp, 'named.ay', beep)
        conf = os.path.join(tmp, 'music.conf')
        open(conf, 'w').write('BEEPY=named.ay\n')
        lib2 = tunelib.Library(tmp, conf, None, None, log=log.append)
        lib2.scan()
        check('tune library: a file music.conf names stays where it is, and is not listed',
              os.path.exists(os.path.join(tmp, 'named.ay')) and 'named.ay' not in lib2.files, str(sorted(os.listdir(tmp))))
    finally:
        shutil.rmtree(tmp)

    # an AY tune whose main loop writes the border and whose interrupt handler plays is not a beeper tune
    handler = bytes.fromhex('01FDFF 3E08 ED79 06BF 3E0A ED79 FB C9'.replace(' ', ''))
    loop = bytes.fromhex('FB 3E02 D3FE 18FC'.replace(' ', ''))                 # EI; LD A,2; OUT ($FE),A; JR back
    mem = bytearray(0x10000)
    mem[0x38:0x38 + len(handler)] = handler
    mem[0x8000:0x8000 + len(loop)] = loop
    spec = zxmusic.Spectrum(mem)
    spec.m.sp = 0xFF00
    spec.run_free(0x8000, 100, [])
    check('zxmusic .ay: a border loop with an AY handler is not taken for a beeper tune',
          spec.beeper > 1000 and spec.ay_writes >= 99 and not zxmusic.is_beeper(spec), f'beeper={spec.beeper} ay={spec.ay_writes}')
    loop = bytes.fromhex('3EF3 D3FE 3C 18FB'.replace(' ', ''))                  # OUT ($FE),A with A counting through $F4/$F6
    mem = bytearray(0x10000)
    mem[0x8000:0x8000 + len(loop)] = loop
    spec = zxmusic.Spectrum(mem)
    spec.m.sp = 0xFF00
    spec.run_free(0x8000, 20, [])
    check('zxmusic .ay: OUT ($FE),A with A = $F4/$F6 is the beeper, not the CPC', not spec.cpc and spec.beeper > 1000,
          f'cpc={spec.cpc} beeper={spec.beeper}')

    # stray bytes are not credits: a reply or a stream stops after the timeout even while they keep arriving
    import pty, threading, time as t2
    for what in ('reply', 'stream'):
        master, slave = pty.openpty()
        fd, old = zxmusic.open_port(os.ttyname(slave), 38400)
        stop = threading.Event()
        def babble():
            while not stop.is_set():
                os.write(master, b'x')
                t2.sleep(0.1)
        th = threading.Thread(target=babble, daemon=True)
        th.start()
        out = []
        call = (lambda: out.append(zxmusic.send_reply(fd, bytes(600)))) if what == 'reply' else \
               (lambda: out.append(zxmusic.stream(fd, [bytes(14)] * 400, 10)))
        t0 = t2.monotonic()
        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        worker.join(8)                                    # a stuck call fails the check instead of hanging the tests
        took = t2.monotonic() - t0
        stop.set()
        th.join(timeout=1)
        check(f'zxmusic serial: a {what} gives up while stray bytes keep arriving',
              not worker.is_alive() and took < 6, f'{what} took {took:.1f} s: {str(out)[:50]}')
        if worker.is_alive():
            continue                                      # leave the stuck thread and its fds behind
        os.close(fd); os.close(master); os.close(slave)

    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
