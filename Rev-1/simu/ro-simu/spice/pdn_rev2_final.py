#!/usr/bin/env python3
"""Rev-2, weryfikacja koncowa zalecanego filtru.

1. Ringi 3G04 (FB7..FB10, jeden uklad na ring -- 3x mniej pojemnosci lokalnej).
2. Odpornosc na tolerancje: indukcyjnosc ferrytu, derating MLCC, tolerancja R.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdn_rev2_ring as P

db = lambda x: 20 * math.log10(max(x, 1e-30))


def scan(v):
    ht, rt = P.run_one(P.build_tf(v), v["tag"], "tf")
    hz, rz = P.run_one(P.build(v), v["tag"], "z")
    pk, pf = P.peak(ht, rt, "zp")
    return dict(
        t270=P.at_freq(ht, rt, "zp", 270e3),
        t540=P.at_freq(ht, rt, "zp", 540e3),
        pk=pk, pf=pf,
        zmax=P.peak(hz, rz, "zp")[0],
        z157=P.at_freq(hz, rz, "zp", P.F_RING),
        k12=P.at_freq(hz, rz, "k12", 270e3),
    )


def show(rows, title):
    print("\n" + "=" * 104)
    print(" " + title)
    print("=" * 104)
    print(f"{'przypadek':40s} {'270kHz':>9s} {'540kHz':>9s} {'szczyt':>9s} "
          f"{'@ f':>10s} {'Zmax':>8s} {'K12@270k':>11s}")
    print("-" * 104)
    for name, r in rows:
        fm = f"{r['pf']/1e3:9.0f}k" if r["pf"] else "        -"
        print(f"{name:40s} {db(r['t270']):+9.1f} {db(r['t540']):+9.1f} "
              f"{db(r['pk']):+9.1f} {fm:>10s} {r['zmax']:8.3f} {r['k12']:11.2e}")


def main():
    os.makedirs(P.OUT, exist_ok=True)
    HF = ["100n", "2n2"]

    # ---------- 1. ringi 3G04: jeden uklad, 1/3 pojemnosci lokalnej ----------
    # modelujemy przez chip_caps podzielone na 3 galezie -> tu 1 galaz o
    # pelnym pradzie; uzywamy tego samego szkieletu, ale HF tylko na jednym
    # wezle => emulacja: chip_caps = 1/3 zestawu na kazdej z 3 galezi.
    rows = []
    for tag, desc, filt, rail in [
        ("g3_asbuilt", "3G04 JAK TERAZ: FB600, 100n+2n2 (1 ukl.)",
         ("fb", "0.95u"), []),
        ("g3_4u7", "3G04: FB600 + 4u7", ("fb", "0.95u"), ["4u7"]),
        ("g3_r2_4u7", "3G04: FB600 + R 2.2R + 4u7", ("fbr", "0.95u", 2.2), ["4u7"]),
        ("g3_r4_4u7", "3G04: FB600 + R 4.7R + 4u7", ("fbr", "0.95u", 4.7), ["4u7"]),
    ]:
        v = dict(tag=tag, per_chip=False, filt=filt, rail_caps=rail,
                 chip_caps=HF, desc=desc)
        # 3G04 = jeden uklad => zestaw HF wystepuje raz, nie trzy razy.
        # Osiagamy to przez podmiane w netliscie: gasimy HF na galeziach 1 i 2.
        src_z = P.build(v)
        src_t = P.build_tf(v)
        for br in (1, 2):
            for g in (1, 2):
                for k in range(len(HF)):
                    src_z = src_z.replace(f"Xcg{br}{g}{k} g{br}{g} 0 ", f"*Xcg{br}{g}{k} g{br}{g} 0 ")
                    src_t = src_t.replace(f"Xcg{br}{g}{k} g{br}{g} 0 ", f"*Xcg{br}{g}{k} g{br}{g} 0 ")
        P.run_one(src_z, tag, "z")
        P.run_one(src_t, tag, "tf")
        hz, rz = P.read_dat(os.path.join(P.OUT, f"z_{tag}.dat"))
        ht, rt = P.read_dat(os.path.join(P.OUT, f"tf_{tag}.dat"))
        pk, pf = P.peak(ht, rt, "zp")
        rows.append((desc, dict(t270=P.at_freq(ht, rt, "zp", 270e3),
                                t540=P.at_freq(ht, rt, "zp", 540e3),
                                pk=pk, pf=pf,
                                zmax=P.peak(hz, rz, "zp")[0],
                                z157=P.at_freq(hz, rz, "zp", P.F_RING),
                                k12=P.at_freq(hz, rz, "k12", 270e3))))
    show(rows, "RINGI 3G04 (FB7..FB10) -- jeden uklad na ring, 1/3 pojemnosci lokalnej")

    # ---------- 2. odpornosc zalecanej konfiguracji ----------
    rows = []
    cases = [
        ("nominal: FB 0.95uH, R 2.2R, 4u7 (3.5uF eff)", "0.95u", 2.2, "4u7"),
        ("ferryt 2x wieksza L (2.0uH)",                 "2.0u",  2.2, "4u7"),
        ("ferryt 2x mniejsza L (0.5uH)",                "0.5u",  2.2, "4u7"),
        ("R -20% (1.8R)",                               "0.95u", 1.8, "4u7"),
        ("R +20% (2.7R)",                               "0.95u", 2.7, "4u7"),
        ("bulk 1uF zamiast 4u7 (montaz zly)",           "0.95u", 2.2, "1u"),
        ("bulk 10uF 0805",                              "0.95u", 2.2, "10u"),
        ("R 4.7R + 4u7 (wariant z zapasem)",            "0.95u", 4.7, "4u7"),
    ]
    for i, (name, l, r, bulk) in enumerate(cases):
        v = dict(tag=f"rob{i}", per_chip=False, filt=("fbr", l, r),
                 rail_caps=[bulk], chip_caps=HF, desc=name)
        rows.append((name, scan(v)))
    show(rows, "ODPORNOSC ZALECANEJ KONFIGURACJI (ringi 1G04, 3 uklady)")

    print("\nDla porownania punkt wyjscia (stan obecny, ring 1G04): "
          "+10.0 dB @270k, szczyt +15.4 dB @316k, Zmax 10.1 Ohm, K12@270k 5.9e-01")
    return 0


if __name__ == "__main__":
    sys.exit(main())
