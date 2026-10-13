# Chiptunes: design notes

Why MUSIC is built the way it is. To use it, see [music.md](music.md); for the
byte-level protocol, [zxpand-serial.md](zxpand-serial.md); for what was tested
on hardware, [testing.md](testing.md).

## Streaming register frames

The ZXpand+ has an AY on ports `$CF` (register) and `$0F` (data). Playing a
tune means writing its 14 registers 50 times a second. Two ways to get
them there:

- **Play on the ZX81**: needs a tracker player in RAM per format, and register
  dumps don't fit (one 3,720-byte ASC module became a 129,028-byte PSG dump).
- **Stream from the computer**: the ZX81 only copies a frame to the chip.
  Any format the computer can decode works, with no length limit, at the
  price of needing the computer.

Streaming it is. The numbers behind that:

| | |
|---|---|
| Serial link, 38400 8N1 | 3,840 bytes/s |
| MUSIC stream, 15 bytes × 50 | 750 bytes/s |
| ZX81 read rate in `SLOW` mode | ~1,720 bytes/s asking `$C5` once per batch, ~1,095 per byte |
| ZXpand+ receive buffer | 255 bytes; **256 unread bytes freeze it** |
| Bytes arriving changed | about 1 in 15,000 |

## The stream

- **Fixed 15-byte frames**: R0-R12, R13, checksum. Each frame carries the
  whole register set, so a frame dropped for a bad checksum costs nothing the
  next frame doesn't repair, and a changed byte can't desynchronise anything.
  A lost or added byte can: two bad frames in a row make the ZX81 slide over
  the stream a byte at a time until 15 bytes add up to 0 and R13 is valid.
  R13 `$FF` = don't write (writing it restarts the envelope), `$FE` = end.
- **Credits**: the ZX81 sends `k` per 60 bytes (4 frames) taken, however they
  were taken; at most 10 frames in flight. Required, because of the freeze
  above. Browser and SERVICE replies have them as well.
- **Pitch**: the ZXpand+ AY runs at the ZX81 clock / 2 (measured: ratio
  0.50001, 1.6063 MHz on a ZX81 that is 1.15% slow). Periods are rescaled from
  the tune's clock to the configured AY clock.

## Formats

- **`.ay`**: the file holds the original player and data. `zxmusic.py` runs it
  in the `z80` package's emulator (INIT once, play routine once per frame) and
  catches writes to `$FFFD`/`$BFFD`. Amstrad CPC files reach the AY through the
  8255 PPI (value on `$F4xx`, then a control write to `$F6xx`: `11` select,
  `10` write); the first such write switches the emulator to CPC ports and a
  1 MHz clock. A song with no length stops 2 s after its last sound.
- **`.pt3`**: Bulba's player (assembled at `$C000`) in the same emulator, once
  per frame until the module ends.
- **`.ym`**: YM2/3/3b/5/6; a pure-Python LH5 decoder (lh4-lh7) handles the LHA
  compression (verified byte for byte against lhasa on Leonard's sample files).
- **`.vgm`/`.vgz`**: AY writes (command A0), cut into 50 Hz frames.
- **`.psg`**: read as ZXTune does (`FF` next frame, `FE n` = 4×n frames, `FD` end).
- Anything else goes through `zxtune123`.

R7 is masked to `$3F` for every format: MSX tunes set bit 7 (I/O port B).

### Konami SCC

An SCC voice of period P sounds like AY period P+1 at the same clock; SCC
volume (linear) maps to AY volume as 15 + 2·log2(v/15). Voices are folded onto
AY channels silent in at least 90% of frames: the highest-pitched voice that
plays at least 25% of the time, chosen once for the whole tune. Choosing per
frame hopped between chord notes.

### Sampled drums

Atari ST tunes play drum samples by writing a volume register 20-70 times per
frame, which a 50 Hz stream can't carry. A hit is a run of 4-7 such frames.
Kick and snare are told apart by writes per frame (a noisy snare writes more
than a kick's slow sweep), split at the biggest gap; with no clear gap, by
how jumpy the values are.

The default `held` drums copy Jedi's *Virtual Escape* (plain AY, bass channel,
4 frames): kick 207, 155, 116, 87 Hz (volume 12, 12, 11, 11, noise on frames
2-4); snare a frame of noise then 138, 123, 110 Hz (volume 11, 12, 11, 10).
`env` decays on the hardware envelope instead.

## The music browser

- The ZX81 draws through the ROM (`CLS` `$0A2A`, `PRINT AT` `$08F5`,
  `RST $10`). Writing the display file directly assumes it is fully expanded,
  and garbled the screen and memory when it wasn't. `PRINT AT` reaches lines
  0-21 only: 18 rows, footer on lines 20-21, column 31 of line 21 unused.
- Replies end in a checksum and are checked before drawing (row count, length,
  selected row): the ZXpand+ has handed over stale buffer contents once in the
  middle of a page.
- Replies have credits, like the stream (and SERVICE's, `k` per 64 bytes).
- Keys, not typed numbers: numbers can't be typed at an `INPUT` prompt there.
- `PRINT AT` (`$08F5`) is slow: its `LOC_ADDR` finds the line by walking the
  display file backward from `VARS`, counting newlines, on every call. Fine for
  drawing a page. The spinner asks once, on entry, for the address of its cell
  (`DF_CC` afterwards), puts BASIC's `DF_CC` and `S_POSN` back and then pokes the
  glyphs in. That needs the cell to exist: `music.bas` pads the line, because
  `PRINT AT` on a short line makes room for it and moves everything after.
- With FLAGS bit 1 set (after `ZXPAND`, which is the `LPRINT` token), `PRINT AT`
  and `RST $10` go to the printer buffer, not the screen: hence `res 1,(iy+1)`.
- `tools/mkp.py` refuses a `$76` byte in the REM code; shift it with a `nop`.

## Sources

- [ZX81 ROM disassembly](https://github.com/proteanthread/ZX-81-ROMS/blob/master/zx81.asm): `PRINT_AT`, `LOC_ADDR`, `PRINT_SP`, `CLS`, the system variables
- [ZXpand-Vitamins](https://github.com/charlierobson/ZXpand-Vitamins): `sndtest`, `midi-play`, `serial-breakout`, `turbo-pcb`; the
  [ZXpand online manual](https://github.com/charlierobson/ZXpand-Vitamins/wiki/ZXpand---Online-Manual)
- ZXTune (`zxtune123`): `.psg` parsing (`psg.cpp`), CPC port handling (`ayemul.cpp`)
- [StSound YmSampleFiles](https://github.com/arnaud-carre/StSound/tree/main/YmSampleFiles) (Leonard): YM test files
- [MAME ay8910.cpp](https://github.com/mamedev/mame/blob/master/src/devices/sound/ay8910.cpp): AY vs YM register read-back
- [Qiqitori](https://blog.qiqitori.com/2023/05/playing-psg-tunes-or-even-asc-tunes-on-the-msx/): `zxtune123` PSG conversion, dump sizes
