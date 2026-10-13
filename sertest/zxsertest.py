#!/usr/bin/env python3
"""
zxsertest - measures the ZXpand+ serial limits, with SERTEST.P on the ZX81.

Two things are measured, for streaming AY music later:

1. Receive buffer. The ZX81 is told to sit still for a while (not
   touching the serial port) while N bytes are sent in one go, then to read
   whatever was kept. Draining a full buffer also gives the fastest rate
   the ZX81 can read at.
2. Sustained receive. The ZX81 reads while the bytes arrive, sent at a few
   fixed rates and as fast as credits allow, to see what it keeps up with.
   Each rate runs twice: reading as many bytes as $C5 last said ("batch",
   as SERVICE does), and asking $C5 before every byte ("ask").

After each test the ZX81 sends back everything it received, so the
comparison is done here, byte for byte. Each run is saved under
sertest/runs/ (sent and received bytes, and a summary).

WARNING: 256 unread bytes hard-freeze the ZXpand+ (TOMTE firmware); only a
power cycle brings it back. So nothing is sent until the ZX81 has said it
is listening ('r'), the buffer test stops at 255 bytes, and the sustained
test never gets more than WINDOW bytes ahead of the credits the ZX81 sends
back (a 'k' per 64 bytes read). Buffer sizes over 255 need --allow-freeze.

Wire protocol (see sertest.asm):
  ZX81 -> server : 'H' and the protocol version (2) every second until the
                   first command
  server -> ZX81 : 'R' delay(2) n(2) flags(1)                 (little-endian)
  ZX81 -> server : 'r'  - listening; now the server sends the n test bytes
  ZX81 -> server : 'k' after every 64 bytes read
  ZX81 -> server : 'D' count(2) elap(2) av0(1) avmax(1) extra(2), then
                   the count bytes it received
  server -> ZX81 : 'Q' when done

Start this first, then RUN on the ZX81. Stdlib only (no pyserial). Usage:
    sertest/zxsertest.py /dev/cu.usbserial-XXXX
    sertest/zxsertest.py /dev/cu.usbserial-XXXX --sizes "" --rates 1000
"""

import argparse, difflib, json, os, random, select, struct, sys, termios, time

BAUDS = {1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800,
         9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400}
FPS = 50                  # ZX81 frames per second (PAL)
BUFSZ = 4096              # most the ZX81 side stores per test
SIZES = '64,128,192,224,240,255'
RATES = '1000,1450,0'     # bytes/s, 0 = as fast as credits allow. 1450 = worst-case AY stream
STREAM = 4096
SAFE_SIZE = 255           # 256 unread bytes froze the ZXpand+
CREDIT = 64               # bytes per 'k' from the ZX81
WINDOW = 192              # most bytes ever sent ahead of the credits
VERSION = 2               # must match VERSION in sertest.asm
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def open_port(dev, baud):
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[0] = 0                                                # iflag: raw
    new[1] = 0                                                # oflag: raw
    new[2] = termios.CS8 | termios.CREAD | termios.CLOCAL     # 8N1, ignore modem lines
    new[3] = 0                                                # lflag: raw
    new[4] = new[5] = BAUDS[baud]
    new[6][termios.VMIN] = 0
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd, old


def write_all(fd, data):
    while data:
        select.select([], [fd], [])
        n = os.write(fd, data)
        data = data[n:]


def read_byte(fd, timeout):
    r, _, _ = select.select([fd], [], [], max(0.0, timeout))
    if not r:
        return None
    b = os.read(fd, 1)
    if not b:
        raise OSError(5, 'serial port closed')
    return b[0]


def read_exact(fd, n, idle):
    """n bytes, giving up after `idle` seconds without one."""
    out = bytearray()
    while len(out) < n:
        r, _, _ = select.select([fd], [], [], idle)
        if not r:
            break
        b = os.read(fd, n - len(out))
        if not b:
            raise OSError(5, 'serial port closed')
        out += b
    return bytes(out)


def wait_for(fd, want, timeout, stray):
    """Read until the byte `want`; other bytes go to `stray`. False on timeout."""
    end = time.monotonic() + timeout
    while True:
        b = read_byte(fd, end - time.monotonic())
        if b is None:
            return False
        if b == want:
            return True
        stray.append(b)


def wait_hello(fd, timeout):
    """The ZX81's protocol version, 1 for the first SERTEST (a bare 'H'), None on timeout."""
    if not wait_for(fd, ord('H'), timeout, bytearray()):
        return None
    v = read_byte(fd, 0.3)                      # the version follows at once; the next 'H' after 1 s
    time.sleep(0.05)
    termios.tcflush(fd, termios.TCIFLUSH)       # another hello may be on its way
    return 1 if v in (None, ord('H')) else v


class Credits:
    """Counts the ZX81's 'k's, and when they came; anything else it sends
    is kept in `stray`."""

    def __init__(self, fd):
        self.fd, self.k, self.stray, self.times, self.t0 = fd, 0, bytearray(), [], time.monotonic()

    def got(self, b):
        if b == ord('k'):
            self.k += 1
            self.times.append(round(time.monotonic() - self.t0, 4))
        else:
            self.stray.append(b)

    def poll(self, timeout=0.0):
        b = read_byte(self.fd, timeout)
        if b is None:
            return False
        self.got(b)
        return True

    def max_gap(self):
        """Longest wait between credits, in ms: a stall in the ZX81's reading."""
        return round(1000 * max((b - a for a, b in zip(self.times, self.times[1:])), default=0))


def send_credited(fd, data, rate, cr):
    """Send at `rate` bytes/s (0 = as fast as credits allow) in one-frame
    slices, never more than WINDOW bytes ahead of the credits.
    Returns (seconds, bytes sent, waited for credits?)."""
    t0 = time.monotonic()
    per = max(1, round(rate / FPS)) if rate else 32
    sent, starved = 0, False
    while sent < len(data):
        chunk = data[sent:sent + per]
        while cr.poll():
            pass
        while sent + len(chunk) - cr.k * CREDIT > WINDOW:
            starved = True
            if not cr.poll(2.0):
                return time.monotonic() - t0, sent, starved     # the ZX81 stopped reading
        write_all(fd, chunk)
        sent += len(chunk)
        if rate:
            time.sleep(max(0.0, t0 + sent / rate - time.monotonic()))
    termios.tcdrain(fd)
    return time.monotonic() - t0, sent, starved


def run(fd, baud, n, delay_frames, rate, flags, seed):
    """One 'R' test. Returns a dict, or None if the ZX81 stopped answering."""
    payload = random.Random(seed).randbytes(n)
    write_all(fd, b'R' + struct.pack('<HHB', delay_frames, n, flags))
    cr = Credits(fd)
    if not wait_for(fd, ord('r'), 3, cr.stray):
        print(f'{n:>6}  the ZX81 did not say it was listening; nothing sent'
              + (f" (got {cr.stray[:32].hex(' ')})" if cr.stray else ''))
        return None
    if delay_frames:                          # buffer test: all at once, n <= 255
        t0 = time.monotonic()
        write_all(fd, payload)
        termios.tcdrain(fd)
        sent_s, sent, starved = time.monotonic() - t0, n, False
    else:
        sent_s, sent, starved = send_credited(fd, payload, rate, cr)
    # the report comes after the wait, the reading and 1-2 s of silence
    end = time.monotonic() + delay_frames / FPS + 6
    while True:
        b = read_byte(fd, end - time.monotonic())
        if b is None:
            print(f'{n:>6}  no report from the ZX81'
                  + (f"; got instead: {cr.stray[:32].hex(' ')}" if cr.stray else '; nothing came back'))
            return None
        if b == ord('D'):
            break
        cr.got(b)
    t0 = time.monotonic()
    head = read_exact(fd, 8, 3)
    if len(head) < 8:
        print(f'{n:>6}  report cut short: D {head.hex(" ")}')
        return None
    count, elap, av0, avmax, extra = struct.unpack('<HHBBH', head)
    got = read_exact(fd, count, 3)
    back_s = time.monotonic() - t0
    return dict(n=n, sent_n=sent, rate=rate, flags=flags, delay=delay_frames,
                sent=payload[:sent], got=got, count=count, elap=elap, av0=av0,
                avmax=avmax, extra=extra, credits=cr.k, credit_times=cr.times,
                max_gap=cr.max_gap(), starved=starved,
                stray=bytes(cr.stray), sent_s=sent_s, back_s=back_s)


def compare(sent, got, show=4):
    """'all', or what went wrong where: changed, lost and extra bytes."""
    if got == sent:
        return 'all'
    ops = [o for o in difflib.SequenceMatcher(None, sent, got, autojunk=False).get_opcodes()
           if o[0] != 'equal']
    out = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == 'replace' and i2 - i1 == j2 - j1:
            out += [f'{i}: {sent[i]:02X}>{got[j1 + i - i1]:02X}' for i in range(i1, i2)]
        else:
            if i2 > i1:
                out.append(f'{i1}: lost {i2 - i1}')
            if j2 > j1:
                out.append(f'{i1}: extra {j2 - j1}')
    more = f' +{len(out) - show} more' if len(out) > show else ''
    return f"{len(out)} bad - " + ', '.join(out[:show]) + more


def read_rate(r):
    """ZX81 read rate in bytes/s, from frames between its first and last byte."""
    if r['count'] < 2:
        return '-'
    if r['elap'] == 0:
        return f">{(r['count'] - 1) * FPS}"
    return f"{(r['count'] - 1) * FPS / r['elap']:.0f}"


def save(rundir, idx, kind, r):
    base = os.path.join(rundir, f"{idx:02d}-{kind}-n{r['n']}-rate{r['rate']}-flags{r['flags']}")
    with open(base + '.sent', 'wb') as f:
        f.write(r['sent'])
    with open(base + '.got', 'wb') as f:
        f.write(r['got'])
    meta = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in r.items() if k not in ('sent', 'got')}
    meta['result'] = compare(r['sent'], r['got'])
    with open(os.path.join(rundir, 'summary.jsonl'), 'a') as f:
        f.write(json.dumps(dict(test=idx, kind=kind, **meta)) + '\n')


def main():
    ap = argparse.ArgumentParser(description='Measure the ZXpand+ serial limits with SERTEST.P')
    ap.add_argument('device')
    ap.add_argument('--baud', type=int, default=38400, choices=sorted(BAUDS))
    ap.add_argument('--sizes', default=SIZES,
                    help=f'buffer test sizes, comma-separated (default {SIZES}); "" to skip')
    ap.add_argument('--rates', default=RATES,
                    help=f'sustained test rates in bytes/s, 0 = as fast as credits allow '
                         f'(default {RATES}); "" to skip')
    ap.add_argument('--modes', default='batch,ask',
                    help='sustained read modes: batch (as many as $C5 said), ask ($C5 before every byte)')
    ap.add_argument('--stream', type=int, default=STREAM,
                    help=f'bytes per sustained test, max {BUFSZ} (default {STREAM})')
    ap.add_argument('--allow-freeze', action='store_true',
                    help=f'allow buffer sizes over {SAFE_SIZE}, which can hard-freeze the ZXpand+')
    a = ap.parse_args()
    sizes = [int(x) for x in a.sizes.split(',') if x]
    rates = [int(x) for x in a.rates.split(',') if x]
    modes = [{'batch': 0, 'ask': 1}[m] for m in a.modes.split(',') if m]
    if any(not 0 < n <= BUFSZ for n in sizes + [a.stream]):
        sys.exit(f'sizes must be 1-{BUFSZ}')
    risky = [n for n in sizes if n > SAFE_SIZE]
    if risky and not a.allow_freeze:
        sys.exit(f'{risky}: 256 or more unread bytes hard-froze the ZXpand+. '
                 'Add --allow-freeze to run them anyway.')

    rundir = os.path.join(ROOT, 'sertest', 'runs', time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(rundir)
    fd, old = open_port(a.device, a.baud)
    idx = 0
    try:
        print(f'zxsertest on {a.device} @ {a.baud} 8N1. RUN SERTEST on the ZX81 now.')
        v = wait_hello(fd, 120)
        if v is None:
            sys.exit('No hello from the ZX81 in 120 s.')
        if v != VERSION:
            sys.exit(f'The ZX81 runs SERTEST protocol version {v}, this script speaks {VERSION}. '
                     'Load the current sertest/SERTEST.P.')
        print('ZX81 says hello.\n')

        if sizes:
            print('1. Receive buffer: the ZX81 waits while N bytes arrive, then reads.')
            print(f"{'sent':>6} {'$C5 first':>9} {'$C5 max':>7} {'got':>5} {'extra':>5}  "
                  f"{'ZX81 read B/s':>13}  result")
            for n in sizes:
                idx += 1
                delay = int((n * 10 / a.baud + 0.5) * FPS) + 1
                r = run(fd, a.baud, n, delay, 0, 0, seed=idx)
                if r is None:
                    print('Stopping.')
                    return
                save(rundir, idx, 'buffer', r)
                print(f"{n:>6} {r['av0']:>9} {r['avmax']:>7} {r['count']:>5} {r['extra']:>5}  "
                      f"{read_rate(r):>13}  {compare(r['sent'], r['got'])}")
            print()

        if rates and modes:
            print(f'2. Sustained: {a.stream} bytes, the ZX81 reading while they arrive.')
            print(f"{'rate B/s':>8} {'mode':>5} {'send s':>6} {'$C5 max':>7} {'got':>5} {'extra':>5}  "
                  f"{'ZX81 read B/s':>13} {'k gap ms':>8}  result")
            for rate in rates:
                for flags in modes:
                    idx += 1
                    r = run(fd, a.baud, a.stream, 0, rate, flags, seed=idx)
                    if r is None:
                        print('Stopping.')
                        return
                    save(rundir, idx, 'sustained', r)
                    note = ''
                    if r['sent_n'] < a.stream:
                        note = f"  (ZX81 stopped sending credits after {r['sent_n']} bytes)"
                    elif r['starved'] and rate:
                        note = '  (waited for credits)'
                    print(f"{rate or 'max':>8} {['batch', 'ask'][flags]:>5} {r['sent_s']:>6.2f} "
                          f"{r['avmax']:>7} {r['count']:>5} {r['extra']:>5}  {read_rate(r):>13} "
                          f"{r['max_gap']:>8}  {compare(r['sent'], r['got'])}{note}")
            print()

        write_all(fd, b'Q')
        termios.tcdrain(fd)
        print(f'Done. The ZX81 should say DONE. Saved in {os.path.relpath(rundir)}')
    except KeyboardInterrupt:
        print('\nbye')
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
