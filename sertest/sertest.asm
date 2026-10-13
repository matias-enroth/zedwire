; SERTEST - measures the ZXpand+ serial limits. Lives in the line-10 REM at 16514.
; Step 2 of the chiptune plan: the receive buffer size, and how fast the ZX81
; can read. The ZX81 is a slave here; sertest/zxsertest.py runs the tests.
;
; USR 16514: say hello ('H' and the protocol version, repeated every second
;   for 30 s) until the server sends a command, then obey commands until 'Q':
;     'R' dly(2) n(2) flags(1)
;                      answer 'r', so the server knows we're listening, then
;                      wait dly frames without touching the serial port, then
;                      read up to n bytes (max BUFSZ) until 1 s without a byte,
;                      then count and drop anything more until 1 s of silence.
;                      flags bit 0: ask $C5 before every byte, instead of
;                      reading as many bytes as the last $C5 said.
;                      A 'k' goes back after every 64 bytes read: credits, so
;                      the server never gets 256 bytes ahead. 256 unread bytes
;                      hard-freeze the ZXpand+.
;                      Report back: 'D', count(2), elap(2), av0, avmax,
;                      extra(2), then the count bytes received.
;                        elap  = frames from the first byte read to the last
;                        av0   = first "bytes waiting" ($C5) after the wait
;                        avmax = largest "bytes waiting" seen
;                        extra = bytes that came after the n-th
;     'Q'              return to BASIC
;   Returns BC = 0 after 'Q', 1 = no command for 30 s, 2 = unknown command
;   (the byte is left at 16517). All 2-byte values are little-endian.
;
; ZXpand+ port API as in pingpong.asm / docs/zxpand-serial.md. Timing uses
; FRAMES, so run it in SLOW mode. Touches A,BC,DE,HL only; IX, IY and AF' left alone.

FRAMES:  equ 16436
BUFSZ:   equ 4096
T_CMD:   equ 30*50               ; frames to wait for the next command
T_SHORT: equ 1*50                ; hello interval, argument and idle timeout
VERSION: equ 2                   ; protocol version, sent after each 'H'

        org 16514
        jp slave                ; 16514
badcmd: db 0                    ; 16517 unknown command byte

; report header, sent in this order
count:  dw 0
elap:   dw 0
av0:    db 0
avmax:  db 0
extra:  dw 0

dly:    dw 0                    ; 'R' arguments, in this order
nmax:   dw 0
flags:  db 0
tfirst: dw 0
tlast:  dw 0
tries:  db 0
avail:  db 0
t0:     dw 0
tlim:   dw 0

slave:  xor a
        ld (avail),a
        ld a,30
        ld (tries),a
hello:  ld a,'H'
        call sendb
        ld a,VERSION
        call sendb
        ld hl,T_SHORT
        ld (tlim),hl
        call getb
        jr nc,gotcmd
        ld a,(tries)
        dec a
        ld (tries),a
        jr nz,hello
tmo:    ld bc,1
        ret

cmd:    ld hl,T_CMD
        ld (tlim),hl
        call getb
        jr c,tmo
gotcmd: cp 'R'
        jr z,rcmd
        cp 'Q'
        jr z,quit
        ld (badcmd),a
        ld bc,2
        ret
quit:   ld bc,0
        ret

; 'R': read the arguments
rcmd:   ld hl,T_SHORT
        ld (tlim),hl
        ld hl,dly
        ld b,5
rarg:   push bc
        call getb
        pop bc
        jr c,tmo
        ld (hl),a
        inc hl
        djnz rarg
        ld a,'r'                ; listening
        call sendb
        ld hl,(nmax)            ; clamp n to BUFSZ
        ld de,BUFSZ+1
        or a
        sbc hl,de
        jr c,nok
        ld hl,BUFSZ
        ld (nmax),hl
nok:    ld hl,(dly)             ; sit still while the buffer fills
        call waitf
        ld hl,0
        ld (tfirst),hl
        ld (tlast),hl
        call query              ; first look at the buffer
        ld (av0),a
        ld (avmax),a
        ld (avail),a
        ld hl,buf
        ld de,0
rloop:  push hl
        ld hl,(nmax)
        or a
        sbc hl,de
        pop hl
        jr z,rend
        call getb
        jr c,rend
        ld (hl),a
        inc hl
        inc de
        ld a,(flags)            ; bit 0: forget the count, ask again
        and 1
        jr z,rkeep
        xor a
        ld (avail),a
rkeep:  ld a,e                  ; a credit every 64 bytes
        and 63
        jr nz,rnocr
        push hl
        push de
        ld a,'k'
        call sendb
        pop de
        pop hl
rnocr:  push hl
        ld hl,(FRAMES)
        ld (tlast),hl
        ld a,d
        or a
        jr nz,rnext
        ld a,e
        dec a
        jr nz,rnext
        ld (tfirst),hl          ; first byte
rnext:  pop hl
        jr rloop
rend:   ld (count),de
        ld hl,(tfirst)          ; FRAMES counts down: elapsed = first - last
        ld de,(tlast)
        or a
        sbc hl,de
        ld a,h
        and $7f
        ld h,a
        ld (elap),hl
        ld hl,0                 ; drop and count whatever else arrives
        ld (extra),hl
drain:  call getb
        jr c,report
        ld hl,(extra)
        inc hl
        ld (extra),hl
        ld a,h                  ; stop at 4096, in case the bytes never end
        cp $10
        jr nz,drain

; report: 'D', the header, then the data
report: ld a,'D'
        call sendb
        ld hl,count
        ld b,8
rhead:  push bc
        ld a,(hl)
        push hl
        call sendb
        pop hl
        inc hl
        pop bc
        djnz rhead
        ld hl,buf
        ld de,(count)
rdata:  ld a,d
        or e
        jp z,cmd
        ld a,(hl)
        push hl
        push de
        call sendb
        pop de
        pop hl
        inc hl
        dec de
        jr rdata

; wait HL frames
waitf:  ld a,h
        or l
        ret z
        ld a,(FRAMES)
        ld e,a
wfl:    ld a,(FRAMES)
        cp e
        jr z,wfl
        dec hl
        jr waitf

; query: A = bytes waiting ($C5)
query:  ld bc,$e007
        ld a,$c5
        out (c),a
        call wcc
        in a,(c)
        ret

; getb: next rx byte in A, carry clear; carry set after tlim frames without
; one. Keeps track of avmax. Preserves HL, DE.
getb:   push hl
        push de
        ld a,(avail)
        or a
        jr nz,rd
        ld hl,(FRAMES)
        ld (t0),hl
gpoll:  call query
        or a
        jr nz,gotav
        ld hl,(t0)
        ld de,(FRAMES)
        or a
        sbc hl,de
        ld a,h
        and $7f
        ld h,a
        ld de,(tlim)
        or a
        sbc hl,de
        jr c,gpoll
        pop de
        pop hl
        scf
        ret
gotav:  ld (avail),a
        ld hl,avmax
        cp (hl)
        jr c,rd
        ld (hl),a
rd:     ld a,(avail)
        dec a
        ld (avail),a
        ld bc,$e007
        ld a,$c7                ; fetch one byte
        out (c),a
        call wcc
        in a,(c)
        pop de
        pop hl
        or a
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

buf:    ds BUFSZ,0
