# Hardware testing

What has been checked on a real ZX81 + ZXpand+ (issue 5.1 board, TOMTE firmware), from macOS
(Apple silicon) with an FT232RL adapter. `zxservice` has also run from Fedora
on a ThinkPad, and `zxsvr` from a Raspberry Pi and a Linux laptop.

## Confirmed

- [x] `LOAD "$"` via `zxsvr`, including loop mode (`-l`)
- [x] Garbled sign-off detected and shown bit by bit, with clean vs garbled counts in loop mode
- [x] Ping/pong in both directions, via machine code (`RUN`) and via BASIC `PUT SER` (`RUN 400`)
- [x] SERVICE with `LOAD "SERVICE:<keyword>"`: `TIME`, `DATE`, `FORTUNE`, `WEATHER`, `CAL`, `JOKE`, `UNAME`, `UPTIME`, `SUN`, `PING`
- [x] Arguments: `CALC1/3`, `CALC9**13`, `PI500`, `FACT50`, `FACTOR360`
- [x] Spaces survive `PUT SER`: `PI 50` typed at SERVICE's prompt arrives as `PI 50`.
      `LOAD "SERVICE:PI 50"` arrives as `PI`: the argument stops at the space in `LOAD`/`GET PARM`
- [x] `GET PARM` returns ZX81 codes: `LOAD "SERVICE:A"` gives length 1, code 38
      (a debug `PRINT PEEK 16446;" ";PEEK 16449` at line 245 of SERVICE)
- [x] A full-screen reply: `PI700` (about 580 bytes, the 19-line cap) with no lost bytes
- [x] The same reply sent unpaced (`--chunk 1024 --pace-ms 0`): 579 bytes, no lost bytes
- [x] macOS `bc` honours `BC_LINE_LENGTH=0` (`PI500` = 502 bytes, no line breaks)
- [x] Error replies: unknown service, missing argument, bad argument (`CALCX`)
- [x] Command timeout: `CALC9**9**9`
- [x] `(NO REPLY - IS ZXSERVICE UP?)` with `zxservice` stopped
- [x] `zxservice` ignoring line noise when the ZX81 is switched off and on (logged as `ignored n bytes of line noise`, nothing run)
- [x] `zxservice` and `zxpong` exiting cleanly when the adapter is unplugged
- [x] The adapter with its jumper on 3.3 V works too (5 V is what the ZXpand+ manual specifies)
- [x] `zxservice` on Linux (Fedora, `/dev/ttyUSB0`): `TIME`, `JOKE`
- [x] `make`, `make zx` (Homebrew z80asm 1.8, byte-identical output) and `make test` (Python 3.14 venv) on macOS

## Open questions

- [ ] What `GET SER` actually does: byte count, where the data goes, how it ends
- [ ] The ZXpand+ receive buffer size. 579 bytes sent unpaced arrive intact, but the ZX81
      reads while they arrive, so this is a lower bound on what works, not the size
- [ ] The garbled `X` at the end of `LOAD "$"` (`98 FF` / `D8 FF` instead of `58`):
      what is actually on the TX pin? A logic analyser would show it.
