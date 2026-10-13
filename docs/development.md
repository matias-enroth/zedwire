# Development

## Repository layout

| Path | What |
|---|---|
| `zxsvr.c` | `LOAD "$"` server (C, POSIX termios, no dependencies) |
| `zxservice.py`, `services.conf.example` | SERVICE server: keyword → command ([service.md](service.md)) |
| `helpers/` | helper scripts for services (`fact.py`, `factor.py`) |
| `service/` | ZX81 side of SERVICE: `service.asm` (Z80), `service.bas`, `table.inc`, prebuilt `SERVICE.P` |
| `zxmusic.py`, `music.conf.example` | music server: tunes → AY register frames, streamed with credits ([music.md](music.md)) |
| `tunelib.py` | the music server's tune library: folder scan, keywords, the ZX81 browser's pages |
| `music/` | ZX81 side of MUSIC: `music.asm` (the frame player), `music.bas`, prebuilt `MUSIC.P` |
| `pingpong/` | link test: `pingpong.asm`, `pingpong.bas`, prebuilt `PINGPONG.P`, `zxpong.py` ([wiring.md](wiring.md#check-the-link)) |
| `aytone/` | AY tone, clock and chip test: `aytone.asm`, `aytone.bas`, prebuilt `AYTONE.P` ([music.md](music.md#aytone)) |
| `sertest/` | serial limits test: `sertest.asm`, `sertest.bas`, prebuilt `SERTEST.P`, `zxsertest.py` ([zxpand-serial.md](zxpand-serial.md#serial-limits-test-sertest)) |
| `tools/mkp.py` | builds a `.p` file from a BASIC text file + a machine-code blob |
| `tools/p2bas.py` | lists the BASIC in a `.p` file |
| `tools/mktable.py` | generates the ASCII → ZX81 character table |
| `tools/ayclock.py` | measures the ZX81 and AY clocks from a recording of AYTONE's clock test (needs numpy) |
| `tools/tunecheck.py` | will a tune sound right on the AY? Checks `.vgm`/`.vgz`/`.ym`/`.ay`/`.psg` files or folders |
| `tools/simulate.py` | runs the Z80 code in an emulator against a fake ZXpand+ and AY |
| `third_party/vt2-pt3-player/` | S.V. Bulba's Vortex Tracker II PT3 player (Z80), used by `zxmusic.py` for `.pt3`; his terms, not MIT |

## Building the ZX81 programs

The `.bin` and `.P` files are committed, so you only need this if you change
the assembly or BASIC:

```sh
make zx        # needs GNU z80asm: brew install z80asm / apt install z80asm
make test      # runs the Z80 code in an emulator, needs the z80 package
```

For `make test`, install the `z80` package in a venv (Homebrew's Python and
recent Debian/Ubuntu refuse a global `pip install`; on Debian/Ubuntu you may
need `apt install python3-venv` first). The package is built from source on
Linux, so you also need a C++ compiler and the Python headers (Fedora:
`sudo dnf install gcc-c++ python3-devel`; Debian/Ubuntu:
`sudo apt install g++ python3-dev`). `make` uses `.venv` automatically:

```sh
python3 -m venv .venv && .venv/bin/pip install z80
```

The machine code sits in a `REM` in line 10, starting at 16514. Neither
program starts by itself when loaded over serial: `GOTO 35` (`GOTO 30` when
updating) saves a copy to SD that does.

What has been tested on real hardware, and what is still open:
[testing.md](testing.md).
