#!/usr/bin/env python3
"""Rev-2: czestotliwosci osmiu ringow z docelowymi czesciami i rezystorami.

Dwie konstrukcje wg schematu Rev-2:
  A) ring z TRZECH obudow 74LVC1G04 (U5-U7, U8-U10, U11-U13, U14-U16)
     - kazda bramka na wlasnym krzemie, wlasna para pinow VCC/GND
     - sciezka miedzy obudowami ok. 4-5 mm
  B) ring z JEDNEJ obudowy 74LVC3G04 (U17..U20)
     - trzy bramki na jednym krzemie, jedna para pinow VCC/GND
     - UWAGA: petla i tak zamyka sie po ZEWNATRZ (pin 7->6 i 2->3 zwarte
       na PCB), wiec sciezki sa krotkie (~1.5 mm), ale nie zerowe

Rezystor rozstrajajacy siedzi w jednym wezle petli i dokłada opoznienie
ok. 0.7*R*C_wezla. Proponowany zestaw (wszystkie Basic/Preferred w JLCPCB):
  1G04:  22 / 49.9 / 100 / 200 Ohm
  3G04:  33 / 75 / 150 / brak (0 Ohm)

Model inwertera: models/74LVC04A.lib, skalibrowany na t_pd = 2.0 ns @ 50 pF,
3.3 V wzgl. karty TI SCES208. To ta sama rodzina procesowa co 1G04/3G04,
ale NIE ten sam uklad -- patrz komentarz o skalowaniu na koncu.
"""
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "rev2_freq")

# (nazwa, pojemnosc sciezki na wezel, lista rezystorow rozstrajajacych)
CONFIGS = [
    ("1G04 (3 obudowy)", 0.6e-12, [22, 49.9, 100, 200]),
    ("3G04 (1 obudowa)", 0.2e-12, [33, 75, 150, 0]),
]
VDD = 3.3


def deck(ctrace, rdet, vdd=VDD, tstop=400e-9):
    r = f"Rd n3 n3r {rdet}" if rdet > 0 else "Rd n3 n3r 1m"
    return f"""* ring Rev-2
.include {HERE}/models/74LVC04A.lib
Vdd vdd 0 DC {vdd}
X1 n3r n1 vdd 0 74LVC04A_NEX
X2 n1  n2 vdd 0 74LVC04A_NEX
X3 n2  n3 vdd 0 74LVC04A_NEX
{r}
Ct1 n1  0 {ctrace}
Ct2 n2  0 {ctrace}
Ct3 n3  0 {ctrace}
Ct4 n3r 0 {ctrace}
.ic v(n1)=0 v(n2)={vdd} v(n3)=0
.control
set noaskquit
set nobreak
tran 1p {tstop} 0 1p
meas tran t1 when v(n1)=1.65 rise=3  from=150n
meas tran t2 when v(n1)=1.65 rise=23 from=150n
let tper = (t2-t1)/20
let fosc = 1/tper
print fosc
meas tran iavg avg i(Vdd) from=200n to=350n
meas tran ipk  min i(Vdd) from=200n to=350n
.endc
.end
"""


def run(src, tag):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, f"{tag}.cir")
    with open(p, "w") as f:
        f.write(src)
    r = subprocess.run([sys.executable, os.path.join(HERE, "ngrun.py"), p],
                       capture_output=True, text=True, timeout=900)
    out = r.stdout
    def g(name):
        m = re.search(rf"{name}\s*=\s*([-\d.e+]+)", out)
        return float(m.group(1)) if m else float("nan")
    return g("fosc"), abs(g("iavg")), abs(g("ipk"))


def main():
    print("=" * 96)
    print(" CZESTOTLIWOSCI RINGOW REV-2  (model 74LVC, VDD = 3.3 V na pinie ukladu)")
    print("=" * 96)
    print(f"{'konstrukcja':22s} {'R_det':>7s} {'f [MHz]':>9s} {'okres':>9s} "
          f"{'I_sr':>8s} {'I_szcz':>8s} {'wzgl. 0R':>9s}")
    print("-" * 96)
    allf = []
    for name, ct, rs in CONFIGS:
        f0, _, _ = run(deck(ct, 0), f"{name[:4]}_ref")
        for rd in rs:
            tag = f"{name[:4]}_{str(rd).replace('.','p')}"
            f, ia, ip = run(deck(ct, rd), tag)
            allf.append((name, rd, f))
            print(f"{name:22s} {rd:6.1f}R {f/1e6:9.2f} {1e9/f:8.3f}n "
                  f"{ia*1e3:6.1f}mA {ip*1e3:6.0f}mA {100*(f/f0-1):+8.1f}%")
        print()

    print("=" * 96)
    print(" ROZSUNIECIE CZESTOTLIWOSCI MIEDZY WSZYSTKIMI OSMIOMA RINGAMI")
    print("=" * 96)
    srt = sorted(allf, key=lambda x: -x[2])
    for i, (n, rd, f) in enumerate(srt):
        d = "" if i == 0 else f"  odstep do poprzedniego: {(srt[i-1][2]-f)/1e6:6.2f} MHz " \
                              f"({100*(srt[i-1][2]-f)/f:5.2f} %)"
        print(f"  {i+1}. {n:22s} R={rd:5.1f}R  f = {f/1e6:7.2f} MHz{d}")
    fmin, fmax = srt[-1][2], srt[0][2]
    print(f"\n  rozrzut calkowity: {fmin/1e6:.1f} .. {fmax/1e6:.1f} MHz "
          f"({100*(fmax/fmin-1):.1f} %)")
    mind = min((srt[i-1][2]-srt[i][2])/srt[i][2] for i in range(1, len(srt)))
    print(f"  najmniejszy wzgledny odstep sasiadow: {100*mind:.2f} %")

    print("\n" + "=" * 96)
    print(" CZULOSC NA NAPIECIE ZASILANIA (dF/dVCC) -- dla ringu bez rezystora")
    print("=" * 96)
    for name, ct, _ in CONFIGS:
        fa, _, _ = run(deck(ct, 0, vdd=3.25), f"{name[:4]}_v325")
        fb, _, _ = run(deck(ct, 0, vdd=3.35), f"{name[:4]}_v335")
        print(f"  {name:22s} dF/dVCC = {(fb-fa)/0.1/1e6:7.1f} MHz/V  "
              f"({(fb-fa)/0.1/fa*100:5.2f} %/V)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
