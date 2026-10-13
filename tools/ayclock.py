#!/usr/bin/env python3
"""
ayclock - measure your ZX81's CPU clock and the ZXpand+ AY clock from a
recording of AYTONE's clock test.

On the ZX81: LOAD AYTONE, RUN 300, give a period (252), start recording,
press a key. It plays 10 beeps in FAST mode, one every 6,500,420 Z80
T-states. Record about 25 s with any microphone near the speaker (on a Mac:
QuickTime Player, File > New Audio Recording), then:

    tools/ayclock.py recording.m4a [--period 252]

The time between beeps gives the CPU clock (6,500,420 T-states / seconds
per beep); the pitch gives the AY clock (16 x period x Hz). The ZX81's clock
comes from a ceramic resonator, not a crystal, so it is often a percent or
so off its nominal 3.25 MHz, and the AY clock (half of it) with it.

Reads WAV directly; other formats (m4a, mp3, ...) are converted with
afconvert (built into macOS) or ffmpeg. Needs numpy: in the repo's venv,
.venv/bin/pip install numpy.
"""

import argparse, os, shutil, subprocess, sys, tempfile, wave

try:
    import numpy as np
except ImportError:
    sys.exit('needs numpy. From the repo root:\n'
             '  python3 -m venv .venv && .venv/bin/pip install numpy\n'
             '  .venv/bin/python tools/ayclock.py recording.m4a')

TSTATES = 6_500_420         # AYTONE clock test: T-states from one beep to the next
NOMINAL_CPU = 3_250_000     # ZX81: 6.5 MHz resonator / 2


def load(path):
    """Mono float samples and the sample rate."""
    if not path.lower().endswith('.wav'):
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
        if shutil.which('afconvert'):
            cmd = ['afconvert', '-f', 'WAVE', '-d', 'LEI16', path, tmp]
        elif shutil.which('ffmpeg'):
            cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-i', path, '-acodec', 'pcm_s16le', tmp]
        else:
            sys.exit('Not a WAV file, and neither afconvert nor ffmpeg is installed to convert it.')
        try:
            subprocess.run(cmd, check=True)
            return load(tmp)
        finally:
            os.unlink(tmp)
    with wave.open(path) as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, '<i2').astype(float)
    elif width == 4:
        x = np.frombuffer(raw, '<i4').astype(float)
    elif width == 1:
        x = np.frombuffer(raw, 'u1').astype(float) - 128
    else:
        sys.exit(f'unsupported WAV sample width {width}')
    return x.reshape(-1, ch).mean(axis=1), sr


def peak_freq(seg, sr, lo, hi):
    """Strongest frequency between lo and hi, to a small fraction of a Hz."""
    n = 1 << max(20, int(np.ceil(np.log2(len(seg)))) + 3)        # zero-pad for a fine grid
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n))
    f = np.fft.rfftfreq(n, 1 / sr)
    band = (f >= lo) & (f <= hi)
    k = np.flatnonzero(band)[np.argmax(spec[band])]
    a, b, c = np.log(spec[k - 1:k + 2] + 1e-30)
    return f[k] + 0.5 * (a - c) / (a - 2 * b + c) * sr / n      # parabolic interpolation


def edges(env, t, thr):
    """Times where env crosses thr upwards and downwards, interpolated."""
    on = env > thr
    up, down = [], []
    for i in np.flatnonzero(on[1:] != on[:-1]) + 1:
        tt = t[i - 1] + (thr - env[i - 1]) / (env[i] - env[i - 1]) * (t[i] - t[i - 1])
        (up if on[i] else down).append(tt)
    return np.array(up), np.array(down)


def the_number(ay, hint):
    """The one figure zxmusic needs, last and unmissable."""
    print(f'\n==> AY clock for zxmusic.py: {ay:.0f} Hz')
    if hint:
        print(f'    Type {ay:.0f} at the zxmusic.py --setup prompt, or run it with --ay-clock {ay:.0f}')


def main(argv=None, hint=True):
    """Prints the measurements; returns the AY clock in Hz. hint=False when
    zxmusic.py --setup runs it: setup uses the number itself."""
    ap = argparse.ArgumentParser(description="Measure the ZX81 and AY clocks from a recording of AYTONE's clock test (RUN 300)")
    ap.add_argument('recording')
    ap.add_argument('--period', type=int, default=252, help='the tone period you gave AYTONE (default 252)')
    a = ap.parse_args(argv)
    x, sr = load(a.recording)
    x = x - x.mean()
    print(f'{a.recording}: {len(x) / sr:.1f} s at {sr} Hz')

    # 1. find the tone: AY clocks from 1.4 to 1.9 MHz cover any ZX81 that works
    lo, hi = 1.4e6 / (16 * a.period), 1.9e6 / (16 * a.period)
    if hi > sr / 2:
        sys.exit(f'period {a.period} is too high-pitched for {sr} Hz audio; use 252')
    f0 = peak_freq(x, sr, lo, hi)

    # 2. the beeps: the tone's strength in 1 ms steps, smoothed over 10 ms
    t = np.arange(len(x)) / sr
    z = x * np.exp(-2j * np.pi * f0 * t)
    env = np.abs(np.convolve(z, np.ones(int(sr * 0.01)) / int(sr * 0.01), 'same'))
    step = max(1, sr // 1000)
    env, te = env[::step], t[::step]
    quiet, loud = np.percentile(env, 20), np.percentile(env, 90)
    snr = loud / max(quiet, 1e-12)
    up, down = edges(env, te, (quiet + loud) / 2)
    # a beep: up, then down 0.6-1.6 s later (they last ~1.04 s)
    beeps = []
    for u in up:
        d = down[down > u]
        if len(d) and 0.6 < d[0] - u < 1.6:
            beeps.append((u, d[0]))

    # 3. tone frequency, measured inside each beep (or over the whole file)
    if beeps:
        fs = np.array([peak_freq(x[int((u + 0.1) * sr):int((d - 0.1) * sr)], sr, f0 - 5, f0 + 5)
                       for u, d in beeps])
        f, f_err = fs.mean(), (fs.std(ddof=1) / np.sqrt(len(fs)) if len(fs) > 1 else float('nan'))
    else:
        f, f_err = f0, float('nan')
    ay = 16 * a.period * f
    print(f'Tone: {f:.3f} Hz' + (f' (± {f_err:.3f})' if f_err == f_err else '')
          + f', tone {snr:.0f}x louder than the quiet parts')
    print(f'AY clock: {ay / 1e6:.4f} MHz  (16 x {a.period} x {f:.3f} Hz)')

    if len(beeps) < 3:
        print(f'\nFound {len(beeps)} beeps, need at least 3 to time them, so no CPU clock.'
              + ('' if snr > 5 else ' The recording is quiet or noisy: move the microphone closer.'))
        the_number(ay, hint)
        return ay
    # 4. beep spacing: a straight line through the beep starts (and ends), so
    #    one missed beep doesn't matter
    starts = np.array([u for u, _ in beeps])
    ends = np.array([d for _, d in beeps])
    rough = np.median(np.diff(starts))
    idx = np.round((starts - starts[0]) / rough)
    p_up = np.polyfit(idx, starts, 1)
    p_dn = np.polyfit(idx, ends, 1)
    cycle = (p_up[0] + p_dn[0]) / 2
    jitter = np.std(starts - np.polyval(p_up, idx)) * 1000
    cpu = TSTATES / cycle
    print(f'Beeps: {len(beeps)}, one every {cycle:.5f} s (timing scatter {jitter:.1f} ms)')
    # the cycle is a straight-line fit through the beep starts, so its uncertainty is the
    # scatter divided by about 9 for 10 beeps; too noisy if that is over 0.05% of the cycle
    # (the AY / CPU test below allows 0.1%). 7 ms of scatter is fine, 80 ms is not
    slope_err = jitter / 1000 / np.sqrt(((idx - idx.mean()) ** 2).sum())
    noisy = slope_err / cycle > 0.0005
    print(f'CPU clock: {cpu / 1e6:.4f} MHz  ({(cpu / NOMINAL_CPU - 1) * 100:+.2f}% from 3.25 MHz)'
          + ('  - not reliable, see below' if noisy else ''))
    ratio = ay / cpu
    if noisy:
        print('AY / CPU: not judged. The beep edges are too noisy for the CPU clock; the AY clock above comes from\n'
              'the pitch of the tone and is still good. For the CPU clock, put the microphone closer to the speaker\n'
              'and record where it is quiet.')
    else:
        print(f'AY / CPU: {ratio:.5f}' + ('  - the AY runs off the ZX81 clock / 2' if abs(ratio - 0.5) < 0.0005
                                          else '  - not half the CPU clock: the AY has a clock of its own?'))
    the_number(ay, hint)
    return ay


if __name__ == '__main__':
    main()
