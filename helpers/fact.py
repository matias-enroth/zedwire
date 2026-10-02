#!/usr/bin/env python3
"""FACT n - exact factorial. zxservice trims the output to fit the screen."""
import math
import sys

try:
    n = int(sys.argv[1])
except (IndexError, ValueError):
    sys.exit("USAGE: FACT <WHOLE NUMBER>")
if n > 5000:
    sys.exit("TOO BIG - TRY 5000 OR LESS")
getattr(sys, 'set_int_max_str_digits', int)(0)       # Python 3.11+ caps int->str at 4300 digits
digits = str(math.factorial(n))
print(f"{n} FACTORIAL ({len(digits)} DIGITS):")   # no "!" on a ZX81
print(digits)
