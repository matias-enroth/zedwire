; ZXpand+ raw serial ping/pong - lives in line 10 REM at 16514
; ZXpand+ port API (from ZXpand-Vitamins serial-breakout/serialio.c):
;   OUT ($0007)      reset transfer buffer
;   OUT ($4007),a    push byte a into transfer buffer
;   OUT ($E007),$C6  write buffered byte to serial
;   OUT ($E007),$C5  -> IN ($E007) = bytes waiting in rx buffer
;   OUT ($E007),$C7  -> IN ($E007) = next rx byte
;   IN ($17) bit 7   busy
; Registers: only A,BC,DE,HL touched; IX/IY left alone (SLOW-mode display).
;
; Entry points and data, as used by pingpong.bas:
;   USR 16514  start: send PING, then receive
;   USR 16529  rx:    receive only (RUN 300 / RUN 400)
;   16640      buf:   received bytes

        org 16514

; USR 16514 : send "PING\n", then receive into BUF. BC = bytes received.
start:  ld hl,msg
txlp:   ld a,(hl)
        or a
        jr z,rx
        push hl
        call sendb
        pop hl
        inc hl
        jr txlp

; USR 16529 : receive only. BC = bytes received.
rx:     ld hl,buf
        ld e,0
rxwait: ld bc,30000
        ld (cnt),bc
poll:   ld bc,$e007
        ld a,$c5
        out (c),a
        call wcc
        in a,(c)
        or a
        jr nz,got
        ld bc,(cnt)
        dec bc
        ld (cnt),bc
        ld a,b
        or c
        jr nz,poll
        jr done
got:    ld bc,$e007
        ld a,$c7
        out (c),a
        call wcc
        in a,(c)
        ld (hl),a
        inc hl
        inc e
        cp 10
        jr z,done
        ld a,e
        cp 64
        jr nz,rxwait
done:   ld b,0
        ld c,e
        ret

; send byte in A
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

msg:    db "PING",10,0
cnt:    dw 0
buf:    ds 64,0
