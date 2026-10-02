# zedwire - ZX81 / ZXpand+ serial tools
#
#   make          build zxsvr (only needs a C compiler)
#   make zx       rebuild the ZX81 programs (.p) from source - needs z80asm
#   make test     run the machine code in a Z80 emulator - needs the z80
#                 package: python3 -m venv .venv && .venv/bin/pip install z80
#
# Prebuilt .bin and .P files are committed, so `make zx` is only needed
# if you change the assembly or BASIC.

CC     ?= cc
CFLAGS ?= -Wall -Wextra -O2
Z80ASM ?= z80asm
# use ./.venv if there is one (see make test)
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

all: zxsvr

zxsvr: zxsvr.c
	$(CC) $(CFLAGS) -o $@ $<

zx: pingpong/PINGPONG.P service/SERVICE.P

pingpong/pingpong.bin: pingpong/pingpong.asm
	cd pingpong && $(Z80ASM) -o pingpong.bin pingpong.asm

service/table.inc: tools/mktable.py
	$(PYTHON) tools/mktable.py $@

service/service.bin: service/service.asm service/table.inc
	cd service && $(Z80ASM) -o service.bin service.asm

pingpong/PINGPONG.P: pingpong/pingpong.bin pingpong/pingpong.bas tools/mkp.py
	$(PYTHON) tools/mkp.py pingpong/pingpong.bin pingpong/pingpong.bas $@

service/SERVICE.P: service/service.bin service/service.bas tools/mkp.py
	$(PYTHON) tools/mkp.py service/service.bin service/service.bas $@

test:
	$(PYTHON) tools/simulate.py

clean:
	rm -f zxsvr

.PHONY: all zx test clean
