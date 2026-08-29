#!/usr/bin/env python3
"""Rev-2 bez izolacji, czesc druga: stopien wejsciowy LDO ringow (U1).

Po usunieciu przetwornicy dominujacym szczytem w torze VBUS -> ring nie jest
juz rezonans filtru ringu, tylko L2 (22 uH) z C2 (1 uF) -- ok. 30 kHz, tam
gdzie PSRR AP2112K ma dopiero ~50 dB. Pytanie: czy L2 pomaga, czy szkodzi.

Filtr ringu ustalony na wariant D (FB600 + R 2.2R + 4u7).
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdn_rev2_noiso as P

db = lambda x: 20 * math.log10(max(x, 1e-30))
BASE = dict(per_chip=False, filt=("fbr", 2.2), chip_caps=["100n", "2n2"],
            rail_caps=["4u7"])

# (opis, podmiana w netliscie)
CASES = [
    ("jak teraz: C58 10u + L2 22uH + C2 1u", []),
    ("bez L2 (zwarcie), C58 10u + C2 1u",
     [("Xl2 vbus n1 L1812_22U", "Rl2 vbus n1 1m")]),
    ("L2 22uH + C2 zwiekszone do 10u",
     [("Xc2 n1 0 C1206_1U", "Xc2 n1 0 C1206_10U")]),
    ("L2 22uH + tlumik RC 4.7R + 4u7",
     [("Xldo1 n1 0 n1 nc1 vdda AP2112K_33",
       "Rdm n1 dm 4.7\nXcdm dm 0 C1206_4U7\nXldo1 n1 0 n1 nc1 vdda AP2112K_33")]),
    ("L2 22uH + tlumik RC 5.1R + 10u",
     [("Xldo1 n1 0 n1 nc1 vdda AP2112K_33",
       "Rdm n1 dm 5.1\nXcdm dm 0 C1206_10U\nXldo1 n1 0 n1 nc1 vdda AP2112K_33")]),
    ("L2 22uH z szeregowym R 4.7R",
     [("Xl2 vbus n1 L1812_22U", "Xl2 vbus n1x L1812_22U\nRl2s n1x n1 4.7")]),
    ("L2 -> ferryt GZ1608D601TF",
     [("Xl2 vbus n1 L1812_22U", "Xl2 vbus n1 GZ1608D601TF")]),
    ("L2 4.7uH + C2 10u (nizsza L, wieksze C)",
     [("Xl2 vbus n1 L1812_22U", "Xl2 vbus n1 L1812_22U l=4.7u"),
      ("Xc2 n1 0 C1206_1U", "Xc2 n1 0 C1206_10U")]),
]


def main():
    os.makedirs(P.OUT, exist_ok=True)
    print("=" * 116)
    print(" TOR VBUS -> VCC RINGU, zaleznie od stopnia wejsciowego U1")
    print(" (filtr ringu staly: FB600 + R 2.2R + 4u7)")
    print("=" * 116)
    print(f"{'stopien wejsciowy U1':40s} {'10kHz':>8s} {'30kHz':>8s} "
          f"{'100kHz':>8s} {'1MHz':>8s} {'szczyt':>9s} {'@ f':>10s} {'dewiacja*':>11s}")
    print("-" * 116)
    for i, (name, subs) in enumerate(CASES):
        v = dict(BASE, tag=f"in{i}")
        src = P.build(v, "vbus")
        for a, b in subs:
            assert a in src, a
            src = src.replace(a, b)
        path = os.path.join(P.OUT, f"_vbus_{v['tag']}.cir")
        with open(path, "w") as f:
            f.write(src)
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(P.HERE, "ngrun.py"), path],
                           capture_output=True, text=True, timeout=1800)
        dat = os.path.join(P.OUT, f"vbus_{v['tag']}.dat")
        if not os.path.exists(dat):
            print(r.stdout[-3000:]); raise RuntimeError(name)
        h, rows = P.read_dat(dat)
        pk, pf = P.peak(h, rows, "vu5")
        dev = 0.050 * pk * 81.4e6 / 1e3
        fm = f"{pf/1e3:8.1f}k" if pf else "       -"
        print(f"{name:40s} {db(P.at(h,rows,'vu5',1e4)):+8.1f} "
              f"{db(P.at(h,rows,'vu5',3e4)):+8.1f} "
              f"{db(P.at(h,rows,'vu5',1e5)):+8.1f} "
              f"{db(P.at(h,rows,'vu5',1e6)):+8.1f} "
              f"{db(pk):+9.1f} {fm:>10s} {dev:8.1f} kHz")
    print("-" * 116)
    print("* dewiacja czestotliwosci ringu przy 50 mV szumu hosta na VBUS w szczycie,")
    print("  dF/dVCC = 81.4 MHz/V. Szum wspolny dla wszystkich 8 ringow -> skorelowany.")

    # --- osobno: przenoszenie zaklocen 74HC174 przy czestotliwosci ringu ---
    print("\n" + "=" * 116)
    print(" 74HC174 (U3/U22 na VDDA) -> VCC RINGU przy czestotliwosci pracy")
    print(" [Ohm; ile napiecia na ringu robi 1 mA zaklocenia z zatrzasku]")
    print("=" * 116)
    cfgs = [
        ("bez filtru (jak teraz), ring jak teraz", dict(per_chip=False, filt=("fb",),
         chip_caps=["100n", "2n2"], rail_caps=[])),
        ("bez filtru, ring FB+2.2R+4u7", BASE),
        ("HC174 przez FB600+4.7R+100n+1u, ring FB+2.2R+4u7",
         dict(BASE, hc_filt=("fbr", 4.7), hc_caps=["100n", "1u"])),
        ("HC174 przez FB600+100n+1u, ring FB+2.2R+4u7",
         dict(BASE, hc_filt=("fb",), hc_caps=["100n", "1u"])),
    ]
    print(f"{'konfiguracja':52s} {'@1MHz':>9s} {'@10MHz':>9s} "
          f"{'@157MHz':>9s} {'@314MHz':>9s}")
    print("-" * 116)
    for i, (name, cfg) in enumerate(cfgs):
        v = dict(cfg, tag=f"hcx{i}")
        h, rows = P.run(v, "hc")
        print(f"{name:52s} {P.at(h,rows,'vu5',1e6):9.5f} "
              f"{P.at(h,rows,'vu5',1e7):9.5f} {P.at(h,rows,'vu5',157e6):9.5f} "
              f"{P.at(h,rows,'vu5',314e6):9.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
