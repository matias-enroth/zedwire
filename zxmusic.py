#!/usr/bin/env python3
"""
zxmusic - streams AY music to the ZX81 (ZXpand+ AY chip) over serial.

The ZX81 side is MUSIC.P: LOAD "MUSIC:TEST" reads its colon argument via
ZXPAND "GET PARM", sends it with ZXPAND "PUT SER ...", then plays what
comes back, one frame per 50 Hz tick. LOAD "MUSIC" on its own opens a
browser of the tunes folder instead (tunelib.py).

The tune is turned into AY register frames here, so the ZX81 needs no
music code at all, just a register writer. Formats:
  .psg            AY register dumps (ZXTune's PSG output), read directly
  .ym             Atari ST register dumps (YM2/YM3/YM3b/YM5/YM6), also when
                  LHA-compressed as they usually are; the chip clock and
                  frame rate come from the file (Atari ST: 2 MHz)
  .vgm, .vgz      VGM logs with an AY-3-8910 family chip, clock from the file.
                  MSX tunes with a Konami SCC: one of its voices goes, as a
                  square wave, onto an AY channel the tune never uses
                  (| scc=N picks the voice, | scc=off or --no-scc: AY only)
  .pt3            Pro Tracker 3 / Vortex Tracker II modules, played by S.V.
                  Bulba's own Z80 PT3 player (third_party/vt2-pt3-player)
                  in the Z80 emulator. TurboSound files (two modules for two
                  AYs) play module 1, or TUNE=file.pt3 | ts=2 the other
  .ay             ZX Spectrum music with its own Z80 player code (ZXAYEMUL):
                  the player runs here in a Z80 emulator (needs the z80
                  package: .venv/bin/pip install z80) and its AY writes are
                  caught, frame by frame. TUNE=file.ay@2 picks song 2
  anything else   converted to PSG with zxtune123, if it's installed
                  (STC, ASC, SQT, PT2 and many more), and cached
  TEST            a built-in test tune: 2 s of A 440 Hz on channel A (check
                  it with a tuner: it's in tune if --ay-clock is right),
                  then a scale with a bass line and a hi-hat

Pitch: Spectrum tunes are written for a 1.7734 MHz AY. The ZXpand+ AY runs
at half the ZX81's clock: 1.625 MHz on paper, and it varies from one ZX81
to the next (measure yours with AYTONE's RUN 300 and tools/ayclock.py).
Tone, noise and envelope periods are rescaled to --ay-clock, so tunes
play in tune.

Wire protocol (see music/music.asm):
  ZX81 -> server : the keyword, ASCII (PUT SER), no line ending
                   (?, ?N, ?P, ?0, ?<number>: the browser; the reply is
                   a 2-byte length and a page in ZX81 codes, or $FF and the
                   keyword of the tune picked, and a checksum; MUSIC sends 'k'
                   after every 64 bytes and the server keeps at most 128 ahead)
  server -> ZX81 : 15-byte frames: R0..R12, R13, checksum (the 15 bytes add
                   up to 0 mod 256). R13 = $FF: don't write R13; $FE: end.
  ZX81 -> server : 'k' after every 4 frames it has taken, 'q' when it stops
The server keeps at most --window frames in flight (default 10 = 150
bytes): 256 unread bytes hard-freeze the ZXpand+.

Tunes: every tune in the tunes folder gets a keyword made from its file
name (tunelib.py), and the folder is looked at again on every request, so
new files can be played at once. music.conf is optional: KEYWORD=file
[@song] [| options] lines add names of your own, '#' comments, paths
relative to music.conf. TEST is always there.

Stdlib only (no pyserial). Usage:
    zxmusic.py --setup        once: serial port, tunes folder, AY clock
    zxmusic.py                then just this (settings in zxmusic.ini)

Sleep: if the computer goes to sleep while a tune streams, the stream stalls,
the ZX81 gives up after 2 s and this server logs "no credits for 3 s". How you
keep a machine awake depends on your setup (power settings, a screensaver, SSH,
a laptop lid), so this program leaves it alone. Two examples of running it
under a sleep lock, if you want one:
    macOS:  caffeinate -i ./zxmusic.py
    Linux:  systemd-inhibit --what=idle:sleep --who=zxmusic --why=streaming ./zxmusic.py
            (over SSH polkit asks for a password)
"""

import argparse, hashlib, math, os, select, shutil, subprocess, sys, termios, time
import tunelib

BAUDS = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400}
SPECTRUM_AY = 1_773_400
ZXPAND_AY = 1_625_000          # 3.25 MHz / 2; measure your own with tools/ayclock.py
FLEN = 15
CREDIT = 4                     # frames per 'k'
IDLE_GAP = 0.05                # seconds of silence that end a keyword
MAX_KEYWORD = 32
CREDIT_TIMEOUT = 3.0           # no 'k' for this long: the ZX81 has gone
CACHE = os.path.join(os.path.expanduser('~'), '.cache', 'zedwire')


# ---- tunes -> frames: lists of 14 values, R0..R12 and R13 (None = no write)

def load_psg(data):
    """PSG register dump -> frames, read the way ZXTune reads it
    (src/formats/chiptune/aym/psg.cpp): FF starts a frame, FE n adds n*4
    frames, FD ends, 00-0F is a register and its value. Writes go to the
    latest frame. Some files skip the 16-byte header: data from offset 4."""
    if data[:3] != b'PSG':
        raise ValueError('not a PSG file')
    regs, r13, frames, open_ = [0] * 13, None, [], False
    i = 4 if len(data) > 4 and data[4] == 0xFF else 16

    def close():
        nonlocal r13
        frames.append(regs + [r13])
        r13 = None

    while i < len(data):
        b = data[i]
        i += 1
        if b == 0xFF:
            if open_:
                close()
            open_ = True
        elif b == 0xFE:
            if i >= len(data):
                break
            n = 4 * data[i]
            i += 1
            if open_:
                close()
            for _ in range(n - 1):
                close()
            open_ = n > 0                # the last of them takes the writes that follow
        elif b == 0xFD:
            break
        elif b < 16:
            if i >= len(data):
                break
            v = data[i]
            i += 1
            if b < 13:
                regs[b] = v
            elif b == 13:
                r13 = v & 0x0F
            # 14, 15: the AY's I/O ports, nothing to do with sound
        else:
            break                        # not PSG data any more
    if open_:
        close()
    return frames


class AyFile:
    """A ZXAYEMUL (.ay) file: Spectrum music with its own player code.
    Big-endian words; pointers are relative to where they're stored."""

    def __init__(self, data):
        if data[:8] != b'ZXAYEMUL':
            raise ValueError('not a ZXAYEMUL .ay file')
        self.d = data
        self.author = self.text(self.ptr(12))
        self.misc = self.text(self.ptr(14))
        self.count, self.first = data[16] + 1, data[17]
        self.songs = self.ptr(18)

    def word(self, o):
        return self.d[o] << 8 | self.d[o + 1]

    def ptr(self, o):
        v = self.word(o)
        return o + (v - 65536 if v >= 32768 else v)

    def text(self, o):
        try:
            return self.d[o:self.d.index(0, o)].decode('latin-1')
        except (ValueError, IndexError):
            return ''

    def song(self, n):
        if not 0 <= n < self.count:
            raise ValueError(f'song {n + 1}: the file has {self.count}')
        e = self.songs + 4 * n
        sd = self.ptr(e + 2)
        pts, adr = self.ptr(sd + 10), self.ptr(sd + 12)
        blocks = []
        while self.word(adr):
            blocks.append((self.word(adr), self.word(adr + 2), self.ptr(adr + 4)))
            adr += 6
        return dict(name=self.text(self.ptr(e)), length=self.word(sd + 4), fade=self.word(sd + 6),
                    hi=self.d[sd + 8], lo=self.d[sd + 9], stack=self.word(pts),
                    init=self.word(pts + 2), interrupt=self.word(pts + 4), blocks=blocks)


class Spectrum:
    """Just enough of a ZX Spectrum to run a music player: a Z80 with 64K
    of RAM, and the 128's AY ports ($FFFD select, $BFFD data) caught, so
    each register write lands in REGS. Needs the z80 package.

    Amstrad CPC players (the .ay files Project AY ripped from the CPC) are
    caught too: the CPC reaches its AY through the 8255 PPI, data on port
    $F4xx, then a control write to $F6xx (bits 7-6: 11 = select register,
    10 = write, 01 = read, 00 = inactive). The first write to $F4xx or
    $F6xx switches to CPC ports for good (as ZXTune does), and sets CPC,
    since its AY runs at 1 MHz."""

    TRAP = 0x00F0                                         # a call returns here (HALT)

    def __init__(self, mem):
        try:
            import z80
        except ImportError:
            raise ValueError('this format needs the z80 package: .venv/bin/pip install z80')
        mem = bytearray(mem)
        mem[self.TRAP] = 0x76
        self.m = z80.Z80Machine()
        self.m.set_memory_block(0, bytes(mem))
        self.regs, self.sel, self.r13 = [0] * 16, 0, None
        self.cpc, self.latch = False, 0
        self.ay_writes = self.beeper = 0                  # AY register writes, ULA (beeper) writes
        self.m.set_output_callback(self._out)
        self.m.set_input_callback(self._in)

    def _write(self, val):
        self.ay_writes += 1
        self.regs[self.sel] = val
        if self.sel == 13:
            self.r13 = val & 0x0F

    def _out(self, port, val):
        hi = port >> 8
        if hi in (0xF4, 0xF6) and port & 0xFF != 0xFE:       # (OUT ($FE),A has A there: the beeper)
            self.cpc = True
        elif not self.cpc and not port & 1:
            self.beeper += 1                              # the Spectrum's ULA: bit 4 is the beeper
        if self.cpc:
            if hi == 0xF4:
                self.latch = val
            elif hi == 0xF6:
                if val & 0xC0 == 0xC0:
                    self.sel = self.latch & 0x0F
                elif val & 0xC0 == 0x80:
                    self._write(self.latch)
        elif port & 0xC002 == 0xC000:
            self.sel = val & 0x0F
        elif port & 0xC002 == 0x8000:
            self._write(val)

    def _in(self, port):
        if self.cpc:
            return self.regs[self.sel] if port >> 8 == 0xF4 else 0xFF
        return self.regs[self.sel] if port & 0xC002 == 0xC000 else 0xFF

    def call(self, addr, frames=10, irq=False, log=None):
        """Run the routine at ADDR until it returns, a frame at a time, at
        most FRAMES of them. With IRQ the routine may wait for the 50 Hz
        interrupt (HALT): each time it does, the frame that has passed is
        appended to LOG, and the interrupt is delivered. A call that never
        waits adds one frame."""
        m = self.m
        sp = (m.sp - 2) & 0xFFFF
        m.set_memory_block(sp, bytes([self.TRAP & 0xFF, self.TRAP >> 8]))
        m.sp, m.pc, m.halted = sp, addr, False
        if irq:
            m.iff1 = m.iff2 = True
        waited = 0
        for _ in range(frames):
            if irq and m.halted and m.pc not in (self.TRAP, self.TRAP + 1):
                log.append(self.frame())
                waited += 1
                m.on_handle_active_int()
            m.ticks_to_stop = 69888                       # one Spectrum frame
            m.run()
            if m.pc in (self.TRAP, self.TRAP + 1):
                if irq and not waited:
                    log.append(self.frame())
                return
        raise ValueError(f'the player at {addr:04X} did not return')

    def run_free(self, addr, frames, log):
        """Run the routine at ADDR as the tune's main loop: it doesn't return
        (EI, HALT, ...) and what plays is the interrupt handler it installed.
        Deliver the 50 Hz interrupt every frame and append the registers to
        LOG after each, until LOG has FRAMES. True if the routine returned."""
        m = self.m
        sp = (m.sp - 2) & 0xFFFF
        m.set_memory_block(sp, bytes([self.TRAP & 0xFF, self.TRAP >> 8]))
        m.sp, m.pc, m.halted = sp, addr, False
        while len(log) < frames:
            if m.iff1:
                m.on_handle_active_int()
            m.ticks_to_stop = 69888
            m.run()
            log.append(self.frame())
            if m.pc in (self.TRAP, self.TRAP + 1):
                return True
        return False

    def frame(self):
        """The registers as they are now, R13 only if it was written."""
        f = self.regs[:13] + [self.r13]
        self.r13 = None
        return f


class Unsupported(ValueError):
    """A tune that can never play on the AY (sampled sound, no AY chip, a
    beeper player): the tune library sets such files aside."""


class NeedsFreeRun(ValueError):
    """An INIT that never returns because it is the tune's main loop: the
    interrupt handler it sets up does the playing."""


class NeedsInterrupts(ValueError):
    """A play routine that never returns on its own: it waits for the
    frame interrupt (HALT)."""


def load_ay(data, n=None, default_len=180 * 50):
    """Run an .ay file's player in a Z80 emulator, one call per 50 Hz frame,
    and catch its AY writes. Memory and registers are set up as the
    ZXAYEMUL format describes. A song with no length is cut 2 s after its
    last sound. A play routine that waits for the frame interrupt itself
    (it starts with HALT) is run again with the interrupt delivered, and
    may take several frames a call. Returns (frames, song name, author,
    AY clock): the clock is 1 MHz for an Amstrad CPC player, None (the
    Spectrum's) otherwise."""
    ay = AyFile(data)
    order = [ay.first] + [i for i in range(ay.count) if i != ay.first] if n is None else [n]
    for k, i in enumerate(order):
        song = ay.song(i)
        try:
            try:
                frames, spec = run_ay(data, song, default_len)
            except NeedsInterrupts:
                frames, spec = run_ay(data, song, default_len, irq=True)
            except NeedsFreeRun:
                frames, spec = run_ay(data, song, default_len, free=True)
        except ValueError as e:
            if n is None and str(e).startswith('a beeper tune') and k + 1 < len(order):
                continue                                  # no song given: the first that has an AY part
            raise
        return frames, song['name'], ay.author, (CPC_AY_CLOCK if spec.cpc else None)


def is_beeper(spec):
    """A player that drives the beeper and (almost) never writes to the AY: a
    few writes are just it detecting a 128K. A tune with a border-colour main
    loop and an interrupt handler playing the AY writes it every frame."""
    return spec.beeper >= 100 and spec.ay_writes <= 40


SAMPLE_WRITES = 1500              # AY writes in INIT and the first frames: a tune makes a few hundred, sampled sound tens of thousands


def unplayable_song(data, ay, n):
    """Why song N of an .ay file can't be played on the AY, in a word, or
    None. 'beeper': its INIT is the whole tune, a loop on the beeper that
    never returns, and nothing reaches the AY. 'sample': digitised sound,
    played by writing a volume register thousands of times, which a 50 Hz
    stream can't carry (it only sees the last value of each frame)."""
    song = ay.song(n)
    spec, init = setup_ay(data, song)
    frames = 8
    try:
        spec.call(init, 60)
    except ValueError:                                    # never returned: a beeper or sample loop, or a main loop
        spec, init = setup_ay(data, song)
        frames = 100
        spec.run_free(init, frames, [])                   # with the 50 Hz interrupt
    else:
        try:
            for _ in range(8):
                if song['interrupt']:
                    spec.call(song['interrupt'])
                else:
                    v = (spec.m.i << 8) | 0xFF
                    spec.call(spec.m.memory[v] | spec.m.memory[(v + 1) & 0xFFFF] << 8)
        except ValueError:
            pass                                          # judge by what was written so far
    if is_beeper(spec):
        return 'beeper'
    return 'sample' if spec.ay_writes > SAMPLE_WRITES + 60 * frames else None


def setup_ay(data, song):
    mem = bytearray([0xC9] * 0x100 + [0xFF] * 0x3F00 + [0] * 0xC000)
    mem[0x38] = 0xFB                                      # EI (then RET)
    for addr, length, off in song['blocks']:
        chunk = data[off:off + min(length, 0x10000 - addr)]
        mem[addr:addr + len(chunk)] = chunk
    init = song['init'] or song['blocks'][0][0]
    spec = Spectrum(mem)
    m = spec.m
    hilo = song['hi'] << 8 | song['lo']
    for r in ('af', 'bc', 'de', 'hl', 'ix', 'iy'):
        setattr(m, r, hilo)
    m.sp = song['stack']
    return spec, init


def run_ay(data, song, default_len, irq=False, free=False):
    spec, init = setup_ay(data, song)
    m = spec.m
    total = (song['length'] or default_len) + song['fade']
    frames = []
    if free:                                              # INIT is the main loop: the handler plays
        spec.run_free(init, total, frames)
        if is_beeper(spec):
            raise Unsupported("a beeper tune: its player drives the Spectrum's beeper "
                              "and never writes to the AY")
    else:
        try:
            spec.call(init, 250)
        except ValueError:
            if is_beeper(spec):
                raise Unsupported("a beeper tune: its player drives the Spectrum's beeper "
                                  "and never writes to the AY") from None
            raise NeedsFreeRun from None
    while len(frames) < total:
        if song['interrupt']:
            try:
                spec.call(song['interrupt'], 50 if irq else 10, irq, frames)
            except ValueError:
                if irq:
                    raise
                raise NeedsInterrupts from None
            if not irq:
                frames.append(spec.frame())
        else:                                             # IM 2: the handler INIT set up
            v = (m.i << 8) | 0xFF
            spec.call(m.memory[v] | m.memory[(v + 1) & 0xFFFF] << 8)
            frames.append(spec.frame())
    del frames[total:]
    for k, f in enumerate(frames):
        left = total - k
        if left <= song['fade']:                          # fade out over the last FADE frames
            for v in (8, 9, 10):
                if not f[v] & 0x10:                       # fixed volume, not the envelope
                    f[v] = (f[v] & 0x0F) * left // (song['fade'] + 1)
    if not song['length']:
        sound = [i for i, f in enumerate(frames) if any(f[v] & 0x1F for v in (8, 9, 10))]
        frames = frames[:sound[-1] + 100] if sound else frames
    return frames, spec


CPC_AY_CLOCK = 1_000_000          # Amstrad CPC: the AY runs at 1 MHz


PT3_PLAYER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'third_party', 'vt2-pt3-player', 'pt3play-c000.bin')


def pt3_modules(data):
    """A TurboSound PT3 (AlCo's format: two modules for two AYs, then a
    16-byte footer 'PT3!' size1 'PT3!' size2 '02TS') -> both modules;
    a plain PT3 -> just itself."""
    if len(data) > 16 and data[-4:] == b'02TS' and data[-16:-12] == b'PT3!':
        n1 = int.from_bytes(data[-12:-10], 'little')
        n2 = int.from_bytes(data[-6:-4], 'little')
        return [data[:n1], data[n1:n1 + n2]]
    return [data]


def load_pt3(data, max_frames=15 * 60 * 50):
    """Pro Tracker 3 / Vortex Tracker II module: played by S.V. Bulba's
    universal PT3 player (Vortex Tracker II v1.0 player r.7, assembled at
    $C000, see third_party/vt2-pt3-player) in the Z80 emulator, once per
    frame, until the module reaches its end. Returns (frames, title)."""
    if len(data) > 0x8000:
        raise ValueError('PT3 module bigger than 32K')
    player = open(PT3_PLAYER, 'rb').read()
    mem = bytearray(0x10000)
    mem[0xC000:0xC000 + len(player)] = player
    mem[0x4000:0x4000 + len(data)] = data                 # the module, below the player
    mem[0xC00A] = 1                                       # SETUP bit 0: don't loop
    spec = Spectrum(mem)
    spec.m.sp = 0x3FF0
    spec.m.hl = 0x4000
    spec.call(0xC003, 50)                                 # INIT, module address in HL
    frames = []
    while len(frames) < max_frames:
        spec.call(0xC005)                                 # PLAY one frame
        if spec.m.memory[0xC00A] & 0x80:                  # SETUP bit 7: the end was reached
            break
        frames.append(spec.frame())
    title = data[0x1E:0x3E].decode('latin-1').strip()
    author = data[0x42:0x62].decode('latin-1').strip() if data[0x3E:0x42] == b' by ' else ''
    return frames, ' - '.join(x for x in (title, author) if x)


# ---- LHA (.lzh): YM files usually come compressed with it

def lha_unpack(data):
    """The first file in an LHA archive (-lh0-, -lh4- to -lh7-, header
    levels 0-2), decompressed. Pure Python, after LHa for UNIX's decoder."""
    level = data[20]
    method = data[2:7]
    if level == 2:
        start = data[0] | data[1] << 8
        packed = int.from_bytes(data[7:11], 'little')
    elif level in (0, 1):
        start = 2 + data[0]
        packed = int.from_bytes(data[7:11], 'little')
        if level == 1:                       # extended headers follow, counted in packed
            nxt = data[start - 2] | data[start - 1] << 8
            base = start
            while nxt:
                nn = data[start + nxt - 2] | data[start + nxt - 1] << 8
                start += nxt
                nxt = nn
            packed -= start - base
    else:
        raise ValueError(f'LHA header level {level} not supported')
    size = int.from_bytes(data[11:15], 'little')
    body = data[start:start + packed]
    if method == b'-lh0-':
        return body[:size]
    dicbit = {b'-lh4-': 12, b'-lh5-': 13, b'-lh6-': 15, b'-lh7-': 16}.get(method)
    if dicbit is None:
        raise ValueError(f'LHA method {method.decode(errors="replace")} not supported')
    return _lzh_decode(body, size, dicbit)


class _BitReader:
    def __init__(self, data):
        self.data, self.pos, self.buf, self.n = data, 0, 0, 0

    def peek(self, k):
        while self.n < k:
            self.buf = (self.buf << 8) | (self.data[self.pos] if self.pos < len(self.data) else 0)
            self.pos += 1
            self.n += 8
        return (self.buf >> (self.n - k)) & ((1 << k) - 1)

    def skip(self, k):
        self.peek(k)
        self.n -= k
        self.buf &= (1 << self.n) - 1

    def get(self, k):
        v = self.peek(k)
        self.skip(k)
        return v


def _huffman(lengths):
    """Canonical code (shorter codes first, then symbol order, as LHA's
    make_table) -> (lookup table indexed by the next maxlen bits, maxlen).
    A table with one symbol and no lengths is (symbol, 0)."""
    maxlen = max(lengths)
    table = [None] * (1 << maxlen)
    code = 0
    for length in range(1, maxlen + 1):
        for sym, l in enumerate(lengths):
            if l == length:
                first = code << (maxlen - length)
                for i in range(first, first + (1 << (maxlen - length))):
                    table[i] = (sym, length)
                code += 1
        code <<= 1
    return table, maxlen


def _decode(bits, tab):
    if isinstance(tab, int):                 # a single symbol, no bits
        return tab
    table, maxlen = tab
    sym, length = table[bits.peek(maxlen)]
    bits.skip(length)
    return sym


def _lzh_decode(body, size, dicbit):
    if size > 64 << 20:
        raise ValueError('LHA: the original size is absurd (%d bytes)' % size)
    NC, NT, TBIT, CBIT = 510, 19, 5, 9
    np_, pbit = (14, 4) if dicbit <= 13 else (17 if dicbit == 16 else 16, 5)
    bits = _BitReader(body)

    def read_pt_len(nn, nbit, special):
        n = bits.get(nbit)
        if n == 0:
            return bits.get(nbit)
        lens, i = [0] * nn, 0
        while i < n:
            c = bits.get(3)
            if c == 7:
                while bits.get(1):
                    c += 1
            lens[i] = c
            i += 1
            if i == special:
                for _ in range(bits.get(2)):
                    lens[i] = 0
                    i += 1
        return _huffman(lens)

    def read_c_len(pt):
        n = bits.get(CBIT)
        if n == 0:
            return bits.get(CBIT)
        lens, i = [0] * NC, 0
        while i < n:
            c = _decode(bits, pt)
            if c <= 2:
                run = 1 if c == 0 else bits.get(4) + 3 if c == 1 else bits.get(CBIT) + 20
                i += run
            else:
                lens[i] = c - 2
                i += 1
        return _huffman(lens)

    out = bytearray()
    left = 0
    while len(out) < size:
        if left == 0:
            left = bits.get(16)
            pt = read_pt_len(NT, TBIT, 3)
            ct = read_c_len(pt)
            pt = read_pt_len(np_, pbit, -1)
        left -= 1
        c = _decode(bits, ct)
        if c < 256:
            out.append(c)
        else:
            length = c - 253
            p = _decode(bits, pt)
            if p:
                p = (1 << (p - 1)) + bits.get(p - 1)
            src = len(out) - p - 1
            for k in range(length):
                out.append(out[src + k])
    return bytes(out[:size])


# ---- YM (Atari ST register dumps) and VGM

YM_MASK = [0xFF, 0x0F, 0xFF, 0x0F, 0xFF, 0x0F, 0x1F, 0x3F, 0x1F, 0x1F, 0x1F, 0xFF, 0xFF, 0x0F]


def to_50hz(frames, rate):
    """Frames at `rate` Hz -> 50 Hz. An envelope-shape write (R13) in any
    frame that is skipped over is kept."""
    if rate == 50:
        return frames
    out, last = [], -1
    for k in range(int(len(frames) * 50 / rate)):
        j = min(len(frames) - 1, int(k * rate / 50))
        f = list(frames[j])
        for i in range(last + 1, j):
            if frames[i][13] is not None and f[13] is None:
                f[13] = frames[i][13]
        out.append(f)
        last = j
    return out


def load_ym(data):
    """YM2!, YM3!, YM3b, YM5!, YM6! (Leonard's ST-Sound formats), LHA
    compressed or not. Returns (frames, title, clock). Digidrums and the
    YM5/6 special effects (SID voice, sync buzzer) are left out."""
    if data[2:5] == b'-lh' and data[6:7] == b'-':
        data = lha_unpack(data)
    tag = data[:4]
    if tag in (b'YM2!', b'YM3!', b'YM3b'):
        body = data[4:-4] if tag == b'YM3b' else data[4:]
        n = len(body) // 14
        regs = [[body[r * n + i] for r in range(14)] for i in range(n)]       # interleaved
        title, clock, rate = '', 2_000_000, 50
    elif tag in (b'YM5!', b'YM6!'):
        if data[4:12] != b'LeOnArD!':
            raise ValueError('YM5/YM6 file without the LeOnArD! check string')
        n = int.from_bytes(data[12:16], 'big')
        attrs = int.from_bytes(data[16:20], 'big')
        drums = int.from_bytes(data[20:22], 'big')
        clock = int.from_bytes(data[22:26], 'big')
        rate = int.from_bytes(data[26:28], 'big') or 50
        p = 34 + int.from_bytes(data[32:34], 'big')
        for _ in range(drums):
            p += 4 + int.from_bytes(data[p:p + 4], 'big')
        texts = []
        for _ in range(3):                                   # name, author, comment
            e = data.index(0, p)
            texts.append(data[p:e].decode('latin-1'))
            p = e + 1
        body = data[p:p + 16 * n]
        if len(body) < 16 * n:
            raise ValueError('the YM file is shorter than its frame count says')
        if attrs & 1:
            regs = [[body[r * n + i] for r in range(16)] for i in range(n)]
        else:
            regs = [list(body[i * 16:i * 16 + 16]) for i in range(n)]
        title = ' - '.join(t for t in texts[:2] if t)
    elif tag in (b'MIX1', b'YMT1', b'YMT2'):
        raise Unsupported(f'{tag.decode()} is sampled sound (digital audio), not AY register music')
    else:
        raise ValueError(f'not a YM file I can read ({tag!r})')
    frames = []
    for r in regs:
        f = [r[i] & YM_MASK[i] for i in range(13)]
        f.append(None if r[13] == 0xFF else r[13] & 0x0F)
        frames.append(f)
    return to_50hz(frames, rate), title, clock


VGM_LEN = {**{c: 2 for c in range(0x30, 0x40)}, 0x4F: 2, 0x50: 2,
           **{c: 3 for c in range(0x40, 0x4F)}, **{c: 3 for c in range(0x51, 0x60)},
           0x61: 3, 0x62: 1, 0x63: 1, 0x68: 12,
           **{c: 1 for c in range(0x70, 0x90)},
           0x90: 5, 0x91: 5, 0x92: 6, 0x93: 11, 0x94: 2, 0x95: 5,
           **{c: 3 for c in range(0xA0, 0xC0)}, **{c: 4 for c in range(0xC0, 0xE0)},
           **{c: 5 for c in range(0xE0, 0x100)}}


DIGI_WRITES = 6          # more writes than this to one volume register in a frame = sampled sound

# VGM header: clock field offset -> chip (VGM 1.71 spec)
VGM_CHIPS = {0x0C: 'SN76489', 0x10: 'YM2413', 0x2C: 'YM2612', 0x30: 'YM2151', 0x38: 'SegaPCM',
             0x40: 'RF5C68', 0x44: 'YM2203', 0x48: 'YM2608', 0x4C: 'YM2610', 0x50: 'YM3812',
             0x54: 'YM3526', 0x58: 'Y8950', 0x5C: 'YMF262', 0x60: 'YMF278B', 0x64: 'YMF271',
             0x68: 'YMZ280B', 0x6C: 'RF5C164', 0x70: 'PWM', 0x74: 'AY-3-8910', 0x80: 'Game Boy',
             0x84: 'NES APU', 0x88: 'MultiPCM', 0x8C: 'uPD7759', 0x90: 'OKIM6258', 0x98: 'OKIM6295',
             0x9C: 'Konami SCC', 0xA0: 'K054539', 0xA4: 'HuC6280', 0xA8: 'C140', 0xAC: 'K053260',
             0xB0: 'Pokey', 0xB4: 'QSound', 0xB8: 'SCSP', 0xC0: 'WonderSwan', 0xC4: 'VSU',
             0xC8: 'SAA1099', 0xCC: 'ES5503', 0xD0: 'ES5506', 0xD8: 'X1-010', 0xDC: 'C352', 0xE0: 'GA20'}
AY_TYPES = {0x00: 'AY-3-8910', 0x01: 'AY-3-8912', 0x02: 'AY-3-8913', 0x03: 'AY8930',
            0x10: 'YM2149', 0x11: 'YM3439', 0x12: 'YMZ284', 0x13: 'YMZ294'}


def scc_level(v):
    """SCC volume (linear, 0-15) -> AY volume (about 3 dB a step)."""
    return 0 if v <= 0 else max(1, min(15, round(15 + 2 * math.log2(v / 15))))


def load_vgm(data, scc=True, drums=True):
    """VGM/VGZ with an AY-3-8910 family chip (command A0). The first chip
    only; 44100 Hz samples are cut into 50 Hz frames. Returns
    (frames, title, clock).

    MSX tunes often have a Konami SCC (K051649, command D2) as well, with
    much of the music. With scc=True, fold_scc() puts some of its voices on
    AY channels the tune leaves silent; scc=[3, 1] picks the voices."""
    import gzip
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    if data[:4] != b'Vgm ':
        raise ValueError('not a VGM file')
    version = int.from_bytes(data[8:12], 'little')
    start = 0x34 + int.from_bytes(data[0x34:0x38], 'little') if version >= 0x150 else 0x40
    if start == 0x34:
        start = 0x40

    def field(o, size):                                      # header fields past the data are 0
        return int.from_bytes(data[o:o + size], 'little') if o + size <= start else 0

    chips = []
    for off, name in VGM_CHIPS.items():
        if field(off, 4) & 0x3FFFFFFF:
            if off == 0x74:
                name = AY_TYPES.get(field(0x78, 1), name)
            chips.append(name + (' x2' if field(off, 4) & 0x40000000 else ''))
    written = ' + '.join(chips) or 'no chip named in the header'
    clock = field(0x74, 4) & 0x3FFFFFFF if version >= 0x151 else 0
    if not clock:
        raise Unsupported(f'written for {written}: no AY-3-8910 family chip, nothing to play')
    if field(0x79, 1) & 0x10:                                # YM2149 with its clock halved
        clock //= 2
    scc_clock = field(0x9C, 4) & 0x3FFFFFFF if version >= 0x161 else 0
    sp, sv, sk = [0] * 5, [0] * 5, [0]                       # SCC periods, volumes, keys
    scc_writes = 0
    scc_frames = []                                          # SCC state at each frame
    regs, r13, frames, t, p = [0] * 13, None, [], 0, start
    writes = 0
    volw = [0, 0, 0]                                         # volume writes per channel this frame
    volv = [[], [], []]                                      # ... and their values
    digi = set()
    digi_frames = {}                                         # (frame, channel) -> sample values

    def advance(samples):
        nonlocal t, r13
        t += samples
        while t >= 882 * (len(frames) + 1):                  # 44100 / 50
            f = regs + [r13]
            for ch in range(3):
                if volw[ch] > DIGI_WRITES:                   # sampled sound: mute rather than click
                    f[8 + ch] = 0
                    digi.add(ch)
                    digi_frames[len(frames), ch] = volv[ch]
            volw[:] = [0, 0, 0]
            volv[:] = [[], [], []]
            frames.append(f)
            scc_frames.append((sp[:], sv[:], sk[0]))
            r13 = None

    while p < len(data):
        c = data[p]
        if c == 0x66:
            break
        if c == 0xA0:
            reg, val = data[p + 1], data[p + 2]
            if not reg & 0x80:                               # bit 7: second chip
                if reg < 13:
                    regs[reg] = val
                    if 8 <= reg <= 10:
                        volw[reg - 8] += 1
                        volv[reg - 8].append(val)
                elif reg == 13:
                    r13 = val & 0x0F
                writes += 1
        elif c == 0xD2 and not data[p + 1] & 0x80:            # SCC: port, register, value
            port, reg, val = data[p + 1], data[p + 2], data[p + 3]
            if port == 1 and reg < 10:
                ch = reg >> 1
                sp[ch] = (sp[ch] & 0xFF) | (val & 0x0F) << 8 if reg & 1 else (sp[ch] & 0xF00) | val
            elif port == 2 and reg < 5:
                sv[reg] = val & 0x0F
            elif port == 3:
                sk[0] = val & 0x1F
            scc_writes += 1
        elif c == 0x61:
            advance(data[p + 1] | data[p + 2] << 8)
        elif c == 0x62:
            advance(735)
        elif c == 0x63:
            advance(882)
        elif 0x70 <= c <= 0x7F:
            advance((c & 0x0F) + 1)
        elif 0x80 <= c <= 0x8F:
            advance(c & 0x0F)
        elif c == 0x67:                                      # data block
            p += 7 + (int.from_bytes(data[p + 3:p + 7], 'little') & 0x7FFFFFFF)
            continue
        p += VGM_LEN.get(c, 1)
    if not writes:
        raise Unsupported('no AY-3-8910 writes in this VGM')
    title = ''
    gd3 = int.from_bytes(data[0x14:0x18], 'little')
    if gd3 and data[0x14 + gd3:0x18 + gd3] == b'Gd3 ':
        fields = data[0x14 + gd3 + 12:].decode('utf-16-le', errors='replace').split('\0')
        title = ' - '.join(x for x in (fields[0], fields[6] if len(fields) > 6 else '') if x)
    played = 'the AY part'
    if scc and scc_clock and scc_writes and frames:
        mapping = fold_scc(frames, scc_frames, clock / scc_clock, scc if isinstance(scc, list) else None)
        if mapping:
            played += ' + ' + ', '.join(f'SCC voice {c + 1} on AY {"ABC"[a]}' for c, a in mapping)
    if len(chips) > 1:
        title += f' [written for {written}; playing {played}]'
    if digi:
        chans = "/".join("ABC"[c] for c in sorted(digi))
        if drums:
            style = drums if drums in DRUM_STYLES else 'held'
            kicks, snares = fake_drums(frames, digi_frames, clock, style)
            title += (f' [sampled drums on {chans} played as AY drums ({style}): '
                      f'{kicks} kicks, {snares} snares]')
        else:
            title += f' [sampled drums on {chans} muted]'
    return frames, title, clock


DRUM_STYLES = ('held', 'env', 'snares', 'kicks')     # held is the default; snares/kicks: held, the other muted


def drum_kinds(runs):
    """How to tell a channel's snares from its kicks. A tune uses only a
    few samples, and each one plays at its own pace: a noisy snare changes
    the volume register far more often than a kick's slow thump (Wings of
    Death: about 20-25 writes a frame against 55-70 in Stage 1, 45-55
    against 65-75 in Stage 2). So split the hits at the biggest gap in
    writes per frame; with no clear gap (one sample), fall back on how
    jumpy the values are. Returns is_snare(run, vals)."""
    rate = lambda run, vals: len(vals) / len(run)
    rates = sorted(rate(r, v) for r, v in runs)
    if len(rates) > 1:
        gap, i = max((b - a, i) for i, (a, b) in enumerate(zip(rates, rates[1:])))
        if gap >= 0.25 * rates[i]:
            cut = (rates[i] + rates[i + 1]) / 2
            return lambda run, vals: rate(run, vals) > cut
    return lambda run, vals: sum(abs(a - b) for a, b in zip(vals, vals[1:])) / max(1, len(vals) - 1) > 4.5


def fake_drums(frames, digi_frames, clock, style='held'):
    """Sampled drums can't be streamed (they hammer a volume register
    thousands of times a second), so play an AY-style drum instead, on the
    same channel and frames as each sample. A hit is a run of sampled
    frames; drum_kinds() tells snares from kicks.

    style='env': kick = a tone dropping from 160 to 45 Hz in three frames, then holding,
    with a click of noise at the start. Snare: noise (brighter at the
    start) over a short tone. Both decay on the AY's hardware envelope
    (shape 0, restarted at each hit, sized to the hit), which is smoother
    and fuller than stepping the volume once a frame; if another channel
    uses the envelope at the time, they step the volume instead.

    style='held' (the default) copies the drums in Jedi's Virtual Escape (YM): the
    volume held high for four frames (three steps below his), then a quick
    fade, no envelope. Kick: 207, 155, 116, 87 Hz (a fourth down per frame), noise
    from the second frame. Snare: a frame of noise, then 138, 123, 110 Hz
    with noise again on the fourth frame. Nothing below 87 Hz, where small
    speakers stop. style='snares' or 'kicks' plays only those (held), to
    tell which is which by ear.
    Returns (kicks, snares)."""
    def period(hz):
        return min(4095, round(clock / (16 * hz)))

    def noise_period(hz):
        return max(1, min(31, round(clock / (16 * hz))))

    kicks = snares = 0
    for ch in range(3):
        runs, k = [], 0
        while k < len(frames):
            if (k, ch) not in digi_frames:
                k += 1
                continue
            run = []
            while (k, ch) in digi_frames:
                run.append(k)
                k += 1
            runs.append((run, [v for f in run for v in digi_frames[f, ch]]))
        is_snare = drum_kinds(runs)
        for run, vals in runs:
            jumpy = is_snare(run, vals)
            n = len(run)
            others = [c for c in range(3) if c != ch]
            use_env = all(not frames[fi][8 + c] & 0x10 for fi in run for c in others)
            # envelope: one decay over the hit (0.8 of it for a snare); cycle = 256 * EP / clock
            ep = min(65535, round((0.8 if jumpy else 1.0) * n * 0.02 * clock / 256))
            for i, fi in enumerate(run):
                f = frames[fi]
                noise_free = all(f[7] >> (3 + c) & 1 for c in others if f[8 + c] & 0x1F)
                if style in ('snares', 'kicks') and jumpy != (style == 'snares'):
                    f[7] |= 9 << ch                          # the other drum: silent
                    f[8 + ch] = 0
                    continue
                if style != 'env':
                    fade = max(0, i - 3)                     # frames past the first four
                    if jumpy:                                # snare
                        hz = (138, 138, 123, 110)[min(i, 3)]
                        noise_on = noise_free and (i == 0 or i >= 3)
                        tone_on = i > 0 or not noise_on
                        vol = (11, 12, 11, 10)[min(i, 3)] - 3 * fade     # 3 steps below Jedi's (by ear, WOD1)
                        if noise_on:
                            f[6] = noise_period(7353 if i == 0 else 6579)
                    else:                                    # kick
                        hz = 207 * 0.75 ** min(i, 3)
                        noise_on = noise_free and 1 <= i <= 3
                        tone_on = True
                        vol = (12, 12, 11, 11)[min(i, 3)] - 3 * fade     # 3 steps below Jedi's (by ear, WOD1)
                        if noise_on:
                            f[6] = noise_period(10417)
                    p = period(hz)
                    f[2 * ch], f[2 * ch + 1] = p & 0xFF, p >> 8
                    f[7] |= 9 << ch
                    if tone_on:
                        f[7] &= ~(1 << ch)
                    if noise_on:
                        f[7] &= ~(8 << ch)
                    f[8 + ch] = max(0, vol)
                    continue
                if jumpy:                                    # snare
                    tone_on, noise_on, hz = i < 2, noise_free, 200
                    if noise_on:
                        f[6] = 4 if i == 0 else 7
                    step = round(15 * (1 - i / n) ** 1.5)
                else:                                        # kick
                    tone_on, noise_on = True, i == 0 and noise_free
                    if noise_on:
                        f[6] = 2
                    hz = 160 * (45 / 160) ** min(1, i / 2)
                    step = 15 if i < 2 else round(15 * (1 - (i - 1) / n))
                p = period(hz)
                f[2 * ch], f[2 * ch + 1] = p & 0xFF, p >> 8
                f[7] |= 9 << ch                              # tone and noise off, then on as needed
                if tone_on:
                    f[7] &= ~(1 << ch)
                if noise_on:
                    f[7] &= ~(8 << ch)
                if use_env:
                    f[8 + ch] = 0x10                         # this channel follows the envelope
                    f[11], f[12] = ep & 0xFF, ep >> 8
                    f[13] = 0 if i == 0 else None            # shape 0 (decay, then silence): restart
                else:
                    f[8 + ch] = step
            if jumpy:
                snares += 1
            else:
                kicks += 1
    return kicks, snares


def fold_scc(frames, scc_frames, ratio, pick=None, free_share=0.9, active_share=0.25):
    """Put Konami SCC voices on AY channels the tune leaves silent, as
    square waves. The choice is fixed for the whole tune, so a voice never
    hops between channels: AY channels silent in at least FREE_SHARE of
    the frames get the highest-pitched SCC voices that play in at least
    ACTIVE_SHARE of them (the top of the chords usually carries the tune),
    or the voices in PICK (numbered 1-5), in that order.
    An SCC period P sounds like AY period P + 1 at the same clock.
    Returns [(SCC voice, AY channel)]."""
    n = len(frames)
    free = [ch for ch in range(3) if sum(1 for f in frames if not f[8 + ch] & 0x1F) >= free_share * n]
    voices = []
    for c in range(5):
        on = [p[c] for p, v, k in scc_frames if k >> c & 1 and v[c] and p[c] > 8]
        if len(on) >= active_share * n:
            voices.append((sorted(on)[len(on) // 2], c))     # median period: smaller = higher
    order = [c - 1 for c in pick] if pick else [c for _, c in sorted(voices)]
    mapping = list(zip(order, free))
    for f, (p, v, k) in zip(frames, scc_frames):
        for c, ay in mapping:
            if f[8 + ay] & 0x1F or not (k >> c & 1 and v[c] and p[c] > 8):
                continue                                     # the AY needs it, or the voice is quiet
            period = min(4095, round((p[c] + 1) * ratio))
            f[2 * ay], f[2 * ay + 1] = period & 0xFF, period >> 8
            f[8 + ay] = scc_level(v[c])
            f[7] = (f[7] & ~(1 << ay)) | (8 << ay)           # tone on, noise off
    return mapping


def test_tune():
    """A 440 for 2 s, then a C major scale up and down over a bass line,
    with a noise hi-hat. Periods for a Spectrum AY (1.7734 MHz)."""
    def period(f):
        return round(SPECTRUM_AY / (16 * f))

    def note(n):                       # semitones from A 440
        return 440 * 2 ** (n / 12)

    frames = []
    a = period(440)
    for _ in range(100):               # tone A only, full volume
        frames.append([a & 0xFF, a >> 8, 0, 0, 0, 0, 0, 0b00111110, 15, 0, 0, 0, 0, None])
    scale = [-9, -7, -5, -4, -2, 0, 2, 3]                 # C D E F G A B C
    melody = scale + scale[::-1]
    bass = [-33, -33, -28, -28, -26, -26, -28, -28] * 2   # C C F F G G F F, two octaves down
    for k, (m, b) in enumerate(zip(melody, bass)):
        pa, pb = period(note(m)), period(note(b))
        for t in range(12):                               # 12 frames a note
            hat = t % 6 == 0
            mixer = 0b00111100 if not hat else 0b00011100   # A, B tone; C noise on hat frames
            frames.append([pa & 0xFF, pa >> 8, pb & 0xFF, pb >> 8, 0, 0, 3,
                           mixer, max(0, 15 - t), 10, 12 if hat else 0, 0, 0, None])
    frames.append([0] * 7 + [0b00111111, 0, 0, 0, 0, 0, None])
    return frames


def rescale(frames, k):
    """Periods for an AY clocked k times the tune's clock: same pitch."""
    def clamp(v, hi):
        return max(0, min(hi, v))
    out = []
    for f in frames:
        f = list(f)
        for lo in (0, 2, 4):                               # tone periods, 12 bits
            p = f[lo] | (f[lo + 1] & 0x0F) << 8
            if p:
                p = clamp(round(p * k), 4095) or 1
            f[lo], f[lo + 1] = p & 0xFF, p >> 8
        if f[6] & 0x1F:                                    # noise period, 5 bits
            f[6] = clamp(round((f[6] & 0x1F) * k), 31) or 1
        f[7] &= 0x3F                                       # I/O ports stay inputs (MSX tunes set bit 7)
        e = f[11] | f[12] << 8                             # envelope period, 16 bits
        if e:
            e = clamp(round(e * k), 65535) or 1
        f[11], f[12] = e & 0xFF, e >> 8
        out.append(f)
    return out


def wire(frame):
    """One frame on the wire: 14 values and a checksum."""
    body = bytes(frame[:13]) + bytes([0xFF if frame[13] is None else frame[13]])
    return body + bytes([-sum(body) & 0xFF])


END = bytes(13) + b'\xfe'
END = END + bytes([-sum(END) & 0xFF])


def to_psg(path, zxtune):
    """Any format zxtune123 knows -> PSG bytes, cached by file content."""
    data = open(path, 'rb').read()
    if data[:3] == b'PSG':
        return data
    exe = zxtune or shutil.which('zxtune123')
    if not exe:
        raise ValueError('not a PSG file, and zxtune123 is not installed to convert it')
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, hashlib.sha1(data).hexdigest() + '.psg')
    if not os.path.exists(out):
        r = subprocess.run([exe, '--convert', f'mode=psg,filename={out}', '--', path],
                           capture_output=True, text=True, timeout=120)
        if not os.path.exists(out):
            raise ValueError(f'zxtune123 could not convert it: {(r.stderr or r.stdout).strip()[-200:]}')
    return open(out, 'rb').read()


def load_tune(path, n, zxtune, scc=True, ts=None, drums=True):
    """Any tune file -> (frames, title, clock or None for the default)."""
    data = open(path, 'rb').read()
    if data[:8] == b'ZXAYEMUL':
        frames, name, author, clock = load_ay(data, n)
        title = ' - '.join(x for x in (name, author) if x)
        return frames, title + (' [Amstrad CPC]' if clock else ''), clock
    if data[:13] == b'ProTracker 3.' or data[:14] == b'Vortex Tracker':
        mods = pt3_modules(data)
        k = (ts or 1) - 1
        if not 0 <= k < len(mods):
            raise ValueError(f'ts={ts}: the file has {len(mods)} module(s)')
        frames, title = load_pt3(mods[k])
        if len(mods) > 1:
            title += f' [TurboSound, written for 2 AYs: playing module {k + 1} of 2, half the channels]'
        return frames, title, None
    if data[:4] == b'Vgm ' or data[:2] == b'\x1f\x8b':
        return load_vgm(data, scc, drums)
    if data[:2] == b'YM' or (data[2:5] == b'-lh' and data[6:7] == b'-'):
        return load_ym(data)
    return load_psg(to_psg(path, zxtune)), '', None


def frames_for(keyword, lib, args):
    """(frames, description) for a keyword, or (None, reason)."""
    clock = None
    try:
        lib.scan()                                          # new or changed files
    except OSError:
        pass
    found = None if keyword == 'TEST' else lib.resolve(keyword)
    if keyword == 'TEST':
        frames, what = test_tune(), 'built-in test tune'
    elif found:
        path, song, opts = found                            # opts: scc=off / scc=3,1, ts=2, drums=off
        scc, ts, drums = not args.no_scc, None, True
        for opt in opts.split():
            key, _, val = opt.partition('=')
            if key.lower() == 'scc':
                scc = False if val.lower() == 'off' else [int(x) for x in val.split(',') if x.isdigit()] or True
            elif key.lower() == 'ts' and val.isdigit():
                ts = int(val)
            elif key.lower() == 'drums':
                drums = False if val.lower() == 'off' else val.lower()   # sampled drums: AY drums (held/env), or muted
        try:
            frames, what, clock = load_tune(path, song, args.zxtune, scc, ts, drums)
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as e:
            return None, f'{path}: {e}'
        what = what or os.path.basename(path)
    else:
        return None, 'unknown tune'
    clock = clock or args.tune_clock
    if clock != args.tune_clock:
        what += f' (AY at {clock / 1e6:.4f} MHz)'
    return rescale(frames, args.ay_clock / clock), what


# ---- serial

def open_port(dev, baud):
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[0] = new[1] = new[3] = 0                              # raw in/out, no echo/canon
    new[2] = termios.CS8 | termios.CREAD | termios.CLOCAL     # 8N1, ignore modem lines
    new[4] = new[5] = BAUDS[baud]
    new[6][termios.VMIN] = 0
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd, old


def write_all(fd, data):
    while data:
        try:
            n = os.write(fd, data)
            data = data[n:]
        except BlockingIOError:
            select.select([], [fd], [], 1.0)


SERIAL_GLOBS = ('/dev/cu.usbserial*', '/dev/cu.usbmodem*', '/dev/cu.wchusbserial*', '/dev/cu.SLAB*',
                '/dev/ttyUSB*', '/dev/ttyACM*')


def serial_ports():
    """USB serial adapters that are plugged in now."""
    import glob
    return sorted(p for g in SERIAL_GLOBS for p in glob.glob(g))


REPLY_WINDOW = 128              # bytes of a reply ahead of MUSIC's credits (was 192: gentler on the ZXpand+)
REPLY_BURST = 8                 # bytes per write, 12 ms apart: about the rate of the tune stream, which never froze it
REPLY_CREDIT = 64               # MUSIC sends 'k' after every 64 it has read


def send_reply(fd, body):
    """A browser reply: 2-byte length, then the bytes, in REPLY_BURST-byte bursts,
    never more than REPLY_WINDOW ahead of MUSIC's credits: if MUSIC stops
    reading, the ZXpand must not fill up (256 unread bytes freeze it).
    Returns True if it all went, False if MUSIC stopped giving credits."""
    data = len(body).to_bytes(2, 'little') + body
    sent, credits = 0, 0
    active = time.monotonic()                             # when it last sent something or got a credit
    while sent < len(data):
        room = 2 + REPLY_WINDOW + credits * REPLY_CREDIT - sent
        if room > 0:
            n = min(REPLY_BURST, room, len(data) - sent)
            write_all(fd, data[sent:sent + n])
            sent += n
            active = time.monotonic()
            time.sleep(0.012)
            timeout = 0
        else:
            timeout = 2.0
        r, _, _ = select.select([fd], [], [], timeout)
        if r:
            got = os.read(fd, 64)
            if not got:
                raise OSError(5, 'serial port closed')
            if got.count(b'k'):
                credits += got.count(b'k')
                active = time.monotonic()
        elif timeout:
            return False
        if room <= 0 and time.monotonic() - active > 2.0:    # stray bytes don't count as credits
            return False
    return True


def read_keyword(fd):
    """Block until a keyword arrives; ends at CR/LF or an idle gap."""
    buf = b''
    while True:
        r, _, _ = select.select([fd], [], [], IDLE_GAP if buf else None)
        if not r:
            return buf
        data = os.read(fd, 64)
        if not data:
            raise OSError(5, 'serial port closed')
        for b in data:
            if b in (10, 13):
                if buf:
                    return buf
            else:
                buf += bytes([b])
        if len(buf) > MAX_KEYWORD:
            return buf


STATS = os.path.join(CACHE, 'streamed')                # total frames streamed, ever


def streamed_frames():
    try:
        return int(open(STATS).read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0


def add_streamed(n):
    """Add N frames to the running total; best effort."""
    try:
        os.makedirs(CACHE, exist_ok=True)
        total = streamed_frames() + n
        with open(STATS + '.tmp', 'w') as f:
            f.write(f'{total}\n')
        os.replace(STATS + '.tmp', STATS)
    except OSError:
        pass


def stream(fd, frames, window):
    """Send frames as the ZX81's credits allow, then the end marker.
    Returns (frames sent, how it ended)."""
    sent = credits = 0
    data = [wire(f) for f in frames] + [END]
    last_k = time.monotonic()
    while True:
        # top up to the window
        while sent < len(data) and sent - credits * CREDIT < window:
            write_all(fd, data[sent])
            sent += 1
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            got = os.read(fd, 64)
            if not got:                                       # unplugged: Linux hangs the port up
                raise OSError(5, 'serial port closed')
            for b in got:
                if b == ord('k'):
                    credits += 1
                    last_k = time.monotonic()
                elif b == ord('q'):
                    return sent, 'end' if sent == len(data) else 'stopped on the ZX81'
        if time.monotonic() - last_k > CREDIT_TIMEOUT:
            return sent, f'no credits for {CREDIT_TIMEOUT:.0f} s - ZX81 gone, or this computer slept? (see the header of zxmusic.py)'


HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, 'zxmusic.ini')


def use_venv():
    """.ay and .pt3 need the z80 package. If this Python hasn't got it but
    the repo's .venv has, run again with that one, so plain
    `python3 zxmusic.py` works too."""
    try:
        import z80  # noqa: F401
        return
    except ImportError:
        pass
    venv = os.path.join(HERE, '.venv')
    py = os.path.join(venv, 'bin', 'python')
    if os.path.exists(py) and os.path.realpath(sys.prefix) != os.path.realpath(venv):
        os.execv(py, [py, os.path.abspath(__file__)] + sys.argv[1:])
    print('Note: .ay and .pt3 tunes need the z80 package: python3 -m venv .venv && '
          '.venv/bin/pip install z80', file=sys.stderr)


def load_settings():
    import configparser
    c = configparser.ConfigParser()
    c.read(SETTINGS)
    return dict(c['zxmusic']) if 'zxmusic' in c else {}


def save_settings(values):
    import configparser
    c = configparser.ConfigParser()
    c['zxmusic'] = {k: str(v) for k, v in values.items() if v not in (None, '')}
    with open(SETTINGS, 'w') as f:
        f.write('# written by zxmusic.py --setup; edit freely or run --setup again\n')
        c.write(f)


def ask(question, default=''):
    a = input(f'{question}' + (f' [{default}]' if default else '') + ': ').strip()
    return a or default


def setup(cur):
    """Ask for the serial port, the tunes folder and (optionally) the AY
    clock; save them in zxmusic.ini next to this script."""
    print('zxmusic setup - Enter keeps what is in [brackets].\n')
    ports = serial_ports()
    if ports:
        print('USB serial adapters plugged in now:')
        for k, p in enumerate(ports, 1):
            print(f'  {k}  {p}')
    else:
        print('No USB serial adapter found (plug it in and run --setup again, or type its path).')
    dev = ask('Serial port (number or path)', cur.get('device') or (ports[0] if ports else ''))
    if dev.isdigit() and 1 <= int(dev) <= len(ports):
        dev = ports[int(dev) - 1]

    tunes = os.path.abspath(os.path.expanduser(ask('\nFolder with your tunes', cur.get('tunes') or os.path.join(HERE, 'tunes'))))
    if not os.path.isdir(tunes):
        if ask(f'{tunes} does not exist. Create it? (y/n)', 'y').lower().startswith('y'):
            os.makedirs(tunes)

    print(f"""
AY clock (optional, keeps tunes in tune): a recording of AYTONE's RUN 300 beeps
(see docs/music.md, Pitch), or the clock in Hz (nominal {ZXPAND_AY}). Enter skips.""")

    clock = cur.get('ay_clock', '')
    a = ask('Recording, clock in Hz, or Enter', clock)
    if a and a != clock:
        try:
            clock = str(round(float(a)))
        except ValueError:
            path = os.path.expanduser(a)
            if not os.path.isfile(path):
                print(f'{path}: no such file - keeping {clock or "the nominal clock"}')
            else:
                sys.path.insert(0, os.path.join(HERE, 'tools'))
                try:
                    import ayclock
                    ay = ayclock.main([path], hint=False)
                    clock = str(round(ay)) if ay else clock
                except SystemExit as e:                       # ayclock without numpy, or a bad file
                    print(e.code if isinstance(e.code, str) else 'ayclock could not measure it')
    values = {'device': dev, 'tunes': tunes, 'ay_clock': clock}
    for k in ('config', 'baud'):
        if cur.get(k):
            values[k] = cur[k]
    save_settings(values)
    print(f'\nSaved in {SETTINGS}:')
    for k, v in values.items():
        if v:
            print(f'  {k} = {v}')
    print('\nStart the server with: ./zxmusic.py')


def main():
    ap = argparse.ArgumentParser(
        description='Stream AY music to the ZX81 over serial. First time: --setup.',
        epilog='Settings from --setup live in zxmusic.ini; options given here win.')
    ap.add_argument('device', nargs='*', metavar='DEVICE',
                    help='serial port (default: from --setup, or the only USB serial adapter plugged in). '
                         'The old form, CONFIG DEVICE, still works')
    ap.add_argument('--setup', action='store_true',
                    help='ask for the serial port, tunes folder and (optionally) the AY clock, and save them')
    ap.add_argument('--tunes', help='folder with your tunes (default: tunes/ next to zxmusic.py)')
    ap.add_argument('--config', help='music.conf: keywords and options of your own (optional; '
                                     'default: music.conf next to zxmusic.py, if there is one)')
    ap.add_argument('--baud', type=int, choices=sorted(BAUDS), help='default 38400')
    ap.add_argument('--ay-clock', type=float,
                    help=f'your ZXpand+ AY clock in Hz, from tools/ayclock.py (default {ZXPAND_AY})')
    ap.add_argument('--tune-clock', type=float, default=SPECTRUM_AY,
                    help=f'the AY clock tunes were written for (default {SPECTRUM_AY}, ZX Spectrum)')
    ap.add_argument('--window', type=int, default=10,
                    help='most frames in flight (default 10 = 150 bytes; 16 at most: 256 unread bytes freeze the ZXpand+)')
    ap.add_argument('--zxtune', help='path to zxtune123, if it is not on PATH')
    ap.add_argument('--no-scc', action='store_true',
                    help="VGM: play only the AY part, not a Konami SCC voice folded onto a free AY channel")
    args = ap.parse_args()
    use_venv()
    cur = load_settings()
    if args.setup:
        try:
            setup(cur)
        except (KeyboardInterrupt, EOFError):
            print('\nSetup cancelled, nothing saved.')
        return
    if not 1 <= args.window <= 16:
        sys.exit('--window must be 1-16 (17 frames would leave 255 bytes unread)')

    device = None
    if len(args.device) == 2:                               # the old form: CONFIG DEVICE
        args.config, device = args.device
    elif len(args.device) == 1:
        device = args.device[0]
    elif len(args.device) > 2:
        ap.error('give at most a config file and a device')
    device = device or cur.get('device')
    if not device:
        ports = serial_ports()
        if len(ports) != 1:
            sys.exit(('No USB serial adapter found.' if not ports else
                      'Several serial adapters: ' + ', '.join(ports) + '.') +
                     ' Give the port, or run zxmusic.py --setup.')
        device = ports[0]
    tunes_dir = args.tunes or cur.get('tunes') or os.path.join(HERE, 'tunes')
    config = args.config or cur.get('config') or os.path.join(HERE, 'music.conf')
    if not os.path.exists(config):
        if args.config:
            sys.exit(f'{config}: no such file')
        config = None
    args.baud = args.baud or int(cur.get('baud', 38400))
    args.ay_clock = args.ay_clock or float(cur.get('ay_clock') or ZXPAND_AY)
    if not os.path.isdir(tunes_dir):
        sys.exit(f'{tunes_dir}: no such folder. Put your tunes there, or give --tunes, or run --setup.')

    lib = tunelib.Library(tunes_dir, config, args.zxtune, CACHE,
                          log=lambda m: print(f"{time.strftime('%H:%M:%S')} {m}"))
    keys = sorted(lib.keys)
    print(f'{len(lib.files)} tunes in {tunes_dir}' + (f', keywords of your own from {config}' if config else ''))
    print('Keywords: TEST, ' + ', '.join(keys[:40]) + (f' and {len(keys) - 40} more' if len(keys) > 40 else '')
          if keys else 'Keywords: TEST')
    print(f'AY clock {args.ay_clock:.0f} Hz' + ('' if args.ay_clock != ZXPAND_AY else ' (nominal; --setup to measure yours)') +
          f', tunes written for {args.tune_clock:.0f} Hz: periods x {args.ay_clock / args.tune_clock:.4f}')

    try:
        fd, old = open_port(device, args.baud)
    except OSError as e:
        ports = serial_ports()
        sys.exit(f'Cannot open {device}: {e.strerror}. ' +
                 (f'Adapters plugged in now: {", ".join(ports)}. ' if ports else 'Is the adapter plugged in? ') +
                 'Give the port, or run zxmusic.py --setup.')
    print(f'{streamed_frames() // 50} seconds streamed so far')
    print(f'Listening on {device} at {args.baud} baud. Ctrl-C to quit.')
    last_reply = 0.0
    try:
        while True:
            raw = read_keyword(fd)
            if any(b < 0x20 or b > 0x7E for b in raw):
                print(f"{time.strftime('%H:%M:%S')} ignored {len(raw)} bytes of line noise")
                continue
            raw = raw.replace(b'k', b'').replace(b'q', b'')   # late credits, never part of a keyword
            keyword = raw.decode('ascii').strip().upper()
            if not keyword:
                continue
            if keyword.startswith('?'):                     # MUSIC's browser
                now = time.monotonic()
                again = keyword == '?' and now - last_reply < 2.0
                try:
                    kind, what = lib.browse(keyword[1:])
                except OSError as e:                        # the tunes folder or music.conf, not the serial port
                    print(f"{time.strftime('%H:%M:%S')} browse {keyword[1:] or '-'}: can't read the tunes folder: {e.strerror or e}")
                    continue
                ok = send_reply(fd, tunelib.page_bytes(*what) if kind == 'page' else tunelib.play_bytes(what))
                note = (' (asked again: the last reply arrived damaged)' if again else '') + \
                       ('' if ok else ' - MUSIC stopped reading, reply cut short')
                if kind == 'page':
                    print(f"{time.strftime('%H:%M:%S')} browse {keyword[1:] or '-'}: {what[0].strip()}{note}")
                else:
                    print(f"{time.strftime('%H:%M:%S')} browse {keyword[1:]}: picked {what}{note}")
                last_reply = time.monotonic()
                continue                        # no input flush: the next request may follow quickly
            frames, what = frames_for(keyword, lib, args)
            if frames is None:                  # just the end marker
                stream(fd, [], args.window)
                print(f"{time.strftime('%H:%M:%S')} {keyword}: {what} [{raw.hex(' ')}]")
                time.sleep(0.6)
                termios.tcflush(fd, termios.TCIFLUSH)
                continue
            print(f"{time.strftime('%H:%M:%S')} {keyword}: {what}, {len(frames)} frames "
                  f"({len(frames) / 50:.0f} s)")
            t0 = time.monotonic()
            sent, how = stream(fd, frames, args.window)
            add_streamed(min(sent, len(frames)))
            print(f"{time.strftime('%H:%M:%S')} {keyword}: sent {min(sent, len(frames))} frames "
                  f"in {time.monotonic() - t0:.1f} s, {how}")
            time.sleep(0.6)                     # the ZX81 drains for 0.5 s after stopping
            termios.tcflush(fd, termios.TCIFLUSH)
    except KeyboardInterrupt:
        print('\nShutting down.')
    except OSError as e:
        print(f'\nSerial port went away ({e.strerror}) - adapter unplugged? Exiting.')
        sys.exit(1)
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old)
        except (OSError, termios.error):
            pass
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == '__main__':
    main()
