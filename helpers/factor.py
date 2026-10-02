#!/usr/bin/env python3
"""FACTOR n - prime factors by trial division (zxservice's timeout stops
hopeless cases)."""
import sys

try:
    n = int(sys.argv[1])
except (IndexError, ValueError):
    sys.exit("USAGE: FACTOR <WHOLE NUMBER>")
if n < 2:
    sys.exit("NEEDS A NUMBER OF 2 OR MORE")

factors, rest, d = [], n, 2
while d * d <= rest:
    while rest % d == 0:
        factors.append(d)
        rest //= d
    d += 1 if d == 2 else 2
if rest > 1:
    factors.append(rest)

print(f"{n} =")
print(' * '.join(map(str, factors)))
if len(factors) == 1:
    print("(PRIME)")
