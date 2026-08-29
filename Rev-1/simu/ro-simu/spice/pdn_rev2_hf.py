#!/usr/bin/env python3
"""Rev-2, czesc druga: dobor kondensatorow HF PRZY UKLADZIE.

Filtr szynowy ustalony w pdn_rev2_ring.py (FB600 + R 2.2R + 4u7 na ring).
Tutaj pytanie: co postawic przy kazdym z trzech 74LVC1G04 -- 100n+2n2 jak teraz,
czy cos innego. Kryterium: impedancja PDN na pinie VCC w okolicy czestotliwosci
ringu (157 MHz) i jej harmonicznych, oraz brak antyrezonansu miedzy okladkami.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdn_rev2_ring import (CAPLIB, F_RING, at_freq, build, build_tf, peak,
                           read_dat, run_one, OUT)

HF_SETS = [
    ("100n+2n2   (jak teraz)", ["100n", "2n2"]),
    ("100n       (sam)",       ["100n"]),
    ("2 x 100n",               ["100n", "100n"]),
    ("100n+1u",                ["100n", "1u"]),
    ("100n+2n2+1u",            ["100n", "2n2", "1u"]),
    ("2n2        (sam)",       ["2n2"]),
]

BASE = dict(per_chip=False, filt=("fbr", "0.95u", 2.2), rail_caps=["4u7"])


def main():
    os.makedirs(OUT, exist_ok=True)
    db = lambda x: 20 * math.log10(max(x, 1e-30))
    print("=" * 108)
    print(" KONDENSATORY HF PRZY UKLADZIE -- impedancja PDN na pinie VCC 74LVC1G04 [Ohm]")
    print(" (szyna: FB600 + R 2.2R + 4u7; f_ring = 157 MHz)")
    print("=" * 108)
    print(f"{'zestaw':26s} {'@10M':>8s} {'@50M':>8s} {'@157M':>8s} {'@314M':>8s} "
          f"{'@471M':>8s} {'Zmax 10M..1G':>14s} {'@ f':>10s}")
    print("-" * 108)
    for name, caps in HF_SETS:
        v = dict(BASE, tag="hf_" + name.split()[0].replace("+", "_").replace("x", "n"),
                 chip_caps=caps, desc=name)
        v["tag"] = "hf_" + str(abs(hash(name)) % 100000)
        hz, rz = run_one(build(v), v["tag"], "z")
        pk, pf = peak(hz, rz, "zp", fmin=1e7, fmax=1e9)
        print(f"{name:26s} "
              f"{at_freq(hz,rz,'zp',1e7):8.3f} {at_freq(hz,rz,'zp',5e7):8.3f} "
              f"{at_freq(hz,rz,'zp',F_RING):8.3f} {at_freq(hz,rz,'zp',2*F_RING):8.3f} "
              f"{at_freq(hz,rz,'zp',3*F_RING):8.3f} "
              f"{pk:14.3f} {pf/1e6:9.1f}M")
    print("-" * 108)
    print("Zmax w pasmie 10 MHz..1 GHz = antyrezonans miedzy kondensatorami / z ESL montazu.")
    print("Nizej = lepiej. Prad ringu ma skladowe przy 157 MHz i jej nieparzystych harmonicznych.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
