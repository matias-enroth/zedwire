#!/usr/bin/env python3
"""
tunecheck - will a tune sound right through zxmusic on the ZXpand+ AY?

    tools/tunecheck.py FILE_OR_FOLDER ...

For each tune (.vgm .vgz .ym .ay .psg .pt3; folders are searched) it prints a
verdict and why:

  complete   the music is all AY: it should sound like the original
  partial    other chips carry part of the music, which is lost (an SCC
             voice can be folded in, see zxmusic.py); how much of the
             file's chip writes go to the AY is shown as a rough guide
  no AY      nothing for the AY to play
  error      the file couldn't be read

and warns about what doesn't come through even on an AY:

  - a second AY ("x2" in a VGM): only the first chip is played
  - sampled sound (drums, speech) made by writing an AY volume register
    many times per frame: zxmusic sends 50 frames a second, so those
    writes are lost
  - YM5/YM6 digidrums and special effects (SID voice, sync-buzzer)

Platforms whose VGMs are usually all-AY: MSX games without SCC/FM sound,
the Vectrex, the Amstrad CPC, the ZX Spectrum 128 and Atari ST (these two
mostly come as .ay and .ym anyway), and early-80s arcade boards built
around AY chips (often two of them).

Needs nothing beyond zxmusic.py (and the z80 package for .ay files).
"""

import gzip, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import zxmusic  # noqa: E402

EXTS = ('.vgm', '.vgz', '.ym', '.ay', '.psg', '.pt3')

# VGM write commands -> chip
VGM_CMD_CHIP = {0x4F: 'SN76489', 0x50: 'SN76489', 0x51: 'YM2413', 0x52: 'YM2612', 0x53: 'YM2612',
                0x54: 'YM2151', 0x55: 'YM2203', 0x56: 'YM2608', 0x57: 'YM2608', 0x58: 'YM2610',
                0x59: 'YM2610', 0x5A: 'YM3812', 0x5B: 'YM3526', 0x5C: 'Y8950', 0x5D: 'YMZ280B',
                0x5E: 'YMF262', 0x5F: 'YMF262', 0xA0: 'AY', 0xB0: 'RF5C68', 0xB1: 'RF5C164',
                0xB2: 'PWM', 0xB3: 'Game Boy', 0xB4: 'NES APU', 0xB5: 'MultiPCM', 0xB6: 'uPD7759',
                0xB7: 'OKIM6258', 0xB8: 'OKIM6295', 0xB9: 'HuC6280', 0xBA: 'K053260', 0xBB: 'Pokey',
                0xBC: 'WonderSwan', 0xBD: 'SAA1099', 0xBE: 'ES5506', 0xBF: 'GA20', 0xC0: 'SegaPCM',
                0xC1: 'RF5C68', 0xC2: 'RF5C164', 0xC3: 'MultiPCM', 0xC4: 'QSound', 0xC5: 'SCSP',
                0xC6: 'WonderSwan', 0xC7: 'VSU', 0xC8: 'X1-010', 0xD0: 'YMF278B', 0xD1: 'YMF271',
                0xD2: 'Konami SCC', 0xD3: 'K054539', 0xD4: 'C140', 0xD5: 'ES5503', 0xD6: 'ES5506',
                **{c: 'YM2612' for c in range(0x80, 0x90)}}
SSG_CMDS = {0x55: 'YM2203', 0x56: 'YM2608', 0x58: 'YM2610'}   # register < $10 = the built-in AY-like SSG
DIGI_WRITES = zxmusic.DIGI_WRITES


def check_vgm(data):
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    if data[:4] != b'Vgm ':
        raise ValueError('not a VGM file')
    version = int.from_bytes(data[8:12], 'little')
    start = 0x34 + int.from_bytes(data[0x34:0x38], 'little') if version >= 0x150 else 0x40
    if start == 0x34:
        start = 0x40

    def field(o, size):
        return int.from_bytes(data[o:o + size], 'little') if o + size <= start else 0

    chips = []
    for off, name in zxmusic.VGM_CHIPS.items():
        if field(off, 4) & 0x3FFFFFFF:
            if off == 0x74:
                name = zxmusic.AY_TYPES.get(field(0x78, 1), name)
            chips.append(name + (' x2' if field(off, 4) & 0x40000000 else ''))
    has_ay = bool(field(0x74, 4) & 0x3FFFFFFF) and version >= 0x151
    dual = bool(field(0x74, 4) & 0x40000000)

    writes, ssg, ay2 = {}, {}, 0
    t, frame, vol_in_frame, digi_frames, frames = 0, 0, [0, 0, 0], 0, 0
    used = [False, False, False]
    p = start
    while p < len(data):
        c = data[p]
        if c == 0x66:
            break
        if c == 0x67:
            p += 7 + (int.from_bytes(data[p + 3:p + 7], 'little') & 0x7FFFFFFF)
            continue
        wait = 0
        if c == 0x61:
            wait = data[p + 1] | data[p + 2] << 8
        elif c == 0x62:
            wait = 735
        elif c == 0x63:
            wait = 882
        elif 0x70 <= c <= 0x7F:
            wait = (c & 0x0F) + 1
        elif 0x80 <= c <= 0x8F:
            wait = c & 0x0F
        chip = VGM_CMD_CHIP.get(c)
        if c == 0xA0:
            reg, val = data[p + 1], data[p + 2]
            if reg & 0x80:
                ay2 += 1
            else:
                writes['AY'] = writes.get('AY', 0) + 1
                if 8 <= reg <= 10:
                    vol_in_frame[reg - 8] += 1
                    if val & 0x1F:
                        used[reg - 8] = True
        elif c in SSG_CMDS and data[p + 1] < 0x10:
            ssg[SSG_CMDS[c]] = ssg.get(SSG_CMDS[c], 0) + 1
            writes[chip] = writes.get(chip, 0) + 1
        elif chip:
            writes[chip] = writes.get(chip, 0) + 1
        if wait:
            t += wait
            while t >= 882 * (frame + 1):
                if max(vol_in_frame) > DIGI_WRITES:
                    digi_frames += 1
                vol_in_frame = [0, 0, 0]
                frame += 1
        p += zxmusic.VGM_LEN.get(c, 1)
    frames = frame

    title = ''
    gd3 = int.from_bytes(data[0x14:0x18], 'little')
    if gd3 and data[0x14 + gd3:0x18 + gd3] == b'Gd3 ':
        f = data[0x14 + gd3 + 12:].decode('utf-16-le', errors='replace').split('\0')
        title = ' | '.join(x for x in (f[0], f[2] if len(f) > 2 else '', f[4] if len(f) > 4 else '') if x)

    notes = []
    total = sum(writes.values())
    others = {k: v for k, v in writes.items() if k != 'AY'}
    if not has_ay or not writes.get('AY'):
        verdict = 'no AY'
        if ssg:
            notes.append(f"{', '.join(ssg)} has an AY-like SSG part ({sum(ssg.values())} writes): "
                         "not played (yet)")
    elif others:
        verdict = 'partial'
        notes.append(f"lost: {', '.join(sorted(others))}; the AY gets {100 * writes['AY'] // max(total, 1)}% "
                     "of the chip writes (a rough guide: chips differ in how much they write)")
        if 'Konami SCC' in others and not all(used):
            notes.append(f"{3 - sum(used)} AY channel(s) free for SCC voices (zxmusic folds one in)")
    else:
        verdict = 'complete'
    if dual or ay2:
        notes.append(f'second AY ({ay2} writes): not played, only the first chip')
        if verdict == 'complete':
            verdict = 'partial'
    if frames and digi_frames > frames // 100:
        notes.append(f'sampled sound on the AY volume registers in {digi_frames} of {frames} frames: '
                     'lost (zxmusic plays AY kicks and snares in their place, or mutes them: | drums=off)')
        if verdict == 'complete':
            verdict = 'partial'
    if has_ay and writes.get('AY'):
        notes.append(f"AY channels used: {''.join('ABC'[i] for i in range(3) if used[i]) or 'none'}")
    return verdict, ' + '.join(chips) or '?', title, f'{frames / 50:.0f} s', notes


def check_ym(data):
    if data[2:5] == b'-lh' and data[6:7] == b'-':
        data = zxmusic.lha_unpack(data)
    if data[:4] in (b'MIX1', b'YMT1', b'YMT2'):
        return 'no AY', f'sampled sound ({data[:4].decode()})', '', '', ['digital audio, not AY registers']
    frames, title, clock = zxmusic.load_ym(data)
    notes, tag = [], data[:4]
    if tag in (b'YM5!', b'YM6!'):
        n = int.from_bytes(data[12:16], 'big')
        attrs = int.from_bytes(data[16:20], 'big')
        drums = int.from_bytes(data[20:22], 'big')
        if drums:
            notes.append(f'{drums} digidrum sample(s): not played')
        p = 34 + int.from_bytes(data[32:34], 'big')
        for _ in range(drums):
            p += 4 + int.from_bytes(data[p:p + 4], 'big')
        for _ in range(3):
            p = data.index(0, p) + 1
        body = data[p:p + 16 * n]

        def reg(r, i):
            return body[r * n + i] if attrs & 1 else body[i * 16 + r]
        fx = sum(1 for i in range(n) if reg(1, i) & 0x30 or reg(3, i) & 0x30)
        if fx:
            notes.append(f'YM5/6 special effects (SID voice, sync-buzzer, digidrums) in {fx} of {n} frames: '
                         'not played, the tune plays without them')
    verdict = 'partial' if notes else 'complete'
    return verdict, f'YM2149 ({tag.decode(errors="replace")}, {clock / 1e6:.4f} MHz)', title, \
        f'{len(frames) / 50:.0f} s', notes


def check(path):
    data = open(path, 'rb').read()
    if data[:8] == b'ZXAYEMUL':
        ay = zxmusic.AyFile(data)
        n = ay.count
        skipped = {i + 1: w for i in range(n) if (w := zxmusic.unplayable_song(data, ay, i))}
        playing = [i for i in range(n) if i + 1 not in skipped]
        if playing:                                       # the first song that plays on the AY is the one checked
            frames, name, author, clock = zxmusic.load_ay(data, playing[0])
        else:
            kinds = ' and '.join(sorted(set(skipped.values())))
            return 'no AY', f'ZX Spectrum {kinds} tune (.ay)', ay.song(ay.first)['name'], '', \
                [f'only {kinds} songs: nothing the AY can play (the library moves the file to unsupported/)']
        notes = []
        for kind, what in (('beeper', 'beeper tunes (the AY is not used)'),
                           ('sample', 'sampled sound (played by volume writes, not carried at 50 Hz)')):
            songs = [str(k) for k, w in skipped.items() if w == kind]
            if songs:
                notes.append(f"song{'s' if len(songs) > 1 else ''} {', '.join(songs)}: {what}; "
                             'left out of the browser')
        if not notes and n > 1:
            notes.append(f'{n} songs, the first checked')
        return 'partial' if skipped else 'complete', \
            'Amstrad CPC AY (.ay, 1 MHz)' if clock else 'ZX Spectrum AY (.ay)', \
            ' - '.join(x for x in (name, author) if x), f'{len(frames) / 50:.0f} s', notes
    if data[:13] == b'ProTracker 3.' or data[:14] == b'Vortex Tracker':
        mods = zxmusic.pt3_modules(data)
        frames, title = zxmusic.load_pt3(mods[0])
        if len(mods) > 1:
            return 'partial', 'TurboSound PT3 (2 AYs)', title, f'{len(frames) / 50:.0f} s', \
                ['two modules, one per AY: one plays at a time (| ts=2 for the other)']
        return 'complete', 'Pro Tracker 3 (.pt3)', title, f'{len(frames) / 50:.0f} s', []
    if data[:4] == b'Vgm ' or data[:2] == b'\x1f\x8b':
        return check_vgm(data)
    if data[:2] == b'YM' or (data[2:5] == b'-lh' and data[6:7] == b'-') or data[:4] == b'MIX1':
        return check_ym(data)
    if data[:3] == b'PSG':
        return 'complete', 'AY register dump (.psg)', '', f'{len(zxmusic.load_psg(data)) / 50:.0f} s', []
    raise ValueError('not a tune format I know')


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        sys.exit(__doc__)
    paths = []
    for a in sys.argv[1:]:
        if os.path.isdir(a):
            for d, _, files in sorted(os.walk(a)):
                paths += [os.path.join(d, f) for f in sorted(files) if f.lower().endswith(EXTS)]
        else:
            paths.append(a)
    counts = {}
    for path in paths:
        try:
            verdict, chips, title, length, notes = check(path)
        except Exception as e:                     # a broken file shouldn't stop a folder scan
            verdict, chips, title, length, notes = 'error', '', '', '', [f'{type(e).__name__}: {e}']
        counts[verdict] = counts.get(verdict, 0) + 1
        print(f'{verdict:9} {os.path.basename(path)}')
        print(f'          {chips}' + (f', {length}' if length else '') + (f' - {title}' if title else ''))
        for n in notes:
            print(f'          {n}')
    if len(paths) > 1:
        print('\n' + ', '.join(f'{v} {k}' for k, v in sorted(counts.items())))


if __name__ == '__main__':
    main()
