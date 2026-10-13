# Vortex Tracker II PT3 player (S.V. Bulba)

`zxmusic.py` plays Pro Tracker 3 / Vortex Tracker II modules (`.pt3`) by
running this Z80 player in an emulator, once per 50 Hz frame, and streaming
the AY register writes it makes.

- `pt3play-c000.bin`: the player, assembled at `$C000` (2,158 bytes).
  Entry points: `$C003` init with the module address in HL, `$C005` play
  one frame, `$C008` mute; `$C00A` is the SETUP byte (bit 0: don't loop,
  bit 7: the end was reached).
- `VTII10bG.asm`: its source ("Vortex Tracker II v1.0 PT3 player for ZX
  Spectrum", release 7, SjASM syntax).
- `ZXAYPlayer.txt`: the author's notes.

Copyright (c) 2004, 2007 Sergey Bulba (S.V. Bulba, <vorobey@mail.khstu.ru>,
http://bulba.untergrund.net/). From the Vortex Tracker II sources, as
mirrored at https://github.com/oisee/vti (`ZXAYHOBETA/`, commit fd5f975),
whose readme.txt says:

> Vortex Tracker II is free program. [...] You can use and distribute
> sources freely, simply credit me somewhere in your projects, where you
> include all or part of the sources and (or) my algorithms.
>
> Sergey Bulba

These files are not covered by zedwire's MIT licence; they are used and
redistributed under the terms above, with thanks.
