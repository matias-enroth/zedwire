# ZXpand+ serial: a practical reference

These notes cover using the ZXpand+ serial port from the ZX81: from BASIC,
from Z80 machine code, and the wire protocols this repo uses. They pull
together the ZXpand online manual, Charlie Robson's example code, and
testing on a real ZX81 + ZXpand+.

Each item is marked:

- **confirmed** — seen working on real hardware
- **from source** — taken from Charlie's example code, not tested separately here
- **untested** — inferred, or not tried yet

## Serial basics

- 8 data bits, no parity, 1 stop bit. 38400 baud is the rate `LOAD "$"` uses
  and is what everything here runs at. **confirmed**
- The port is on the ZXpand+'s 10-way extension (joystick) connector:
  pin 6 GND, pin 8 RX, pin 9 TX. It is TTL-level, so use a TTL USB-serial
  adapter. See [wiring.md](wiring.md). **confirmed**
- No flow control lines are used.

## From BASIC: the `ZXPAND` command

The ZXpand+ adds a `ZXPAND "..."` command. In a program it is stored as the
**`LPRINT` token (`$E1`)**. Tools that write `.p` files should emit `LPRINT`
(Charlie's `midiplay` source writes `LPRINT "OPEN MIDI"`). **confirmed**

Serial commands from the manual, with what was found:

| Command | Effect | Notes |
|---|---|---|
| `ZXPAND "OPE SER 38400"` | open serial, 8N1 | baud 1200–38400. **confirmed** at 38400 |
| `ZXPAND "PUT SER TEXT"` | send text | Sent as **plain ASCII** (the firmware converts from ZX81 codes), **with no line ending**. `"PUT SER PING"` puts `50 49 4E 47` on the wire. **confirmed** |
| `ZXPAND "PUT SER " + A$` | send a string variable | as above, spaces included. **confirmed** |
| `ZXPAND "PUT SER *nnnnn lll"` | send `lll` raw bytes from address `nnnnn` | per the manual. **untested** |
| `ZXPAND "GET SER"` | "copies the content of the serial buffer to the specified address" | Behaviour undocumented; not needed here. **untested** |
| `ZXPAND "CLO SER"` | close the port | **confirmed** |
| `ZXPAND "GET PARM"` | fetch the argument from the last `LOAD "NAME:ARG"` | see below |

The manual gives the IO buffer address as **16449**.

### `LOAD "NAME:ARG"` arguments

`LOAD "SERVICE:TIME"` loads `SERVICE.P` and stores `TIME` for the program to
pick up. After `ZXPAND "GET PARM"`:

- `PEEK 16446` = length of the argument
- the characters start at 16449

This layout comes from Charlie's `midiplay.asm`: `API_DLEN` = `$403E` (16446),
data from `API_DPTR` + 2 = 16449. **confirmed** (SERVICE reads `TIME`, `FORTUNE`, `WEATHER`
correctly this way).

The characters are **ZX81 codes**, as `midiplay` assumes: `LOAD "SERVICE:A"`
gives length 1 and code 38. **confirmed** (PEEKed from inside the program
before any other `ZXPAND` command; in immediate mode, 16449 held other data.)

The argument stops at the first space: `LOAD "SERVICE:PI 50"` gives `PI`.
**confirmed**

**At most 27 characters.** The argument is handed over at 16449, inside the
ZX81's printer buffer (`PRBUFF`, 16444-16476, whose last byte is the ENTER), so
there is room for 27. A 30-character argument arrived as the first 27 characters
and then two junk bytes (`LOAD "MUSIC:QWERTYUIOPASDFGHJKLZXCVBNM1234"` reached the
server as `QWERTYUIOPASDFGHJKLZXCVBNM1??`), and the length said 29. **confirmed**
that 27 is the last good character; the overflow going into the calculator's
memory (`MEMBOT`, 16477) is **inferred** from the ROM's system variable layout.
Programs here reject a length above 27 (`L>27`).

**Keyword tokens don't survive.** `LOAD "SERVICE:CALC9**9**9"` typed with
the `**` key (shift-H, a single token) reaches the server as `CALC9,`: the token
turns into a `,` somewhere in `LOAD`/`GET PARM`, and the rest is lost (possibly
`PUT SER` stopping at the comma). Type two separate `*`s (shift-B) instead.
**confirmed**

Also from the manual: add `STOP` (shift-A) to the end of a `LOAD` filename to
stop the program running by itself.

## From machine code: the port-level API

BASIC can send but has no good way to receive, so the receive side uses the
ZXpand+ I/O port directly. The commands come from Charlie's
[`serial-breakout/serialio.c`](https://github.com/charlierobson/ZXpand-Vitamins/blob/master/serial-breakout/serialio.c)
(comment: "requires use of firmware version 'M'"). Everything goes through
I/O port **`$07`**; the **high byte of BC** chooses the operation.

| Z80 | Operation | Status |
|---|---|---|
| `ld bc,$0007` / `out (c),a` | reset the transfer buffer | **confirmed** |
| `ld bc,$4007` / `out (c),a` | push byte `A` into the transfer buffer | **confirmed** |
| `ld bc,$E007` / `ld a,$C6` / `out (c),a` | send the buffered byte out of the serial port | **confirmed** |
| `ld bc,$E007` / `ld a,$C5` / `out (c),a`, then `in a,(c)` | `A` = number of received bytes waiting | **confirmed** |
| `ld bc,$E007` / `ld a,$C7` / `out (c),a`, then `in a,(c)` | `A` = next received byte | **confirmed** |
| `ld bc,$E007` / `ld a,$CB` / `out (c),a` | open serial at 1200 × *n* baud, *n* pushed into the buffer first (32 → 38400) | **from source** — `OPE SER` from BASIC is used here instead |
| `in a,($17)` / `and $80` | busy: loop until bit 7 clears after every command | **confirmed** |

Sending one byte (`A` = byte):

```z80
sendb:  ld d,a
        ld bc,$0007     ; reset transfer buffer
        ld a,c
        out (c),a
        call wcc
        ld a,d
        ld bc,$4007     ; byte into buffer
        out (c),a
        call wcc
        ld bc,$E007     ; transmit it
        ld a,$C6
        out (c),a
wcc:    in a,($17)      ; wait for command completion
        and $80
        jr nz,wcc
        ret
```

Receiving one byte, if any is waiting:

```z80
        ld bc,$E007
        ld a,$C5        ; how many waiting?
        out (c),a
        call wcc
        in a,(c)
        or a
        jr z,nothing
        ld bc,$E007
        ld a,$C7        ; fetch one
        out (c),a
        call wcc
        in a,(c)        ; A = byte
```

`wcc` doesn't touch BC, so `in a,(c)` right after it reads the result.

Practical notes:

- **The receive buffer holds data while BASIC is busy.** Bytes that arrive
  during a `ZXPAND` command or BASIC lines are there when the machine code
  starts polling. **confirmed**
- **The receive buffer keeps 255 bytes, and 256 unread bytes freeze the
  ZXpand+.** With the ZX81 not reading, 64-255 bytes were all kept and `$C5`
  reported them exactly; 256 froze the ZXpand+ so hard that only pulling the
  power helped. Keep the unread backlog under 256 bytes. **confirmed** (`SERTEST`)
- If the count from `$C5` is more than one, those bytes can be read without
  asking again (SERVICE does this). **confirmed** for a buffer that isn't
  being filled at the same time. **confirmed** while bytes keep arriving too,
  as long as the backlog stays small (`SERTEST`, with credits): reading this way
  the ZX81 keeps up with about 1,720 bytes/s; asking `$C5` before every byte
  manages only about 1,095. An early test without credits did lose and gain
  bytes; that didn't happen again (see [testing.md](testing.md)).
- About 1 byte in 15,000 arrives changed (2 seen in 30,000), with nothing lost
  or added. Anything that matters needs a checksum. **confirmed**
- Draining a full buffer, the ZX81 reads about 1,800 bytes/s in SLOW mode
  (255 bytes in about 7 frames; the frame count makes this rough). **confirmed**
- In SLOW mode the display takes most of the CPU, so polling is slow. For
  timeouts, the `FRAMES` system variable (16436) counts down once per frame
  (50 Hz) in SLOW mode only.
- Leave **IX and IY alone** — the ZX81 ROM uses them for the display and system
  variables. The routines here use only A, BC, DE and HL.

## `LOAD "$"` protocol (zxsvr)

Documented on the
[ZXpand-Vitamins wiki](https://github.com/charlierobson/ZXpand-Vitamins/wiki/Serial-Server-and-LOAD-%22$%22)
and implemented by `zxsvr.cs` / `zxsvr.c`. The ZXpand+ asks, the server answers:

| ZX81 sends | Server replies |
|---|---|
| `I` | file length, 2 bytes little-endian |
| `T`, block *n*, length *len* | *len* bytes (0 = 256) from offset *n* × 256, then a 2-byte little-endian sum of those bytes |
| `X` | done (sign-off) |

**`LOAD "$"` often ends with a garbled sign-off.** The program loads fine,
but the final `X` (`58`) arrives damaged, so far as `98` or `D8`, usually
followed by `FF`. `zxsvr` treats a stray byte after the whole file as the
sign-off and shows it bit by bit against `X`; loop mode (`-l`) counts clean
vs garbled ones:

```
 . [98]  ?
   Expected the sign-off 'X' here - the whole file has been sent.
   On the wire (start | bit 0 ... bit 7 | stop):
   expected 'X'   58:  0 | 0 0 0 1 1 0 1 0 | 1
   received       98:  0 | 0 0 0 1 1 0 0 1 | 1
                                       ^ ^
   followed by: FF
   Treating it as a garbled sign-off. The load itself should be fine.
```

## SERVICE wire protocol (zxservice)

| Direction | Bytes |
|---|---|
| ZX81 → server | request in ASCII: a keyword (letters only), optionally followed directly by an argument (`CALC2**100`). It ends at CR/LF **or** after 50 ms of silence, because `PUT SER` sends no line ending. |
| server → ZX81 | 2-byte little-endian length, then that many bytes of ASCII text. `\n` = new line. |
| ZX81 → server | while it reads the reply: `k` (a credit) after every 64 bytes stored, not counting the length. The server never sends more than 192 bytes beyond its credits and gives up after 2 s without one. Credits that arrive late are ignored; requests are upper case, so a lower-case `k` is never part of one |

The server lays the text out for the ZX81 screen before sending: plain ASCII
only, 32 columns, at most 19 lines. A line of exactly 32 characters gets no
`\n` after it, because the ZX81 moves to the next line by itself there. The
text is sent in 32-byte chunks with 10 ms gaps.

`PUT SER` only ever sends printable ASCII, so a request containing any other
byte is treated as line noise (switching the ZX81 off produces a burst of it)
and gets no reply.

## SERVICE machine code (service/service.asm)

Everything lives in the line-10 `REM`, starting at 16514.

| Address | What |
|---|---|
| `USR 16514` | receive one reply into the buffer. Returns the number of bytes received (0 = none). Waits 12 s for the first byte and 1 s between bytes. Sends `k` after every 64 bytes stored |
| `USR 16517` | print the buffer with ROM `RST $10`, converting ASCII to ZX81 codes (lowercase → uppercase, `'` → `"`, CR dropped, anything else unprintable → `?`) |
| 16520–16521 | length the server said it would send |
| 16522–16523 | bytes actually received |
| buffer | 1024 bytes, inside the REM |

If received < promised, SERVICE prints `(SHORT received/promised)`.

The conversion table doesn't contain a literal `$76` (the ZX81 end-of-line
code), and neither does the code. A `$76` inside a REM line runs fine but
confuses `LIST`.

## MUSIC wire protocol (zxmusic)

Used by `MUSIC` and `zxmusic.py`; see [music.md](music.md). All **confirmed**
on hardware.

### Requests

ZX81 → server, ASCII via `PUT SER`, ended by CR/LF or 50 ms of silence.

| Request | Means |
|---|---|
| `KEYWORD`, `KEYWORD/N` | play a tune, or its song *N* (up to 30 characters; `TEST` is built in) |
| `?` | music browser: the current page |
| `?N`, `?P` | next / previous page |
| `?0` | up a folder, or out of a song list |
| `?T` | top folder |
| `?01`-`?18` | entry *k* of the page: open or play it |

`LOAD "MUSIC"` with no argument starts in the music browser.

### Tune stream

Server → ZX81, one 15-byte frame per 50 Hz tick:

| Byte | |
|---|---|
| 0-12 | AY registers R0-R12 |
| 13 | R13, or `$FF` = don't write R13, or `$FE` = end of tune |
| 14 | checksum: all 15 bytes add up to 0 (mod 256) |

ZX81 → server: `k` (a credit) after every 60 bytes (4 frames) taken from the
buffer, however they were taken, and `q` once when it stops.

A frame with a bad checksum is dropped: that is the common case, a changed
byte. Two bad frames in a row mean a byte was lost or added and the framing is
off by one. The ZX81 then holds the sound and slides a 15-byte window over the
stream one byte at a time, until the bytes add up to 0 and R13 is `$FE`, `$FF`
or 0-15, and plays on from there. A lost or added byte costs two frames. It
gives up after 200 bytes without finding a frame. It waits 5 s for the first
frame and gives up on a stream silent for 2 s.

The server keeps at most `--window` frames in flight (default 10, maximum 16),
gives up after 3 s without a credit, and after a tune waits 0.6 s and flushes
its input (the ZX81 drains for 0.5 s after stopping). An unknown tune gets just
the end frame.

### Music browser replies

A 2-byte little-endian length, then the bytes, ending in a checksum (the bytes
add up to 0). A page starts with its row count (0-18); a tune pick starts with
`$FF` and then the keyword in ZX81 codes. MUSIC discards a reply that doesn't add up or whose row count or
length is impossible, and asks again (four tries). The server sends 8-byte
bursts about every 12 ms, at most 128 bytes ahead of the credits; MUSIC sends
`k` per 64 bytes read.

A page:

| Bytes | |
|---|---|
| 1 | rows (0-18) |
| 1 | selected row (0-based) |
| 1 | pages |
| 32 | header (inverse): folder or file, `n/m` |
| 32 × rows | entries in ZX81 codes; folders end in `/`, multi-song files show `(n)` |
| 32 × 2 | footer (inverse): keys, the chip in the corner |
| 1 | checksum |

A pick: `$FF`, the keyword in ZX81 codes, a checksum.

### MUSIC machine code (music/music.asm)

In the line-10 `REM` at 16514; no `$76` byte may appear in it. AY writes are
`out ($CF),reg` then `out ($0F),value`. `FRAMES` (16436) paces the tune in
`SLOW` mode, `LAST_K` (16421) reads keys. Only A, BC, DE, HL are touched.

| Address | |
|---|---|
| `USR 16514` | play. Returns 0 end, 1 key, 2 nothing arrived, 3 no frame found in 200 bytes, 4 stream stopped |
| `USR 16517` | chip: 0 none, 1 AY-3-8910, 2 YM2149, 3 other (writes R1, reads it back) |
| 16520, 16522, 16524, 16526 | frames played, bad, underruns, late (2 bytes each) |
| `USR 16528` | music browser. Returns 0 quit, 1 tune picked, 2 no reply |
| `USR 16531` | receive one reply into the buffer; returns the byte count. `k` per 64 bytes |
| 16534, 16536 | announced length, bytes received |
| 16538 | pointer to the reply (740-byte buffer in the REM) |

## Serial limits test (SERTEST)

How the limits above were measured.

```sh
./zxsvr sertest/SERTEST.P /dev/cu.usbserial-XXXX      # LOAD "$" on the ZX81
./sertest/zxsertest.py /dev/cu.usbserial-XXXX
```

On the ZX81: `RUN`. The ZX81 does what `zxsertest` tells it and sends back
everything it received; `zxsertest` compares byte for byte. The ZX81 shows
only `DONE`. Raw bytes of each run go to `sertest/runs/` (not committed).

1. **Receive buffer**: the ZX81 leaves the port alone while *N* bytes arrive
   (64 to 255), then reads them. `$C5 first` is what the ZXpand+ reports
   waiting; `ZX81 read B/s` is how fast it drains.
2. **Sustained**: 4096 bytes at 1,000 and 1,450 bytes/s and flat out, the ZX81
   reading as they arrive, either as many bytes as `$C5` last reported
   (`batch`, as SERVICE does) or asking `$C5` before every byte (`ask`).
   `extra` = bytes beyond the 4096 sent; `k gap ms` = longest wait between
   credits; `result` lists bad bytes as `position: sent>received`.

**Never let 256 bytes pile up unread: it hard-freezes the ZXpand+.** The
server sends nothing until the ZX81 says it is listening, and in the sustained
test the ZX81 sends a credit per 64 bytes read and the server stays within 192
bytes. `--help` lists the options; buffers over 255 need `--allow-freeze`.

## Open questions

The things still to check (`GET SER`, the receive buffer size, the garbled
`X`) are listed in [testing.md](testing.md).
