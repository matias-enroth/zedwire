#!/usr/bin/env python3
"""
zxpong - minimal serial ping/pong responder for ZXpand+ bring-up.

Dumps every byte that arrives (hex + printable), and whenever it sees
"PING" it answers "PONG!\\n". It recognises PING in both plain ASCII and
in the ZX81's own character set, and says which one it saw - so this
doubles as the "what does PUT SER actually put on the wire" probe.

Stdlib only (no pyserial).  Usage:
    ./zxpong.py /dev/cu.usbserial-XXXX [baud]
"""
import os, sys, select, termios, time

ZX = ' ??????????"?$:?()><=+-*/;,.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
PING_ASCII = b'PING'
PING_ZX81 = bytes(ZX.index(c) for c in 'PING')          # 35 2E 33 2C
BAUDS = {1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800,
         9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400}

def open_port(dev, baud):
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[0] = 0                                           # iflag: raw
    new[1] = 0                                           # oflag: raw
    new[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # 8N1, ignore modem lines
    new[3] = 0                                           # lflag: no echo/canon/sig
    new[4] = new[5] = BAUDS[baud]
    new[6][termios.VMIN] = 0
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd, old

def show(data):
    hx = ' '.join(f'{b:02X}' for b in data)
    asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
    zx = ''.join(ZX[b] if b < 64 else '.' for b in data)
    return f'{hx:<48} |{asc}|  zx81:|{zx}|'

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    dev = sys.argv[1]
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 38400
    fd, old = open_port(dev, baud)
    print(f'zxpong on {dev} @ {baud} 8N1. Ctrl-C to quit.')
    buf = b''
    try:
        while True:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            data = os.read(fd, 256)
            if not data:
                continue
            print(f'{time.strftime("%H:%M:%S")} RX {show(data)}')
            buf = (buf + data)[-64:]
            kind = 'ascii' if PING_ASCII in buf else 'zx81-charset' if PING_ZX81 in buf else None
            if kind:
                os.write(fd, b'PONG!\n')
                print(f'{time.strftime("%H:%M:%S")} got PING ({kind}) -> sent PONG!')
                buf = b''
    except KeyboardInterrupt:
        print('\nbye')
    except OSError as e:
        print(f'\nSerial port went away ({e.strerror}) - adapter unplugged? Exiting.')
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
