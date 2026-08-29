#!/usr/bin/env python3
"""Rev-2 bez izolacji, czesc trzecia: dobor R i C filtru ringu.

Po usunieciu przetwornicy jedynym mechanizmem tlumienia ponizej ~100 kHz
jest szeregowe RC filtru ringu (ferryt jest tam elektrycznie niewidoczny --
0.97 uH to przy 30 kHz zaledwie 0.18 Ohm). Stad kompromis: wieksze R = nizsza
czestotliwosc zalamania, ale wiekszy spadek DC i wieksze przesuniecie
czestotliwosci pracy ringu.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdn_rev2_noiso as P

db = lambda x: 20 * math.log10(max(x, 1e-30))
HF = ["100n", "2n2"]
CEFF = {"4u7": 3.3e-6, "10u": 6.0e-6}     # pojemnosc efektywna @3.3 V


def main():
    os.makedirs(P.OUT, exist_ok=True)
    print("=" * 124)
    print(" DOBOR R I C FILTRU RINGU (Rev-2 bez izolacji, ferryt GZ1608D601TF)")
    print("=" * 124)
    print(f"{'filtr':26s} {'f_zal':>8s} {'10kHz':>7s} {'30kHz':>7s} {'100kHz':>7s} "
          f"{'1MHz':>8s} {'Zmax':>7s} {'K12@30k':>9s} {'K12@100k':>9s} "
          f"{'spadek':>8s} {'df_stat':>8s}")
    print("-" * 124)
    rows = []
    for i, (r, c) in enumerate([(0, "4u7"), (1.0, "4u7"), (2.2, "4u7"), (4.7, "4u7"),
                                (10, "4u7"), (2.2, "10u"), (4.7, "10u"), (10, "10u")]):
        filt = ("fb",) if r == 0 else ("fbr", r)
        v = dict(tag=f"rc{i}", per_chip=False, filt=filt, chip_caps=HF,
                 rail_caps=[c])
        hz, rz = P.run(v, "z")
        hb, rb = P.run(v, "vbus")
        # transmitancja filtru ringu = ring / VDDA
        def tf(f):
            return P.at(hb, rb, "vu5", f) / P.at(hb, rb, "vda", f)
        zmax, _ = P.peak(hz, rz, "vu5")
        rtot = 0.45 + r
        drop = rtot * P.I_RING
        fc = 1 / (2 * math.pi * rtot * CEFF[c]) if r > 0 else float("nan")
        name = f"FB + {r:g}R + {c}" if r else f"FB + {c} (bez R)"
        fcs = f"{fc/1e3:6.1f}k" if r else "   ---"
        print(f"{name:26s} {fcs:>8s} {db(tf(1e4)):+7.1f} {db(tf(3e4)):+7.1f} "
              f"{db(tf(1e5)):+7.1f} {db(tf(1e6)):+8.1f} {zmax:7.3f} "
              f"{P.at(hz,rz,'vu8',3e4):9.2e} {P.at(hz,rz,'vu8',1e5):9.2e} "
              f"{drop*1e3:6.0f} mV {drop*81.4:6.1f} MHz")
        rows.append((name, drop))
    print("-" * 124)
    print("f_zal   = czestotliwosc zalamania RC (ponizej niej filtr nie tlumi nic)")
    print("dB      = transmitancja VDDA -> VCC ringu (sam filtr, bez LDO)")
    print("K12     = sprzezenie ring1 -> ring2 (nizej = lepsza dekorelacja kanalow)")
    print("spadek  = spadek DC przy 24 mA; df_stat = statyczne przesuniecie f ringu")
    print("          (81.4 MHz/V; przesuniecie statyczne jest nieszkodliwe)")

    # --- referencja: stan obecny ---
    v0 = dict(tag="ref", per_chip=False, filt=("fb",), chip_caps=HF, rail_caps=[])
    hz, rz = P.run(v0, "z")
    hb, rb = P.run(v0, "vbus")
    tf = lambda f: P.at(hb, rb, "vu5", f) / P.at(hb, rb, "vda", f)
    zmax, zf = P.peak(hz, rz, "vu5")
    print(f"\nSTAN OBECNY (FB + 3x(100n+2n2), bez bulku i bez R):")
    print(f"  10 kHz {db(tf(1e4)):+.1f} dB | 30 kHz {db(tf(3e4)):+.1f} dB | "
          f"100 kHz {db(tf(1e5)):+.1f} dB | 1 MHz {db(tf(1e6)):+.1f} dB")
    print(f"  Zmax {zmax:.2f} Ohm @ {zf/1e3:.0f} kHz | "
          f"K12@30k {P.at(hz,rz,'vu8',3e4):.2e} | K12@100k {P.at(hz,rz,'vu8',1e5):.2e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
