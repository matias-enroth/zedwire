; MUSIC - plays AY music streamed over serial by zxmusic.py. Lives in the
; line-10 REM at 16514.
;
; Stream (server -> ZX81): fixed 15-byte frames, one per 50 Hz frame:
;   R0..R12, R13, checksum
;   R13 = $FF: don't write R13 (writing it restarts the envelope)
;   R13 = $FE: end of tune
;   checksum: all 15 bytes add up to 0 (mod 256). A bad frame is dropped;
;   every frame carries the whole register set, so the next one repairs it.
;   Two bad frames in a row mean a byte was lost or added: the framing is
;   off, so MUSIC slides a window over the stream one byte at a time until
;   15 bytes add up to 0 and R13 is plausible (HUNT below), holding the sound.
; Credits (ZX81 -> server): a 'k' after every 60 bytes (4 frames) taken from
; the serial buffer, however they were taken; the server keeps at most 10
; frames (150 bytes) in flight.
; 256 unread bytes hard-freeze the ZXpand+, so this matters.
; When it stops, for whatever reason, the ZX81 sends 'q', silences the AY
; and throws away what's still arriving.
;
; USR 16514: play. Returns BC = why it stopped:
;   0 end of tune, 1 key pressed, 2 nothing arrived (is zxmusic up?),
;   3 no frame found in HUNTMAX bytes, 4 the stream stopped mid-tune
; USR 16517: which sound chip? BC = 0 none answered, 1 AY-3-8910,
;   2 YM2149, 3 something else. Writes R1 and reads it back: an AY reads
;   unused bits as 0, a YM2149 returns them as written (MAME ay8910.cpp,
;   "tested and confirmed on hardware"); nothing there reads as $FF.
; Statistics, 2 bytes each:
;   16520 frames played    16522 bad frames (dropped; a lost or added byte counts 2)
;   16524 underruns (no frame ready in time)
;   16526 late (frame ticks missed because the work took too long)
; USR 16528: the browser, in the style of ZXpand Commander: zxmusic sends
;   a page (?, ?N, ?P, ?0, ?T, ?<row>), drawn through the ROM (see below)
;   with the selected row inverted. 6/7 or the cursor keys move,
;   8/Enter opens a folder or plays, 5/0/. go back, Q quits.
;   Needs the serial port open (ZXPAND "OPE SER") and a CLS'd screen.
;   Returns BC = 0 quit, 1 a tune was picked (the reply at (16538) starts with $FF
;   and its keyword, (16536) bytes), 2 no reply (is zxmusic up?).
; USR 16531: receive one reply (2-byte length, then the bytes) into the
;   buffer at (16538); BC = bytes received, 0 = none. Used by the browser.
;   Sends 'k' after every 64 bytes; zxmusic keeps at most 128 in flight.
; A page: rows, selected row, pages, then 32-byte lines in ZX81 codes:
;   header (line 0), the rows (from line 2), two footer lines (20, 21).
;   Drawn through the ROM (CLS, PRINT AT, RST $10), which copes with the
;   display file in any state; writing it directly assumed a fully
;   expanded display and garbled the screen (and memory) on hardware.
;   With one page the cursor stops at the ends; with more, running off
;   the bottom or top asks for the next or previous page.
;
; While a tune plays, a spinner shows that frames are coming in: a block on
;   line SPINLN, column SPINCOL that fills a quarter at a time and empties
;   again, one step every SPINDIV good frames. A spinner that stands still
;   means no good frames: the sound is held.
;   The ROM's PRINT AT finds a place in the display file by walking it from
;   the end, which is far too slow to do every step. So it is asked once, on
;   entry, for the address of the spinner's cell (DF_CC afterwards), BASIC's
;   print position is put straight back, and the steps are plain memory writes.
;   That needs the cell to exist: music.bas prints "ANY KEY TO STOP  " on
;   line SPINLN, so the line is already 17 characters long. If it were
;   shorter, PRINT AT would make room for it and move everything after it.
;
; SLOW mode: FRAMES (16436) paces the tune, LAST_K (16421) sees keys.
; AY on $CF/$0F, serial as in docs/zxpand-serial.md.
; Touches A,BC,DE,HL only; IX, IY and AF' left alone.

FRAMES:  equ 16436
LAST_K:  equ 16421
DF_CC:   equ 16398               ; where BASIC prints next
S_POSN:  equ 16441               ; and the same as column and line
SPINLN:  equ 3                   ; the spinner's line
SPINCOL: equ 16                  ; and column
SPINDIV: equ 15                  ; good frames per spinner step (3 a second)
FLEN:    equ 15                  ; bytes per frame
CREDIT:  equ 4                   ; frames per 'k'
CREDB:   equ CREDIT*FLEN         ; bytes per 'k'
T_START: equ 5*50                ; frames to wait for the first frame
HUNTMAX: equ 200                 ; bytes to slide through before giving up
MAXIDLE: equ 2*50                ; frames without data before giving up

        org 16514
        jp play                 ; 16514
        jp chipid               ; 16517
played: dw 0                    ; 16520
bad:    dw 0                    ; 16522
under:  dw 0                    ; 16524
late:   dw 0                    ; 16526
        jp browse               ; 16528
        jp recv                 ; 16531
rlen:   dw 0                    ; 16534 declared reply length
count:  dw 0                    ; 16536 bytes received
bufp:   dw buf                  ; 16538 where the reply is

avail:  db 0                    ; bytes known to be waiting, not yet read
lastf:  db 0                    ; FRAMES low byte at the last tick
credb:  db 0                    ; bytes taken since the last 'k'
badrun: db 0
hunting:db 0                    ; 1 while sliding the window
hidx:   db 0                    ; the oldest byte of the window in fbuf (a ring)
hsum:   db 0                    ; the sum of the 15 bytes in the window
hcnt:   db 0                    ; bytes slid so far
htick:  db 0                    ; slides left this tick
lsum:   db 0                    ; the sum of the last frame read
spint:  db 0                    ; good frames since the last spinner step
spin:   db 0                    ; the spinner's step, 0-7
spaddr: dw 0                    ; the spinner's cell in the display file
idle:   dw 0
reason: db 0
fbuf:   ds FLEN,0
tbuf:   ds FLEN,0

play:   ld hl,0
        ld (played),hl
        ld (bad),hl
        ld (under),hl
        ld (late),hl
        ld (idle),hl
        xor a
        ld (avail),a
        ld (credb),a
        ld (badrun),a
        ld (hunting),a
        ld (spint),a
        ld (spin),a
        ld hl,(DF_CC)           ; BASIC's print position: PRINT AT moves it
        push hl
        ld hl,(S_POSN)
        push hl
        res 1,(iy+1)            ; FLAGS: print to the screen, not the printer
        ld bc,SPINLN*256+SPINCOL
        call PRAT               ; DF_CC is now the spinner's cell
        ld hl,(DF_CC)
        ld (spaddr),hl
        pop hl
        ld (S_POSN),hl
        pop hl
        ld (DF_CC),hl
        call silence
        ld b,50                 ; let go of the key that started us (1 s max)
rel:    call keyup
        jr z,relok
        push bc
        call tick
        pop bc
        djnz rel
relok:  ld a,(FRAMES)
        ld (lastf),a
        ld hl,T_START           ; wait for the first frame
first:  push hl
        call tick
        call ready
        pop hl
        jr nc,go
        call keyup
        ld a,1
        jp nz,stop
        dec hl
        ld a,h
        or l
        jr nz,first
        ld a,2
        jp stop
go:     ld hl,0                 ; count late ticks from the first frame on
        ld (late),hl
        jr loop1

loop:   call tick
        call keyup              ; any key: stop
        ld a,1
        jr nz,stop
        ld a,(hunting)
        or a
        jp nz,hunt
        call ready
        jr nc,loop1
nodata: ld hl,(under)           ; no frame ready: hold the sound
        inc hl
        ld (under),hl
        ld hl,(idle)
        inc hl
        ld (idle),hl
        ld de,MAXIDLE
        or a
        sbc hl,de
        jr c,loop
        ld a,4
        jr stop
loop1:  ld hl,0
        ld (idle),hl
        call getfr              ; read and check a frame
        ld a,FLEN
        call credit
        jr nz,badfr
good:   xor a
        ld (badrun),a
        ld a,(fbuf+13)
        cp $fe                  ; end of tune
        ld a,0
        jr z,stop
        call write
        ld hl,(played)
        inc hl
        ld (played),hl
        call spinner
        jr loop
badfr:  ld hl,(bad)
        inc hl
        ld (bad),hl
        ld a,(badrun)
        inc a
        ld (badrun),a
        cp 2                    ; one bad frame: a changed byte, drop it
        jr c,loop
        ld a,(lsum)             ; two in a row: the framing is off. Slide.
        ld (hsum),a
        xor a
        ld (hidx),a
        ld (hcnt),a
        inc a
        ld (hunting),a
        jp loop

; stop with reason A: silence, tell the server, drain what's in flight
stop:   ld (reason),a
        call silence
        ld a,'q'
        call sendb
drain:  ld b,25                 ; until 0.5 s without a byte
dr1:    push bc
        call tick
        call query
        pop bc
        or a
        jr nz,dr2
        djnz dr1
        call spinoff
        ld a,(reason)
        ld c,a
        ld b,0
        ret
dr2:    ld b,a                  ; read and drop A bytes
dr3:    push bc
        call rdb
        pop bc
        djnz dr3
        jr drain

; hunt: find the framing again. fbuf holds the last bad frame, now treated as
; a ring: hidx is its oldest byte, hsum the sum of all 15. Each new byte
; replaces the oldest; when the sum is 0 and R13 is $FE, $FF or 0-15 the
; ring is a frame, so put it in order and play it. At most FLEN slides a
; tick (the time a frame takes to read), HUNTMAX in all. The sound is held.
hunt:   ld a,(avail)
        or a
        jr nz,hu0
        call query
        ld (avail),a
        or a
        jp z,nodata
hu0:    ld hl,0
        ld (idle),hl
        ld a,FLEN
        ld (htick),a
hu1:    ld a,(avail)
        or a
        jr nz,hu2
        call query
        ld (avail),a
        or a
        jp z,loop               ; the rest hasn't arrived: next tick
hu2:    dec a
        ld (avail),a
        call rdb                ; A = the next byte
        ld c,a
        ld a,(hidx)
        ld e,a
        ld d,0
        ld hl,fbuf
        add hl,de               ; HL = the oldest byte
        ld a,(hsum)
        sub (hl)
        add a,c
        ld (hsum),a
        ld (hl),c               ; ... replaced by the new one
        ld a,e
        inc a
        cp FLEN
        jr c,hu3
        xor a
hu3:    ld (hidx),a
        ld a,1
        call credit
        ld a,(hsum)
        or a
        jr nz,hu4
        ld a,(hidx)             ; R13 is 13 bytes after the oldest
        add a,13
        cp FLEN
        jr c,hu5
        sub FLEN
hu5:    ld e,a
        ld d,0
        ld hl,fbuf
        add hl,de
        ld a,(hl)
        cp $fe                  ; $FE or $FF
        jr nc,hfound
        cp 16                   ; 0-15
        jr c,hfound
hu4:    ld a,(hcnt)
        inc a
        ld (hcnt),a
        cp HUNTMAX
        jr c,hu6
        ld a,3
        jp stop
hu6:    ld a,(htick)
        dec a
        ld (htick),a
        jr nz,hu1
        jp loop
hfound: ld a,(hidx)             ; the ring in order, the oldest byte first
        or a
        jr z,hf2
        ld e,a
        ld d,0
        ld hl,fbuf
        add hl,de               ; HL = the oldest
        ld a,FLEN
        sub e
        ld c,a
        ld b,0                  ; BC = from there to the end of the ring
        ld de,tbuf
        ldir
        ld a,(hidx)
        ld c,a
        ld b,0                  ; BC = from the start of the ring to there
        ld hl,fbuf
        ldir
        ld hl,tbuf
        ld de,fbuf
        ld bc,FLEN
        ldir
hf2:    xor a
        ld (hunting),a
        jp good

; spinner: called after every good frame; every SPINDIV of them, the next step
; of the block: a quarter, a half, three quarters, full, then emptied the
; same way round. Quarter blocks are codes 1 (top left), 2 (top right) and 4
; (bottom left); the inverse (+128) of a block is what it leaves out. The
; display file holds character codes as they are.
spinner:ld a,(spint)
        inc a
        cp SPINDIV
        jr c,sp1
        xor a
sp1:    ld (spint),a
        ret nz
        ld a,(spin)
        ld e,a
        inc a
        and 7
        ld (spin),a
        ld d,0
        ld hl,spglyph
        add hl,de
        ld a,(hl)
        jr spput

spglyph:db 1,3,132,128,129,131,4,0

; spinoff: an empty spinner
spinoff:xor a

; spput: put the character in A in the spinner's cell
spput:  ld hl,(spaddr)
        ld (hl),a
        ret

; tick: wait for the next frame; count missed ones in LATE
tick:   ld a,(lastf)
        ld b,a
tk1:    ld a,(FRAMES)
        cp b
        jr z,tk1
        ld (lastf),a
        ld c,a                  ; FRAMES counts down: elapsed = old - new
        ld a,b
        sub c
        dec a
        ret z
        ld hl,(late)
        ld e,a
        ld d,0
        add hl,de
        ld (late),hl
        ret

; keyup: Z if no key is down (LAST_K = $FFFF). Keeps HL.
keyup:  push hl
        ld hl,(LAST_K)
        inc hl
        ld a,h
        or l
        pop hl
        ret

; ready: carry clear if a whole frame is waiting
ready:  ld a,(avail)
        cp FLEN
        ret nc
        call query
        ld (avail),a
        cp FLEN
        ret

; getfr: read FLEN bytes into fbuf. Z if the checksum is right.
getfr:  ld hl,fbuf
        ld b,FLEN
        ld e,0
gf1:    push bc
        push hl
        push de
        call rdb
        pop de
        pop hl
        pop bc
        ld (hl),a
        inc hl
        add a,e
        ld e,a
        djnz gf1
        ld a,(avail)
        sub FLEN
        ld (avail),a
        ld a,e
        ld (lsum),a
        or a
        ret

; credit: A bytes taken from the serial buffer; a 'k' for every CREDB.
; Keeps the flags (and A).
credit: push af
        push bc
        ld b,a
        ld a,(credb)
        add a,b
cr1:    cp CREDB
        jr c,cr2
        sub CREDB
        push af
        ld a,'k'
        call sendb
        pop af
        jr cr1
cr2:    ld (credb),a
        pop bc
        pop af
        ret

; write: R0-R12 from fbuf, then R13 unless it's $FF
write:  ld hl,fbuf
        xor a
wr1:    out ($cf),a
        ld c,a
        ld a,(hl)
        out ($0f),a
        inc hl
        ld a,c
        inc a
        cp 13
        jr c,wr1
        ld a,(hl)
        cp $ff
        ret z
        ld c,a
        ld a,13
        out ($cf),a
        ld a,c
        out ($0f),a
        ret

; chipid: see the header. Restores R1.
chipid: ld a,1
        out ($cf),a
        in a,($cf)
        ld d,a                  ; R1 as it was
        ld a,1
        ld c,0
        call ayw                ; R1 = 0 ...
        ld a,1
        out ($cf),a
        in a,($cf)
        ld e,a                  ; ... reads back as 0 unless nothing is there
        ld a,1
        ld c,$ff
        call ayw                ; R1 = $FF ...
        ld a,1
        out ($cf),a
        in a,($cf)
        ld b,a                  ; ... reads back $0F (AY) or $FF (YM)
        ld a,1
        ld c,d
        call ayw
        ld c,0                  ; none
        ld a,e
        or a
        jr nz,cid
        inc c                   ; AY
        ld a,b
        cp $0f
        jr z,cid
        inc c                   ; YM
        cp $ff
        jr z,cid
        inc c                   ; something else
cid:    ld b,0
        ret

; silence: volumes off, mixer all off
silence:
        ld a,8
        ld c,0
        call ayw
        ld a,9
        call ayw
        ld a,10
        call ayw
        ld a,7
        ld c,$ff
ayw:    out ($cf),a
        ld a,c
        out ($0f),a
        ret

; query: A = bytes waiting ($C5)
query:  ld bc,$e007
        ld a,$c5
        out (c),a
        call wcc
        in a,(c)
        ret

; rdb: A = next byte ($C7)
rdb:    ld bc,$e007
        ld a,$c7
        out (c),a
        call wcc
        in a,(c)
        ret

; send the byte in A
sendb:  ld d,a
        ld bc,$0007
        ld a,c
        out (c),a
        call wcc
        ld a,d
        ld bc,$4007
        out (c),a
        call wcc
        ld bc,$e007
        ld a,$c6
        out (c),a
wcc:    in a,($17)
        and $80
        jr nz,wcc
        ret

; ---- browser replies -------------------------------------------------

BUFSZ:   equ 740                 ; a page: 3 + 22 lines of 32
T_REPLY: equ 5*50                ; frames to wait for a reply to start
T_NEXT:  equ 1*50                ; frames to wait between bytes

tav:    db 0                    ; bytes known to be waiting (browser)
t0:     dw 0
tlim:   dw 0

recv:   xor a
        ld (tav),a
        ld hl,0
        ld (count),hl
        ld (rlen),hl
        ld hl,T_REPLY
        ld (tlim),hl
        call getb
        jr c,rdone
        ld (rlen),a
        ld hl,T_NEXT
        ld (tlim),hl
        call getb
        jr c,rdone
        ld (rlen+1),a
        ld hl,(rlen)            ; clamp to the buffer
        ld de,BUFSZ
        or a
        sbc hl,de
        jr c,lenok
        ld (rlen),de
lenok:  ld hl,buf
        ld de,0
rloop:  push hl
        ld hl,(rlen)
        or a
        sbc hl,de
        pop hl
        jr z,rstore
        call getb
        jr c,rstore
        ld (hl),a
        inc hl
        inc de
        ld a,e                  ; a credit for every 64 bytes: the server
        and 63                  ; never has more than 128 unread (256
        jr nz,rloop             ; freeze the ZXpand)
        push hl
        push de
        ld a,'k'
        call sendb
        pop de
        pop hl
        jr rloop
rstore: ld (count),de
rdone:  ld bc,(count)
        ret

; getb: next byte in A, carry clear; carry set after tlim frames without
; one. Keeps HL, DE.
getb:   push hl
        push de
        ld a,(tav)
        or a
        jr nz,gb2
        ld hl,(FRAMES)
        ld (t0),hl
gb1:    call query
        or a
        jr nz,gb3
        ld hl,(t0)              ; FRAMES counts down: elapsed = t0 - now
        ld de,(FRAMES)
        or a
        sbc hl,de
        ld a,h
        and $7f
        ld h,a
        ld de,(tlim)
        or a
        sbc hl,de
        jr c,gb1
        pop de
        pop hl
        scf
        ret
gb3:    ld (tav),a
gb2:    ld a,(tav)
        dec a
        ld (tav),a
        call rdb
        pop de
        pop hl
        or a
        ret

; ---- the browser ------------------------------------------------

DECODE:  equ $07BD               ; ROM: key value in BC -> HL at its character
CLS:     equ $0A2A               ; ROM: clear the screen
PRAT:    equ $08F5               ; ROM: PRINT AT line B, column C
ROWS:    equ 18                  ; rows on a page, lines 2-19
K_UP:    equ $70                 ; shift 7
K_DOWN:  equ $71                 ; shift 6
K_LEFT:  equ $72                 ; shift 5
K_RIGHT: equ $73                 ; shift 8
K_ENTER: equ $76

nrows:  db 0
rowsp:  dw 0                    ; the first row in the buffer
inv:    db 0                    ; $80 while printing the selected row
pline:  db 0
npages: db 0
chip:   db 0
tries:  db 0
sel:    db 0
held:   db 0
rpt:    db 0
        db 0                    ; keeps rq_next off $4476 (a $76 in the REM)
rq_cur: db "?",0
rq_next:db "?N",0
rq_prev:db "?P",0
        db 0                    ; keeps rq_back off $4376 (a $76 in the REM)
rq_back:db "?0",0
rq_row: db "?00",0

        nop                     ; keeps $76 out of the addresses below
browse: call waitup
        call chipid             ; for the corner of the screen
        ld a,c
        ld (chip),a
        xor a
        ld (tries),a
        ld hl,rq_cur
breq:   call request
        jr nz,brep
bnone:  ld bc,2                 ; no reply
        ret
brep:   call csum               ; all the bytes must add up to 0
        jp nz,bbad
        ld hl,(bufp)
        ld a,(hl)
        cp $FF                  ; a tune was picked (a page starts with its row count, 0-18)
        jr nz,bpage
        ld bc,1
        ret
; a page must add up: at most ROWS rows, 2 + 32 * (rows + 3) bytes, the
; selection on a row. Anything else (a stray or changed byte) would draw
; over memory, so throw it away and ask again, three times at most.
bpage:  cp ROWS+1
        jp nc,bbad
        push hl
        ld l,a
        ld h,0
        ld de,3
        add hl,de
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        inc hl
        inc hl
        inc hl
        ld de,(count)           ; (without the checksum byte)
        or a
        sbc hl,de
        pop hl
        jp nz,bbad
        ld b,a                  ; rows
        inc hl
        ld a,(hl)               ; selected row
        or a
        jr z,bsel
        cp b
        jp nc,bbad
bsel:   ld (sel),a
        ld a,b
        ld (nrows),a
        inc hl
        ld a,(hl)               ; pages
        ld (npages),a
        xor a
        ld (tries),a
        inc hl
        call draw
        call hilite
bkeys:  call getkey
        cp K_DOWN
        jp z,bdown
        cp $22                  ; 6
        jp z,bdown
        cp K_UP
        jp z,bup
        cp $23                  ; 7
        jp z,bup
        cp K_RIGHT
        jp z,bopen
        cp $24                  ; 8
        jp z,bopen
        dec a                   ; Enter, without a literal $76 in the REM
        cp K_ENTER-1
        jp z,bopen
        inc a
        cp K_LEFT
        jp z,bback
        cp $21                  ; 5
        jp z,bback
        cp $1c                  ; 0
        jp z,bback
        cp $1b                  ; .
        jp z,bback
        cp $36                  ; Q
        jp nz,bkeys
bquit:  ld bc,0
        ret
        nop                     ; four nops keep $76 out of the addresses below
        nop
        nop
        nop
bdown:  ld a,(nrows)
        ld b,a
        ld a,(sel)
        inc a
        cp b
        jr nc,bnext
        jr bmove
bnext:  ld a,(npages)           ; one page: stop at the bottom
        cp 2
        jp c,bkeys
        ld hl,rq_next
        jp breq
bup:    ld a,(sel)
        or a
        jr z,bprev
        dec a
bmove:  push af
        call lolite
        pop af
        ld (sel),a
        call hilite
        jp bkeys
bprev:  ld a,(npages)           ; one page: stop at the top
        cp 2
        jp c,bkeys
        ld hl,rq_prev
        jp breq
bback:  ld hl,rq_back
        jp breq
bbad:   ld a,(tries)
        inc a
        ld (tries),a
        cp 4
        jp nc,bnone
        call flush
        ld hl,rq_cur
        jp breq
bopen:  ld a,(nrows)
        or a
        jp z,bkeys
        ld a,(sel)
        inc a                   ; rows are 1-19 on the wire
        ld c,$30                ; tens, ASCII
bo1:    cp 10
        jr c,bo2
        sub 10
        inc c
        jr bo1
bo2:    add a,$30
        ld (rq_row+2),a
        ld a,c
        ld (rq_row+1),a
        ld hl,rq_row
        jp breq

; csum: Z if the reply's bytes add up to 0 (mod 256); then drops the
; checksum byte from (count). The ZXpand has been seen to hand over stale
; buffer contents in the middle of a page; this catches it.
csum:   ld hl,(bufp)
        ld de,(count)
        xor a
cs1:    ld b,a
        ld a,d
        or e
        ld a,b
        jr z,cs2
        add a,(hl)
        inc hl
        dec de
        jr cs1
cs2:    or a
        ret nz
        ld hl,(count)
        dec hl
        ld (count),hl
        xor a
        ret

; request: send the 0-terminated ASCII at HL and a CR, then receive the
; reply. NZ if there is one. Stray bytes still waiting are dropped first.
request:
        push hl
        call query
        or a
        call nz,flush
        pop hl
rqs:    ld a,(hl)
        or a
        jr z,rq2
        push hl
        call sendb
        pop hl
        inc hl
        jr rqs
rq2:    ld a,13
        call sendb
        call recv
        ld a,b
        or c
        ret

; draw: clear the screen and print the page at HL: the header on line 0,
; the rows from line 2, the footer on lines 20 and 21, and the sound chip
; in the bottom right corner.
draw:   res 1,(iy+1)            ; FLAGS: print to the screen, not the printer
        push hl
        call CLS
        pop hl
        xor a
        ld (inv),a
        ld (pline),a
        ld e,32
        call prline             ; header
        ld (rowsp),hl
        ld a,2
        ld (pline),a
        ld a,(nrows)
        or a
        jr z,dp2
        ld b,a
dp1:    push bc
        ld e,32
        call prline
        ld a,(pline)
        inc a
        ld (pline),a
        pop bc
        djnz dp1
dp2:    ld a,20
        ld (pline),a
        ld e,32
        call prline             ; footer
        ld a,21
        ld (pline),a
        ld e,21                 ; the rest of line 21 is the chip
        call prline
        ld a,(chip)             ; 10 characters, columns 21-30
        ld hl,chipnm
        ld bc,10
        or a
dp3:    jr z,dp4
        add hl,bc
        dec a
        jr dp3
dp4:    ld e,10
        ld bc,21*256+21
        jr prat2                ; (column 31 of line 21 stays empty: printing
                                ; there would move the ROM onto line 22)

chipnm: ; 10 bytes each, inverse, by USR 16517's answer
        db $80,$80,$b3,$b4,$80,$a8,$ad,$ae,$b5,$8f  ; "NO CHIP?"
        db $80,$a6,$be,$96,$9f,$96,$a4,$a5,$9d,$9c  ; "AY-3-8910"
        db $80,$80,$80,$80,$be,$b2,$9e,$9d,$a0,$a5  ; "YM2149"
        db $80,$80,$80,$ba,$b3,$b0,$b3,$b4,$bc,$b3  ; "UNKNOWN"

; prline: print E bytes from HL on line (pline), from column 0, each XORed
; with (inv). Returns HL past them.
prline: ld a,(pline)
        ld b,a
        ld c,0
prat2:  push hl
        push de
        call PRAT
        pop de
        pop hl
pr1:    ld a,(inv)
        xor (hl)
        push hl
        push de
        rst $10
        pop de
        pop hl
        inc hl
        dec e
        jr nz,pr1
        ret

; hilite / lolite: print the selected row in inverse, or back to normal
hilite: ld a,$80
        jr hl1
lolite: xor a
hl1:    ld (inv),a
        ld a,(sel)
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld de,(rowsp)
        add hl,de
        ld a,(sel)
        add a,2
        ld (pline),a
        ld e,32
        call prline
        xor a
        ld (inv),a
        ret

; getkey: wait for a key, A = its character. A key held down repeats after
; 0.4 s, then every 0.08 s.
getkey: ld bc,(LAST_K)
        ld a,c
        inc a
        jr nz,gk1
        xor a                   ; nothing pressed
        ld (held),a
        jr getkey
gk1:    ld a,(held)
        or a
        jr nz,gk2
        inc a
        ld (held),a
        ld a,20
        ld (rpt),a
        jr gk3
gk2:    call frame
        ld a,(rpt)
        dec a
        ld (rpt),a
        jr nz,getkey
        ld a,4
        ld (rpt),a
gk3:    ld bc,(LAST_K)
        ld a,c
        inc a
        jr z,getkey             ; let go meanwhile
        call DECODE
        ld a,(hl)
        ret

; flush: read and drop bytes until 0.2 s without one
flush:  ld b,10
fl1:    push bc
        call frame
        call query
        pop bc
        or a
        jr nz,fl2
        djnz fl1
        ret
fl2:    ld b,a
fl3:    push bc
        call rdb
        pop bc
        djnz fl3
        jr flush

; frame: wait for the next 50 Hz tick
frame:  ld a,(FRAMES)
        ld b,a
fr1:    ld a,(FRAMES)
        cp b
        jr z,fr1
        ret

; waitup: wait (up to 1 s) until no key is down
waitup: ld b,50
wu1:    call keyup
        ret z
        push bc
        call frame
        pop bc
        djnz wu1
        ret

buf:    ds BUFSZ,0
