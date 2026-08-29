#!/usr/bin/env python3
"""Rev-2: odsprzeganie zatrzaskow 74HC174 (U3, U22) po przeniesieniu na VCC.

Stan po zmianie uzytkownika:
    U2 AP2112K -> VCC -> FB(GZ1608D601TF) -> U3/U22, lokalnie 100n + 2n2

Pytanie: czy dolozyc 1 uF rownolegle.

Zatrzask jest UKLADEM PROBKUJACYM -- jego lokalne VCC ustala prog przelaczania
wejscia D, wiec tetnienie na tym wezle przeklada sie wprost na chwile
probkowania. Kazdy 74HC174 probkuje 4 kanaly, wiec zaburzenie jego szyny jest
skorelowane dla tych czterech kanalow. To jest inne kryterium niz przy ringach:
liczy sie NISKA I PLASKA impedancja wezla, a nie tlumienie toru.

Wejscia D sa pobudzane wprost z ringow (~157 MHz), wiec stopnie wejsciowe
przelaczaja sie z ta czestotliwoscia -- prad zasilania ma silna skladowa HF.
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
OUT = os.path.join(HERE, "out", "rev2_latch")

F_RING = 157e6
I_LATCH = 16e-3      # jeden 74HC174: 4 wejscia D przelaczane f_ring
I_MCU = 30e-3

CAP = {"100n": "C0402_100N", "2n2": "C0402_2N2", "1u": "C0603_1U",
       "4u7": "C0805_4U7", "10u": "C0805_10U", "1u6": "C1206_1U",
       "4u7b": "C1206_4U7", "10ub": "C1206_10U"}


def build(v, probe="l1"):
    n = ["* zatrzaski 74HC174 na VCC",
         f'.include "{MODELS}/AP2112K.lib"',
         f'.include "{MODELS}/passives.lib"', "",
         "V5 v5s 0 DC 5 AC 0", "Lusb v5s lu 500n", "Rusb lu vbus 0.2",
         "Xfb2 vbus n2 GZ1608D601TF",
         f"Xc3 n2 0 {CAP['1u6']}",
         "Xldo2 n2 0 n2 nc2 vcc AP2112K_33",
         f"Xc5 vcc 0 {CAP['4u7b']}",
         f"Xc46 vcc 0 {CAP['100n']}", f"Xc47 vcc 0 {CAP['100n']}",
         f"Xc53 vcc 0 {CAP['1u6']}",
         f"Imcu vcc 0 DC {I_MCU}"]

    # --- dwa zatrzaski ---
    if v["shared_fb"]:
        n.append("Xtl vcc pl TRACE len=6")
        n.append("Xfbl pl lc GZ1608D601TF" if v["fb"] else "Rfbl pl lc 1m")
        if v.get("rs"):
            n.append(f"Rsl lc lcr {v['rs']}")
            lc = "lcr"
        else:
            lc = "lc"
        for k, c in enumerate(v.get("shared_caps", [])):
            n.append(f"Xcsh{k} {lc} 0 {CAP[c]}")
        for i, node in enumerate(("l1", "l2")):
            n.append(f"Xtn{i} {lc} {node} TRACE len=5")
            for k, c in enumerate(v["caps"]):
                n.append(f"Xcl{i}{k} {node} 0 {CAP[c]}")
            n.append(f"Il{i} {node} 0 DC {I_LATCH}")
    else:
        for i, node in enumerate(("l1", "l2")):
            n.append(f"Xtl{i} vcc p{node} TRACE len=6")
            n.append(f"Xfbl{i} p{node} q{node} GZ1608D601TF" if v["fb"]
                     else f"Rfbl{i} p{node} q{node} 1m")
            if v.get("rs"):
                n.append(f"Rsl{i} q{node} {node} {v['rs']}")
            else:
                n.append(f"Rsl{i} q{node} {node} 1m")
            for k, c in enumerate(v["caps"]):
                n.append(f"Xcl{i}{k} {node} 0 {CAP[c]}")
            n.append(f"Il{i} {node} 0 DC {I_LATCH}")

    n.append(f"Iinj 0 {probe} AC 1")
    n += ["", ".control", "set noaskquit", "set nobreak", "op",
          "ac dec 50 1k 1g",
          "let zl1 = abs(v(l1))", "let zl2 = abs(v(l2))", "let zvc = abs(v(vcc))",
          "set wr_singlescale", "set wr_vecnames",
          f'wrdata {OUT}/{v["tag"]}.dat zl1 zl2 zvc', ".endc", ".end"]
    return "\n".join(n) + "\n"


def rd(p):
    L = [l.split() for l in open(p) if l.strip()]
    return L[0], [[float(x) for x in l] for l in L[1:]]


def at(h, r, c, f):
    j = h.index(c); prev = None
    for x in r:
        if x[0] >= f:
            if prev is None:
                return x[j]
            t = (math.log(f) - math.log(prev[0])) / (math.log(x[0]) - math.log(prev[0]))
            return prev[j] + t * (x[j] - prev[j])
        prev = x
    return r[-1][j]


def peak(h, r, c, fmin=5e3, fmax=5e7):
    j = h.index(c); best = (0.0, 0.0)
    for i in range(1, len(r) - 1):
        f = r[i][0]
        if not (fmin <= f <= fmax):
            continue
        if r[i][j] >= r[i-1][j] and r[i][j] >= r[i+1][j] and r[i][j] > best[0]:
            best = (r[i][j], f)
    return best


def run(v):
    src = build(v)
    p = os.path.join(OUT, f"_{v['tag']}.cir")
    with open(p, "w") as f:
        f.write(src)
    r = subprocess.run([sys.executable, os.path.join(HERE, "ngrun.py"), p],
                       capture_output=True, text=True, timeout=1800)
    d = os.path.join(OUT, f"{v['tag']}.dat")
    if not os.path.exists(d):
        print(r.stdout[-3000:]); raise RuntimeError(v["tag"])
    return rd(d)


db = lambda x: 20 * math.log10(max(x, 1e-30))
HF = ["100n", "2n2"]

VARIANTS = [
    ("bez ferrytu, 100n+2n2", dict(fb=False, shared_fb=False, caps=HF)),
    ("FB, 100n+2n2  (stan po zmianie)", dict(fb=True, shared_fb=False, caps=HF)),
    ("FB, 100n+2n2+1u", dict(fb=True, shared_fb=False, caps=HF + ["1u"])),
    ("FB, 100n+2n2+4u7", dict(fb=True, shared_fb=False, caps=HF + ["4u7"])),
    ("FB, 100n+2n2+10u", dict(fb=True, shared_fb=False, caps=HF + ["10u"])),
    ("FB+2.2R, 100n+2n2+1u", dict(fb=True, shared_fb=False, caps=HF + ["1u"], rs=2.2)),
    ("FB+2.2R, 100n+2n2+4u7", dict(fb=True, shared_fb=False, caps=HF + ["4u7"], rs=2.2)),
    ("bez ferrytu, 100n+2n2+4u7", dict(fb=False, shared_fb=False, caps=HF + ["4u7"])),
]
SHARED = [
    ("1 FB na OBA zatrzaski, 100n+2n2 przy kazdym",
     dict(fb=True, shared_fb=True, caps=HF, shared_caps=[])),
    ("1 FB na OBA + 4u7 wspolny, 100n+2n2 przy kazdym",
     dict(fb=True, shared_fb=True, caps=HF, shared_caps=["4u7"])),
    ("2 FB (po jednym), 100n+2n2+4u7 przy kazdym",
     dict(fb=True, shared_fb=False, caps=HF + ["4u7"])),
]


def report(title, cases, off=0):
    print("\n" + "=" * 116)
    print(" " + title)
    print("=" * 116)
    print(f"{'konfiguracja':38s} {'Zmax':>8s} {'@ f':>10s} {'Z@1M':>8s} "
          f"{'Z@10M':>8s} {'Z@157M':>8s} {'sprz. L1->L2':>13s} {'@157M':>9s}")
    print("-" * 116)
    for i, (name, cfg) in enumerate(cases):
        v = dict(cfg, tag=f"lt{off+i}")
        h, r = run(v)
        pk, pf = peak(h, r, "zl1")
        z1m = at(h, r, "zl1", 1e6)
        z157 = at(h, r, "zl1", F_RING)
        # sprzezenie zatrzask1 -> zatrzask2 w szczycie i przy f_ring
        cpk = db(at(h, r, "zl2", pf) / at(h, r, "zl1", pf)) if pf else 0
        c157 = db(at(h, r, "zl2", F_RING) / z157)
        fm = f"{pf/1e3:8.1f}k" if pf else "       -"
        print(f"{name:38s} {pk:8.3f} {fm:>10s} {z1m:8.3f} "
              f"{at(h,r,'zl1',1e7):8.3f} {z157:8.3f} {cpk:+12.1f}dB {c157:+8.1f}dB")


def main():
    os.makedirs(OUT, exist_ok=True)
    report("IMPEDANCJA LOKALNEJ SZYNY 74HC174 (probkujacego) [Ohm]", VARIANTS)
    report("JEDEN FERRYT NA OBA ZATRZASKI CZY PO JEDNYM", SHARED, off=20)
    print("\nZmax = szczyt rezonansu FB z kondensatorami (5 kHz..50 MHz).")
    print("sprz. L1->L2 = ile zaburzenia z jednego zatrzasku widzi drugi.")
    print("Wejscia D pracuja z f_ring ~157 MHz -- kolumny Z@157M i sprz.@157M")
    print("sa dla tego ukladu najwazniejsze.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
