# Hardware testing

What has been checked on a real ZX81 + ZXpand+ (issue 5.1 board, TOMTE
firmware), from macOS (Apple silicon) with an FT232RL adapter, sound through a
stereo soundbar. `zxservice` has also run from Fedora on a ThinkPad, and
`zxsvr` from a Raspberry Pi and a Linux laptop. The Linux checklist for
`zxmusic` is [below](#thinkpad-linux-checklist).

## Not yet tested on hardware

Changed after the results below were gathered. Tested in the Z80 simulator
and over a pseudo-terminal only (`make test`).

- [ ] **MUSIC spinner** (next to `ANY KEY TO STOP`). Tested against a stub of the ROM's `PRINT AT` only. MUSIC asks `PRINT AT` once, on entry, for the display-file address of the spinner's cell and then writes the glyphs there directly. Check: the block fills and empties cleanly in the right place, the lines BASIC prints after the tune (`STOPPED`, `FRAMES`, ...) still start in the right place and the spinner is gone, and LATE stays 0
- [ ] **MUSIC resync** after a lost or added byte, and the per-byte credits it uses. The clean-stream path is the same code as before, so `COMMANDO` should still show BAD 0, UNDERRUNS 0, LATE 0. A hunt reads up to 15 bytes a tick with a little more work per byte than a normal frame: check that it doesn't show up as LATE

## Confirmed

### Serial, `LOAD "$"`, SERVICE

- [x] `LOAD "$"` via `zxsvr`, including loop mode (`-l`)
- [x] The garbled sign-off is detected and shown bit by bit
- [x] Ping/pong both ways, from machine code (`RUN`) and BASIC `PUT SER` (`RUN 400`)
- [x] SERVICE keywords: `TIME`, `DATE`, `FORTUNE`, `WEATHER`, `CAL`, `JOKE`, `UNAME`, `UPTIME`, `SUN`, `PING`
- [x] Arguments: `CALC1/3`, `CALC9**13`, `PI500`, `FACT50`, `FACTOR360`
- [x] Spaces survive `PUT SER` (`PI 50` at SERVICE's prompt) but not `LOAD "SERVICE:PI 50"`, which arrives as `PI`
- [x] `GET PARM` returns at most 27 characters: a 30-character argument arrived as 27 good ones and two junk bytes
- [x] A 27-character keyword works end to end: `LOAD "MUSIC:QWERTYUIOPASDFGHJKLZXCVBNM1"` (from `music.conf`) plays Scout
- [x] A longer keyword (30 characters) gets `KEYWORD TOO LONG (MAX 27)` and then the music browser, no junk keyword
- [x] The server's credit timeout works: `zxmusic.py` logged `no credits for 3 s` when the Mac went to sleep mid-tune, and carried on (the music browser worked afterwards)
- [x] `GET PARM` returns ZX81 codes
- [x] A full-screen reply (`PI700`, ~580 bytes) arrives intact, paced or not
- [x] Error replies, command timeout (`CALC9**9**9`), `(NO REPLY - IS ZXSERVICE UP?)`
- [x] `zxservice` ignores line noise when the ZX81 is switched off and on, and exits cleanly when the adapter is unplugged
- [x] Adapter jumper at 3.3 V works as well as 5 V
- [x] `zxservice` on Linux (Fedora, `/dev/ttyUSB0`): `TIME`, `JOKE`
- [x] **SERVICE credits** (`SERVICE.P` sends `k` per 64 bytes, `zxservice` stays within 192 bytes of them): `PI500` (502 bytes), `PI700` (579), `JOKE`, `FORTUNE`, all whole. `PI700` also with `--chunk 1024 --pace-ms 0`: whole. Both big replies are past the 194 bytes the server sends before its first credit, so the credits do get back to it

### The AY and its clock

- [x] `AYTONE`: tones from machine code (periods 63, 110, 252, 504), period read back from `$CF` matches
- [x] **The AY runs at the ZX81's CPU clock / 2.** Clock test (`RUN 300`, period 252), 10 beeps recorded with a Mac's microphone: beeps every 2.0234 s give a CPU clock of 3.2126 MHz, the tone (398.399 ± 0.003 Hz) an AY clock of 1.6063 MHz; ratio 0.50001. This ZX81's resonator is 1.15% slow, so the clock differs between machines
- [x] The chip (marked AY-3-8910 / 0827 CDA / Microchip, possibly relabelled) reads R1 as 15 and R6 as 31: AY-3-8910 behaviour

### Serial limits (`SERTEST`)

- [x] Receive buffer: 64, 128 and 255 unread bytes are all kept and `$C5` reports them exactly; the ZX81 drains them at ~1,800 bytes/s
- [x] **256 unread bytes hard-freeze the ZXpand+** (power cycle). Happened twice: 256 bytes sent to a ZX81 that wasn't reading, and a ZX81 that had dropped back to BASIC while `zxsertest` kept streaming
- [x] Without credits, reading while bytes arrive lost and duplicated bytes (`$C5` peaked at 190 at 1,000 B/s). With credits it didn't happen again
- [x] Full run with credits: 4096 bytes at 1,000 and 1,450 B/s and flat out, `batch` and `ask` modes: nothing lost or extra, one changed byte in 4096 at most. The ZX81 keeps up at ~1,720 B/s (`batch`) and ~1,095 B/s (`ask`); longest gap between credits 85 ms
- [x] Changed bytes: 2 in ~30,000, each a single byte with nothing lost or added around it

### MUSIC

On the Mac, `--ay-clock 1606345`, by ear against chiptune.app.

- [x] Spectrum `.ay`: `COMMANDO` (6,646 frames until stopped), `FOLLIN` (8,175 frames, to the end), `BIONIC1`, `HARRIER`. On the first two, 0 bad frames, 0 underruns, 0 late ticks
- [x] Amstrad CPC `.ay` (1 MHz AY): `DARKAGE`, `RUBBER`. Same pitch and tempo as chiptune.app
- [x] Atari ST `.ym` (YM2, YM5, YM6, LHA-compressed): `ANCOOL`, `SCOUT`, `ENCHANT`, `JESS1`, `JESS2`, `ESCAPE1`
- [x] `.pt3`: `SYNC`, `ESPRESSO`, and `SURFING` (TurboSound, module 1 of 2). `SYNC` also played in full on the current build (25,088 frames, 8 min 21 s, the longest run so far) with the new credits, the resync code and the spinner
- [x] Sampled drums (`WOD1`, `WOD2`, Atari ST VGM): the AY drums are close to the originals. Muted without them. `held` is the default
- [x] MSX `.vgz` with an SCC (`JUNGLE`, `NEMESIS`): one SCC voice on the free AY channel works; folding the loudest voice per frame didn't
- [x] `MUSIC:TEST`: a tuner shows 440 Hz, so the measured clock and the rescaling are right
- [x] Chip detection: "AY-3-8910" shown at the prompt and while playing
- [x] Music browser: folders, a multi-song file's list, paging, going back, playing from both levels and back to the list; a session of browsing and playing with one damaged page, asked for again and received, and no freeze
- [x] More Spectrum `.ay` (2026-10-13, Mac): `ASTROMAR/1`, `AGENTX2/3` (its play routine starts with `HALT HALT`: needs the frame interrupt), `AUFWIEDE/1`, `/13`, `/15`, `ROBOCOP/1`; and on Linux `KWIKSNAX/2` (its `INIT` is an `EI`/`HALT` main loop, run free with the interrupt delivered). Played by ear; not checked against a reference

### Build

- [x] `make`, `make zx` (Homebrew z80asm 1.8, byte-identical output) and `make test` (Python 3.14 venv) on macOS

## ThinkPad (Linux) checklist

`zxservice` and `zxsvr` have run on Linux for a while; `zxmusic.py`, MUSIC and the
music browser were first run on the ThinkPad (Fedora, Python 3.14) on 2026-10-12: what is
ticked below has been seen. Note anything odd in the table at the end.

**Machine and cable**

- [x] `ls -l /dev/ttyUSB*` shows the adapter (`root:dialout`), and you are in `dialout`. `lsusb`: an FTDI FT232 (`0403:6001`)
- [x] ModemManager (running here: it holds a sleep lock) doesn't get in the way: a full 106 s tune streamed clean with it running
- [ ] The quick check in [wiring.md](wiring.md#host-side) shows `48 45 4c 4c 4f`

**Build and tests**

- [x] `make` builds `zxsvr` without warnings (Fedora, `cc`)
- [x] `pip install z80` needs a C++ compiler on Linux (no wheel for Python 3.14 here): it failed with `No such file or directory: 'g++'` until `gcc-c++` was installed
- [x] `python3 -m venv .venv && .venv/bin/pip install z80` (after `gcc-c++`), then `make test`: all 59 `ok`, the same as the Mac. Python 3.14
- [ ] Optional: `make zx` with a `z80asm` leaves `git status` clean

**SERVICE** (regression)

- [ ] `zxsvr service/SERVICE.P`, `LOAD "$"`, `GOTO 35`; `zxservice.py` on Linux (2026-10-13): `TIME` and `JOKE` work. `PI500` (502 bytes) works with credits from the copy in memory straight after `GOTO 30`, but `LOAD "SERVICE:PI500"` after a reset still gives `(SHORT 192/502)`: `GOTO 30` doesn't replace the `SERVICE.P` on the SD card (no error shown: the delete is refused without a message, then `SAVE` doesn't overwrite), so the old copy, which doesn't send credits, keeps loading. Not yet resolved; `FORTUNE` wasn't tried

**zxmusic.py**

- [x] `--setup` lists `/dev/ttyUSB0` and saves `zxmusic.ini` (it listed three `/dev/ttyACM*` ports first, the laptop's modem, and offered one as the default)
- [ ] With one adapter and no `zxmusic.ini`, it finds the port
- [ ] Plain `python3 zxmusic.py` switches to `.venv`
- [ ] `~/.cache/zedwire/library.json` appears
- [x] `MUSIC:TEST` from the ThinkPad plays its beep and then the scale, in tune: the FFT in `ayclock.py` measured the A at **440.39 Hz** from a recording (the nearest the AY can play with the 1607431 Hz clock is period 228 = 440.63 Hz, so within 0.06%). A web pitch detector said 442, which was the detector's error, not the ZX81's. The Mac's tuner said 440
- [x] A full-length `.ay` tune from the ThinkPad: Space Harrier II song 1 (`SPACEHA2/1`), 5,307 frames in 106.0 s (50.07 frames a second, like the Mac), streamed to the end, "nothing bad" on the ZX81
- [x] `MUSIC:COMMANDO` from the ThinkPad, to the end (12,650 frames, 252.6 s): `THE END`, BAD 0, UNDERRUNS 0, LATE 0, the same as the Mac. The FTDI `latency_timer` is 16 ms (the default), and it did not matter
- [x] A CPC `.ay` (`DARKAGE2`, 1 MHz AY) played for 68 s, stopped with a key, and the music browser came back fine
- [x] One each of `.ym` (`ENCHANT1`), `.pt3` (`SYNCHRON`) and `.vgz` (`OPENINGT`) streamed from the ThinkPad and stopped with a key; no damaged pages in the log
- [x] Music browser on Linux: draws, opens a multi-song file, picks a song
- [x] Music browser, the rest (2026-10-13, 48 tunes, 3 pages): `6`/`7`, paging both ways, `5`/`0`, `Q`, back to the list after a tune; Space does nothing. A full page of 18 rows was first taken for a tune pick (the play marker was `$12`, which is 18): fixed, the marker is `$FF`. The `asked again` count wasn't kept
- [x] `systemd-inhibit --what=idle:sleep --who=zxmusic --why=... .venv/bin/python zxmusic.py`: the lock shows in `systemd-inhibit --list` and goes with the server. From an SSH session polkit asks for the password first
- [ ] The ThinkPad stays awake past its idle timeout while a tune streams
- [ ] A file added while the server runs shows up
- [x] Unplugging the adapter mid-tune: `Serial port went away`, clean exit, on the Mac and on Linux. The first try on Linux spun at 100% CPU and never exited, because a hung-up Linux port reads as empty bytes and the streaming and reply loops didn't treat that as the port closing (fixed in `zxmusic.py`, `zxservice.py`, `zxpong.py`)
- [x] `tools/tunecheck.py` on the tunes folder: `14 complete, 4 no AY, 11 partial`, the same as the Mac, so the `.ay`, `.ym`, `.pt3` and `.vgz` readers agree

**Calibration**

- [x] `ayclock.py` on Linux (numpy from pip, a WAV from `arecord -f S16_LE -r 44100 -c 1 -d 30`, the laptop's built-in microphone, a soundbar next to the laptop): tone 354x louder than the background, AY clock **1.6074 MHz** (the Mac measured 1.6063 MHz for the same ZX81, 0.07% apart), beeps every 2.02123 s (scatter 7.1 ms), CPU clock 3.2161 MHz, AY / CPU 0.49981: the AY runs off the ZX81 clock / 2, confirmed on a second machine. A first recording with the soundbar further away (tone 24x, scatter 83.5 ms) gave a good AY clock (1.6077 MHz) but useless beep timing; `ayclock.py` now says "not judged" when the spacing is too uncertain (the updated script ran on the good recording and judged it normally; the "not judged" message itself has only been checked by arithmetic)
- [x] `zxmusic.py --setup` accepts the WAV and saves the clock (1607431 Hz)

| Results | |
|---|---|
| ThinkPad, distro, Python | ThinkPad X240, Fedora, Python 3.14 |
| Adapter, jumper, `latency_timer` | FTDI FT232, `latency_timer` 16 |
| ModemManager | running; no trouble |
| `make test` | 59 ok |
| `COMMANDO`: frames, bad, underruns, late | 12,650, 0, 0, 0 (and `SPACEHA2/1`: 5,307 frames, nothing bad) |
| Music browser: `asked again` count | |
| Needed changes | `gcc-c++` for the `z80` package (docs); `ayclock.py` output |

## Open questions

- [ ] The stereo mix: which AY channels go left, right or both?
- [ ] Why the ZXpand+ once handed over stale buffer contents mid-page (bytes from 256 earlier, then one byte repeated). Checksums catch it
- [ ] Both damaged music browser pages seen were the first page after stopping a tune
- [ ] **ZXpand+ freezes** (2026-10-09, two in 12 minutes, after the resync, spinner and credit changes; none in the hours before). Both were in a `Burnin' Rubber` (CPC, multi-song) session, in the music browser a few seconds after stopping a tune with a key. 1: `RUBBER` stopped at 122 s, a key to go back to its song list: blank screen, the ZXpand+ dead until the power was cut for a few seconds. 2: `RUBBER` song 2 stopped at 7 s, the song list displayed, then the back key (`?0`, a reply of about 680 bytes): the screen froze with its text visible, and the ZXpand+'s own reset did nothing until the power was cut. The same steps worked many times before and after, so not reproducible on demand yet. Comparison builds are in `ab/` (not committed). 3: 2026-10-13 18:45, Mac server: opening the `ROBOCOP` song list (`?07` from page 2/2 of the root list, a 390-byte reply). Not after stopping a tune: the last one had ended on its own about 90 s earlier, and the requests before it (`?0`, `?N`) were fine. The ZXpand+ froze; the server logged `ignored 28 bytes of line noise` when it was powered up again. The same request worked at 18:46:17 and 18:47:03, so the page content isn't the cause. 4: 2026-10-13, ThinkPad server, an hour or two later: opening the `KWIKSNAX` song list (10 songs), again not after stopping a tune. Of the four, the last two were both opening a multi-song file's list. Since then the server's browser replies go out 8 bytes at a time, 128 ahead of the credits (was 16 and 192), the rate of the tune stream, which has never frozen it; not yet tested for long enough to say
- [ ] What `GET SER` does: byte count, where the data goes, how it ends
- [ ] What causes the changed bytes
- [ ] A finer ZX81 read rate than "about 1,800 bytes/s" (frame counts are coarse)
- [ ] The garbled `X` at the end of `LOAD "$"` (`98 FF` / `D8 FF` for `58`): only the last bits are wrong, and an `FF` often follows
