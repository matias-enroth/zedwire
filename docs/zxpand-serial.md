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
- **Buffer size is unknown.** If the count from `$C5` is more than one, those
  bytes can be read without asking again (SERVICE does this). `zxservice`
  paces its replies by default, but a 579-byte reply sent in one go at
  38400 baud also arrives intact. **confirmed**
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

## SERVICE wire protocol (zxservice)

| Direction | Bytes |
|---|---|
| ZX81 → server | request in ASCII: a keyword (letters only), optionally followed directly by an argument (`CALC2**100`). It ends at CR/LF **or** after 50 ms of silence, because `PUT SER` sends no line ending. |
| server → ZX81 | 2-byte little-endian length, then that many bytes of ASCII text. `\n` = new line. |

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
| `USR 16514` | receive one reply into the buffer. Returns the number of bytes received (0 = none). Waits 12 s for the first byte and 1 s between bytes. |
| `USR 16517` | print the buffer with ROM `RST $10`, converting ASCII to ZX81 codes (lowercase → uppercase, `'` → `"`, CR dropped, anything else unprintable → `?`) |
| 16520–16521 | length the server said it would send |
| 16522–16523 | bytes actually received |
| buffer | 1024 bytes, inside the REM |

If received < promised, SERVICE prints `(SHORT received/promised)`.

The conversion table doesn't contain a literal `$76` (the ZX81 end-of-line
code), and neither does the code. A `$76` inside a REM line runs fine but
confuses `LIST`.

## Open questions

The things still to check (`GET SER`, the receive buffer size, the garbled
`X`) are listed in [testing.md](testing.md).
