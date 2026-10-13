# SERVICE: ask the computer from the ZX81

```
LOAD "SERVICE:JOKE"
```

`zxservice.py` on the computer runs a command and the answer is printed on the
ZX81's screen: jokes, fortunes, the time, the weather, a calendar, and
arithmetic to more digits than the ZX81 has. Wire protocol:
[zxpand-serial.md](zxpand-serial.md#service-wire-protocol-zxservice).

## Quick start

Hardware: see the [README](../README.md#what-you-need) and
[wiring.md](wiring.md).

```sh
make                                                  # builds ./zxsvr
cp services.conf.example services.conf
./zxsvr service/SERVICE.P /dev/cu.usbserial-XXXX      # LOAD "$" on the ZX81
./zxservice.py services.conf /dev/cu.usbserial-XXXX
```

**First time:** on the ZX81, type `GOTO 35`. This saves SERVICE to the SD card
as a self-starting program and runs it (it asks for a keyword; type `TIME`).
`zxsvr` isn't needed for SERVICE again.
**Updating** to a newer `SERVICE.P`: load it with `zxsvr` again and type
`GOTO 30`, which deletes the old file first (`SAVE` doesn't replace it, so the
old one would keep loading).
Always put SERVICE on the card this way: a `SERVICE.P` copied onto the card
by hand doesn't start by itself, so `LOAD "SERVICE:TIME"` would just stop.
From then on:

```
LOAD "SERVICE:TIME"
LOAD "SERVICE:FORTUNE"
LOAD "SERVICE:WEATHER"
```

The text after the colon is the keyword. `LOAD "SERVICE"` on its own asks
for one.

## services.conf

`services.conf` maps keywords to shell commands, one per line. A few from
[`services.conf.example`](../services.conf.example):

```
TIME=date '+%H:%M:%S'
JOKE=curl -s -m 8 -H 'Accept: text/plain' https://icanhazdadjoke.com/

# Services that take an argument use "$1":  LOAD "SERVICE:CALC2**100"
CALC=echo "$1" | bc -l
PI=echo "scale=$1+5; p=4*a(1); scale=$1; p/1" | bc -l
```

The maths services do what the ZX81 can't: its numbers run out at about
9 digits. `CALC2**100` prints all 31 digits, `PI500` gives 500 digits of
pi, `FACT50` gives 50 factorial exactly (65 digits), and `FACTOR600851475143`
gives the prime factors.

- Keywords are case-insensitive. The keyword **only selects** a command from
  this file: nothing received over serial is ever executed.
- **Arguments:** keywords are letters only, so the argument is everything
  from the first non-letter on (`PI50` → `PI`, `50`). A command
  that uses `"$1"` gets it as a shell positional parameter, never pasted into
  the command text. It may only contain digits, spaces and
  `+ - * / ^ ( ) .`, and `**` becomes `^`. Commands without `$1` ignore any
  argument.
- Commands run in the config file's directory, so `helpers/...` paths work.
- Output is laid out for the ZX81: accents and symbols folded to plain ASCII
  (`Zürich` → `ZURICH`), wrapped to 32 columns, cut to 19 lines.
- Commands time out after 10 seconds. The whole process group is killed then,
  so `CALC9**9**9` can't leave a `bc` running.
- The example's `FORTUNE` uses `fortune -s -n 200 | fmt -w 32` to keep to
  short fortunes and rewrap them (`brew install fortune` on macOS).

Options: `--baud`, `--width`, `--lines`, and `--chunk`/`--pace-ms`, which
control how fast replies are sent (see below).

## Typing it on the ZX81

- **No spaces in `LOAD "SERVICE:..."`.** `LOAD "SERVICE:CALC 1/3"` arrives as
  `CALC`: `LOAD`/`GET PARM` stops at the first space. `PUT SER` keeps spaces,
  so `CALC 1/3` typed at SERVICE's prompt works. Otherwise write `CALC1/3`.
- To type `**` press `*` (shift-B) twice. The `**` key (shift-H) enters a
  keyword token, which the ZXpand+ turns into `,` and cuts off.

## Flow control

SERVICE sends a credit (`k`) for every 64 bytes it reads, and `zxservice`
never gets more than 192 bytes ahead, so a stalled ZX81 can't freeze the
ZXpand+ (see [zxpand-serial.md](zxpand-serial.md)). On top of that replies go
out in 32-byte chunks with 10 ms gaps.

- `SERVICE.P` and `zxservice.py` must be updated together: with an old
  `SERVICE.P` (no credits) `zxservice` gives up after 2 s and says so
  (`GOTO 30` updates it).
- If SERVICE prints `(SHORT n/m)`, bytes were lost: try `--pace-ms 20` or
  `--chunk 16`.
- `zxservice` and `zxmusic` both need the serial port: run one at a time.
