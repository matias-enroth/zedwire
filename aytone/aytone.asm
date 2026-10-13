; ZXpand+ AY tone test - one tone on channel A. Lives in line 10 REM at 16514.
; Step 1 of the chiptune plan: prove we can drive the AY from our own code.
;
; ZXpand+ AY ports (ZXpand online manual, "Sound hardware"; same pairs as ZON-X):
;   OUT ($CF),a   select register a
;   OUT ($0F),a   write a to the selected register
;   IN  a,($CF)   read the selected register back
; Write order as in Charlie Robson's ZXpand-Vitamins sndtest/main.asm.
; Registers: only A,BC,DE touched; IX, IY and AF' left alone (SLOW-mode display).
;
; Entry points and data, as used by aytone.bas:
;   USR 16514  tone:  period/volume -> channel A, mixer = tone A only.
;                     BC = registers 1:0 read back (= the period if the AY answers)
;   USR 16517  quiet: volume A = 0, mixer = all off. BC = 0
;   16520/1    per:   tone period, 1-4095 (low byte first)
;   16522      vol:   volume 0-15
;   USR 16523  clock: run in FAST mode. 10 beeps: the tone on for half of a
;                     fixed number of Z80 T-states, off for the other half.
;                     A recording gives both the AY's clock (the tone pitch)
;                     and the ZX81's own clock (the beep rate), so we can see
;                     whether the AY runs off the ZX81's clock.
;   USR 16526  chip:  which chip is it? Writes $FF to R1 (4 bits used) and
;                     R6 (5 bits), reads them back, restores them.
;                     BC = R6 read back * 256 + R1 read back:
;                     $1F0F = AY-3-8910 (unused bits read as 0),
;                     $FFFF = YM2149 (they read back as written).
;                     Source: MAME src/devices/sound/ay8910.cpp, "tested and
;                     confirmed on hardware".

        org 16514

        jp tone
        jp quiet
per:    dw 252                  ; 252 = 440 Hz if the AY runs at 1.7734 MHz
vol:    db 15
        jp clock                ; 16523
        jp chip                 ; 16526
ncyc:   db 0

tone:   ld a,(per)
        ld e,a
        xor a
        call wr                 ; R0 tone A fine
        ld a,(per+1)
        and $0f
        ld e,a
        ld a,1
        call wr                 ; R1 tone A coarse (4 bits)
        ld a,(vol)
        and $0f
        ld e,a
        ld a,8
        call wr                 ; R8 volume A, fixed level (no envelope)
        ld e,$fe
        ld a,7
        call wr                 ; R7 mixer: tone A on; B, C and noise off
        ld a,1                  ; read back R1:R0 into BC
        out ($cf),a
        in a,($cf)
        ld b,a
        xor a
        out ($cf),a
        in a,($cf)
        ld c,a
        ret

quiet:  ld e,0
        ld a,8
        call wr                 ; R8 volume A = 0
        ld e,$ff
        ld a,7
        call wr                 ; R7 mixer: everything off
        ld bc,0
        ret

; 10 beeps, one every 6,500,420 T-states (tools/simulate.py checks
; this). No interrupts in FAST mode, so the Z80 runs at its own clock throughout.
NBEEPS: equ 10
HALFN:  equ 4999                ; inner loop count, ~3.25M T-states per half

clock:  ld a,NBEEPS
        ld (ncyc),a
cyc:    call tone
        call half
        call quiet
        call half
        ld a,(ncyc)
        dec a
        ld (ncyc),a
        jr nz,cyc
        ld bc,0
        ret

half:   ld e,25
h1:     ld bc,HALFN
h2:     dec bc
        ld a,b
        or c
        jr nz,h2
        dec e
        jr nz,h1
        ret

chip:   ld a,1                  ; save R1 and R6
        out ($cf),a
        in a,($cf)
        ld d,a
        ld a,6
        out ($cf),a
        in a,($cf)
        push af
        ld e,$ff
        ld a,1
        call wr
        ld a,6
        call wr
        ld a,1                  ; read back
        out ($cf),a
        in a,($cf)
        ld c,a
        ld a,6
        out ($cf),a
        in a,($cf)
        ld b,a
        ld e,d                  ; restore
        ld a,1
        call wr
        pop af
        ld e,a
        ld a,6
        call wr
        ret

; write E to AY register A
wr:     out ($cf),a
        ld a,e
        out ($0f),a
        ret
