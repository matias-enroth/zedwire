#!/usr/bin/env python3
"""
zxservice - keyword -> shell command server for the ZX81 (ZXpand+ serial).

The ZX81 side is SERVICE.P: LOAD "SERVICE:TIME" reads its colon argument
via ZXPAND "GET PARM", sends it with ZXPAND "PUT SER ...", then receives
the reply with a small machine-code routine and prints it.

Wire protocol
-------------
ZX81 -> server : ASCII request: a keyword, optionally followed by an
                 argument ("CALC2**100"). Keywords are letters only, so the
                 argument starts at the first non-letter. A space in between
                 is allowed: PUT SER sends it, but LOAD "SERVICE:..." (GET
                 PARM) cuts the argument at the first space. PUT SER sends
                 no line terminator, so a request ends at CR/LF *or* after
                 a short idle gap.
server -> ZX81 : 2-byte little-endian length, then that many bytes of
                 ASCII text ('\\n' = new line).

The reply is laid out for the ZX81 screen: wrapped to 32 columns and cut
to fit under the header line. A line of exactly 32 characters gets no
'\\n' after it, since the ZX81 wraps to the next line by itself there.

Config file
-----------
One KEYWORD=command per line, '#' comments. Keywords are matched
case-insensitively. The keyword only selects a command from this file;
nothing from the wire is ever executed.

A command that mentions "$1" takes an argument, e.g.

    CALC=echo "$1" | bc -l

The argument is passed as a positional parameter (sh -c CMD zxservice ARG),
never pasted into the command text, and may only contain digits, spaces
and + - * / ^ ( ) . - enough for arithmetic, not enough to do mischief.
"**" is turned into "^", since that's how you'd type a power on a ZX81.
Commands run in the config file's directory.

Stdlib only (no pyserial). Usage:
    zxservice.py services.conf /dev/cu.usbserial-XXXX [--baud 38400]
"""

import argparse, os, re, select, signal, subprocess, sys, termios, textwrap, time, unicodedata

COMMAND_TIMEOUT = 10     # seconds; SERVICE.P waits 12 s for a reply to start
IDLE_GAP = 0.05          # seconds of silence that ends a keyword
MAX_KEYWORD = 32
ARG_OK = re.compile(r'[0-9 +\-*/^().]{1,30}')
BAUDS = {1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800,
         9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400}


def load_config(path):
    commands = {}
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, cmd = line.partition('=')
            key, cmd = key.strip().upper(), cmd.strip()
            if not sep or not key or not cmd:
                print(f"{path}:{lineno}: skipping malformed line: {line}", file=sys.stderr)
                continue
            commands[key] = cmd
    return commands


def takes_arg(cmd):
    return '$1' in cmd or '${1' in cmd


def decode(b):
    """Command output as text. Not everything is UTF-8 (old fortune files,
    some locales); fall back to Latin-1 rather than crash the server."""
    try:
        return b.decode('utf-8')
    except UnicodeDecodeError:
        return b.decode('latin-1')


def run_command(cmd, arg='', cwd=None):
    """Run cmd via sh with arg as $1. The whole process group is killed on
    timeout, so a runaway pipeline (bc on 9^9^9) can't linger."""
    env = dict(os.environ, BC_LINE_LENGTH='0')     # bc: don't split long numbers
    try:
        p = subprocess.Popen(['/bin/sh', '-c', cmd, 'zxservice', arg], cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    except Exception as e:
        return f"(error: {e})"
    try:
        out, err = p.communicate(timeout=COMMAND_TIMEOUT)
        out, err = decode(out), decode(err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        return "(command timed out)"
    # keep leading spaces (cal centres its header); drop blank lines around
    out = out.rstrip().lstrip('\n')
    if out:
        return out
    err = err.strip().splitlines()
    return err[-1] if err else "(no output)"          # e.g. a traceback's last line


def handle(request, commands, cwd):
    """request -> (text, tag). "PI50", "PI 50" -> keyword PI, argument 50."""
    m = re.match(r'([A-Z]*)(.*)', request)
    keyword, arg = m.group(1), m.group(2)
    if keyword not in commands:
        # "CALCX": no keyword CALCX, but CALC takes an argument - so the
        # argument is "X" (and gets rejected below). Longest match wins,
        # so FACTOR beats FACT.
        prefixes = [k for k, c in commands.items()
                    if takes_arg(c) and keyword.startswith(k)]
        if prefixes:
            k = max(prefixes, key=len)
            keyword, arg = k, keyword[len(k):] + arg
    arg = ' '.join(arg.split()).replace('**', '^')
    cmd = commands.get(keyword)
    if cmd is None:
        return f"NO SUCH SERVICE: {keyword}", 'unknown'
    if not takes_arg(cmd):
        text = run_command(cmd, cwd=cwd)
        return text, 'timed out' if text == "(command timed out)" else 'ok'
    if not arg:
        return f"USAGE: {keyword}<ARGUMENT>, E.G. {keyword}50", 'no argument'
    if not ARG_OK.fullmatch(arg):
        return f"BAD ARGUMENT: {arg}\nDIGITS AND + - * / ** ( ) . ONLY", 'bad argument'
    text = run_command(cmd, arg, cwd=cwd)
    return text, 'timed out' if text == "(command timed out)" else 'ok'



# Things NFKD won't decompose into plain ASCII.
ZX_FOLD = str.maketrans({
    'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'ß': 'ss', 'đ': 'd', 'Đ': 'D',
    'ł': 'l', 'Ł': 'L', 'þ': 'th', 'Þ': 'TH', 'ð': 'd', 'Ð': 'D', 'ı': 'i',
    '‘': "'", '’': "'", '‚': ',', '“': '"', '”': '"', '„': '"', '«': '"', '»': '"',
    '–': '-', '—': '-', '−': '-', '…': '...', '•': '*', '·': '.', '×': '*', '÷': '/',
    '€': 'EUR', '£': 'GBP', '©': '(C)', '®': '(R)', '™': 'TM',
    '\u00a0': ' ', '\u2009': ' ', '\u202f': ' ',
})


def zx_fold(text):
    """Dumb text down to plain ASCII: zurich, not z?rich. Accents are
    dropped, a few symbols spelled out, anything else (emoji etc) removed."""
    text = unicodedata.normalize('NFKD', text.translate(ZX_FOLD))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return text.encode('ascii', errors='ignore').decode('ascii')


def zx_layout(text, width, max_lines):
    """Wrap for a width-column screen; returns ASCII bytes."""
    text = zx_fold(text)
    lines = []
    for para in text.expandtabs(4).replace('\r', '').split('\n'):
        lines += textwrap.wrap(para, width) or ['']
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines - 1] + ['...']
    out = ''
    for i, ln in enumerate(lines):
        out += ln
        if i < len(lines) - 1 and len(ln) < width:
            out += '\n'
    return out.encode('ascii', errors='replace')


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


def send_reply(fd, data, chunk, pace):
    """Length prefix, then the text in small paced chunks so the ZXpand's
    receive buffer isn't outrun while the ZX81 polls it."""
    write_all(fd, bytes([len(data) & 0xff, (len(data) >> 8) & 0xff]))
    for i in range(0, len(data), chunk):
        write_all(fd, data[i:i + chunk])
        if pace:
            termios.tcdrain(fd)
            time.sleep(pace)


def read_keyword(fd):
    """Block until a keyword arrives; ends at CR/LF or an idle gap."""
    buf = b''
    while True:
        r, _, _ = select.select([fd], [], [], IDLE_GAP if buf else None)
        if not r:
            return buf                                        # idle gap after data
        data = os.read(fd, 64)
        if not data:
            continue
        for b in data:
            if b in (10, 13):
                if buf:
                    return buf
            else:
                buf += bytes([b])
        if len(buf) > MAX_KEYWORD:
            return buf


def main():
    ap = argparse.ArgumentParser(description="Keyword -> command server for the ZX81, over serial")
    ap.add_argument('config')
    ap.add_argument('device')
    ap.add_argument('--baud', type=int, default=38400, choices=sorted(BAUDS))
    ap.add_argument('--width', type=int, default=32, help="wrap width (0 = no layout)")
    ap.add_argument('--lines', type=int, default=19, help="max reply lines")
    ap.add_argument('--chunk', type=int, default=32, help="bytes per paced chunk")
    ap.add_argument('--pace-ms', type=float, default=10, help="pause between chunks (0 = none)")
    args = ap.parse_args()

    commands = load_config(args.config)
    if not commands:
        sys.exit(f"No usable KEYWORD=command lines in {args.config}")
    print(f"Loaded {len(commands)} service(s): "
          + ', '.join(k + (' <arg>' if takes_arg(c) else '') for k, c in sorted(commands.items())))
    cwd = os.path.dirname(os.path.abspath(args.config))

    fd, old = open_port(args.device, args.baud)
    print(f"Listening on {args.device} at {args.baud} baud. Ctrl-C to quit.")
    try:
        while True:
            raw = read_keyword(fd)
            hexed = ' '.join(f'{b:02X}' for b in raw)
            if any(b < 0x20 or b > 0x7E for b in raw):
                # PUT SER only ever sends printable ASCII. Anything else is line
                # noise (e.g. the ZX81 being switched off) - don't answer it.
                shown = hexed if len(raw) <= 16 else ' '.join(hexed.split()[:16]) + ' ...'
                print(f"{time.strftime('%H:%M:%S')} ignored {len(raw)} bytes of line noise [{shown}]")
                continue
            request = raw.decode('ascii').strip().upper()
            if not request:
                continue
            text, tag = handle(request, commands, cwd)
            data = zx_layout(text, args.width, args.lines) if args.width else \
                zx_fold(text).encode('ascii')[:1000]
            send_reply(fd, data, args.chunk, args.pace_ms / 1000)
            print(f"{time.strftime('%H:%M:%S')} <- {request!r} [{hexed}] -> {len(data)} bytes ({tag})")
    except KeyboardInterrupt:
        print("\nShutting down.")
    except OSError as e:
        print(f"\nSerial port went away ({e.strerror}) - adapter unplugged? Exiting.")
        sys.exit(1)
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old)
        except (OSError, termios.error):
            pass                      # the device is already gone
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == '__main__':
    main()
