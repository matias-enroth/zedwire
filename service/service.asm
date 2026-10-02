; SERVICE - ZX81 side of zxservice. Lives in the line-10 REM at 16514.
;
; USR 16514 (recv): read a zxservice reply - 2-byte LE length, then that
;   many bytes - into BUF. Returns BC = bytes stored (0 = no reply).
;   Declared length is left in RLEN so BASIC can spot a short read.
; USR 16517 (prt):  print BUF via ROM RST $10, converting ASCII to the
;   ZX81 charset (lowercase folded to upper, \n -> newline, CR dropped).
;
; ZXpand+ port API as in pingpong.asm. Timeouts use FRAMES (SLOW mode).
; Touches A,BC,DE,HL only; IX/IY left alone.

FRAMES:  equ 16436
BUFSZ:   equ 1024
T_FIRST: equ 12*50               ; frames to wait for the reply to start
T_NEXT:  equ 1*50                ; frames to wait between bytes

        org 16514
        jp recv                 ; 16514
        jp prt                  ; 16517
rlen:   dw 0                    ; 16520 declared reply length
count:  dw 0                    ; 16522 bytes actually received
avail:  db 0
t0:     dw 0
tlim:   dw 0

recv:   xor a
        ld (avail),a
        ld hl,0
        ld (count),hl
        ld (rlen),hl
        ld hl,T_FIRST
        ld (tlim),hl
        call getb
        jr c,rdone
        ld (rlen),a
        ld hl,T_NEXT
        ld (tlim),hl
        call getb
        jr c,rdone
        ld (rlen+1),a
        ld hl,(rlen)            ; clamp to buffer size
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
        jr rloop
rstore: ld (count),de
rdone:  ld bc,(count)
        ret

; getb: next rx byte in A, carry clear; carry set on timeout (tlim frames).
; Preserves HL, DE.
getb:   push hl
        push de
        ld a,(avail)
        or a
        jr nz,rd
        ld hl,(FRAMES)
        ld (t0),hl
gpoll:  ld bc,$e007
        ld a,$c5                ; how many bytes waiting?
        out (c),a
        call wcc
        in a,(c)
        or a
        jr nz,gotav
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
        jr c,gpoll
        pop de
        pop hl
        scf
        ret
gotav:  ld (avail),a
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

wcc:    in a,($17)
        and $80
        jr nz,wcc
        ret

prt:    ld hl,buf
        ld de,(count)
ploop:  ld a,d
        or e
        ret z
        ld a,(hl)
        inc hl
        dec de
        and $7f
        push hl
        ld hl,table
        ld c,a
        ld b,0
        add hl,bc
        ld a,(hl)
        pop hl
        cp $ff                  ; unprintable: skip
        jr z,ploop
        cp $fe                  ; newline marker (keeps $76 out of the REM)
        jr nz,pout
        ld a,$75                ; $76 without a literal $76 byte
        inc a
pout:   push hl
        push de
        rst $10
        pop de
        pop hl
        jr ploop

table:
        include "table.inc"

buf:    ds BUFSZ,0
