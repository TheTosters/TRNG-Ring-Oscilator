#!/usr/bin/env python3
"""Analiza impedancji sieci zasilania (PDN) plytki RO-TRNG i dobor filtrow.

Generuje netlisty ngspice dla kolejnych wariantow filtracji i zbiera:
  Zpin(f)  -- impedancja widziana przez pin VCC ukladu z ringami
  fres     -- czestotliwosc i wysokosc rezonansu (peaking) filtru
  Zpin @ f_ring i harmonicznych
  Kxy(f)   -- przenoszenie zaklocen miedzy VCC dwoch roznych ukladow

Topologia odwzorowuje realna plytke Rev-1/hardware/RO-Trng.kicad_sch:
  USB 5V -> C12(1u) -> AP2112K-3.3 -> VCC (C9 100n, C10 1u, C14 100n, C15 2n2)
  VCC -> FB2 -> VCCQ (C7 10u, C8 1u, C16 100n)
  VCCQ -> FB3 -> U3 (C5 100n, C6 2n2)
  VCCQ -> FB4 -> U2 (C3 100n, C4 2n2)
  VCCQ -> FB5 -> U1 (C1 2n2, C2 100n)
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
OUT = os.path.join(HERE, "out")

F_RING = 159.5e6  # z tb_ro_single.cir


def filt(name, a, b, kind, val=None):
    """Element szeregowy filtru miedzy wezlami a i b."""
    if kind == "ferrite":
        return f"X{name} {a} {b} BLM18AG102SN1D\n"
    if kind == "ferrite_l":          # ferryt z wymuszona mniejsza L (podmagnesowanie)
        return f"X{name} {a} {b} BLM18AG102SN1D l={val}\n"
    if kind == "L100n":             # zastepnik uzyty w schemacie simu
        return f"L{name} {a} m{name} 100n\nR{name} m{name} {b} 0.1\n"
    if kind == "res":               # zwykly rezystor szeregowy
        return f"R{name} {a} {b} {val}\n"
    if kind == "short":
        return f"R{name} {a} {b} 1m\n"
    if kind == "rl":                # rezystor + ferryt (tlumienie rezonansu)
        return f"R{name} {a} f{name} {val}\nX{name} f{name} {b} BLM18AG102SN1D\n"
    raise ValueError(kind)


def build(variant, chip_caps, vccq_caps, probe="vu1"):
    """Zwraca netliste dla danego wariantu."""
    n = []
    n.append("* auto-generated PDN study")
    n.append(f'.include "{MODELS}/AP2112K.lib"')
    n.append(f'.include "{MODELS}/passives.lib"')
    n.append("")
    # --- strona wejsciowa: kabel USB + C12 ---
    n.append("V5   v5s 0 DC 5 AC 0")
    n.append("Lusb v5s lu 500n")
    n.append("Rusb lu  v5 0.2")
    n.append("Xc12 v5  0  C0805_1U")
    # --- LDO ---
    n.append("Xldo v5 0 v5 nc vcc AP2112K_33")
    # --- szyna VCC: C9 0603 100n, C10 0805 1u, C14 0402 100n, C15 0402 2n2 ---
    n.append("Xc9  vcc 0 C0603_100N")
    n.append("Xc10 vcc 0 C0805_1U")
    n.append("Xc14 vcc 0 C0402_100N")
    n.append("Xc15 vcc 0 C0402_2N2")
    # obciazenie STM32 + 74HC174 (DC, do punktu pracy)
    n.append("Idig vcc 0 DC 25m")
    # --- FB2: VCC -> VCCQ ---
    n.append("Xtr1 vcc vccqa TRACE len=8")
    n.append(filt("fb2", "vccqa", "vccq", variant["fb2"], variant.get("fb2v")).rstrip())
    # --- szyna VCCQ ---
    for i, c in enumerate(vccq_caps):
        n.append(f"Xq{i} vccq 0 {c}")
    # --- trzy odgalezienia do ukladow U1/U2/U3 ---
    for k, chip in enumerate(("vu1", "vu2", "vu3"), start=3):
        n.append(f"Xtr{k} vccq q{chip} TRACE len=5")
        n.append(filt(f"fb{k}", f"q{chip}", chip, variant["fbc"], variant.get("fbcv")).rstrip())
        for i, c in enumerate(chip_caps):
            n.append(f"Xc{chip}{i} {chip} 0 {c}")
        # kazdy uklad = 2 ringi, srednio 2 x 24 mA
        n.append(f"Idc{chip} {chip} 0 DC 48m")
    # --- wstrzykniecie pradu AC w pin badanego ukladu ---
    n.append(f"Iinj 0 {probe} AC 1")
    n.append("")
    n.append(".control")
    n.append("set noaskquit")
    n.append("set nobreak")
    n.append("op")
    n.append("ac dec 50 1k 3g")
    n.append(f"let zp = abs(v({probe}))")
    n.append("let zq = abs(v(vccq))")
    n.append("let zc = abs(v(vcc))")
    n.append("let z5 = abs(v(v5))")
    n.append("let kx = abs(v(vu2))")
    n.append("let kx3 = abs(v(vu3))")
    n.append("set wr_singlescale")
    n.append("set wr_vecnames")
    n.append(f'wrdata {OUT}/pdn_{variant["tag"]}.dat zp zq zc z5 kx kx3')
    n.append(".endc")
    n.append(".end")
    return "\n".join(n) + "\n"


def read_dat(path):
    """Czyta plik wrdata -> (naglowki, lista wierszy)."""
    with open(path) as f:
        lines = [ln.split() for ln in f if ln.strip()]
    hdr = lines[0]
    rows = [[float(x) for x in ln] for ln in lines[1:]]
    return hdr, rows


def at_freq(hdr, rows, col, f):
    """Interpolacja logarytmiczna wartosci kolumny `col` przy czestotliwosci f."""
    j = hdr.index(col)
    prev = None
    for r in rows:
        if r[0] >= f:
            if prev is None:
                return r[j]
            import math
            t = (math.log(f) - math.log(prev[0])) / (math.log(r[0]) - math.log(prev[0]))
            return prev[j] + t * (r[j] - prev[j])
        prev = r
    return rows[-1][j]


def peak(hdr, rows, col, fmin=1e3, fmax=1e9):
    """Najwyzszy lokalny szczyt (nie koniec zakresu) danej kolumny."""
    j = hdr.index(col)
    best = (0.0, 0.0)
    for i in range(1, len(rows) - 1):
        f = rows[i][0]
        if not (fmin <= f <= fmax):
            continue
        v = rows[i][j]
        if v >= rows[i - 1][j] and v >= rows[i + 1][j] and v > best[0]:
            best = (v, f)
    return best  # (wartosc, czestotliwosc)


def run(src, tag, variant_tag):
    path = os.path.join(OUT, f"_pdn_{tag}.cir")
    with open(path, "w") as f:
        f.write(src)
    r = subprocess.run([sys.executable, os.path.join(HERE, "ngrun.py"), path],
                       capture_output=True, text=True, timeout=1800)
    dat = os.path.join(OUT, f"pdn_{variant_tag}.dat")
    if not os.path.exists(dat):
        print(r.stdout[-3000:])
        raise RuntimeError(f"wariant {tag}: brak pliku wynikow")
    hdr, rows = read_dat(dat)
    res = {}
    for f, t in ((F_RING, "f1"), (2 * F_RING, "f2"), (3 * F_RING, "f3"),
                 (5 * F_RING, "f5"), (10 * F_RING, "f10")):
        res[f"zp_{t}"] = at_freq(hdr, rows, "zp", f)
        res[f"kx_{t}"] = at_freq(hdr, rows, "kx", f)
        res[f"z5_{t}"] = at_freq(hdr, rows, "z5", f)
    for f, t in ((1e3, "1k"), (1e5, "100k"), (1e6, "1m"), (1e7, "10m")):
        res[f"zp_{t}"] = at_freq(hdr, rows, "zp", f)
    res["zp_max"], res["zp_maxf"] = peak(hdr, rows, "zp")
    res["zq_max"], res["zq_maxf"] = peak(hdr, rows, "zq")
    return res


# ---------------------------------------------------------------- warianty
CH_ASBUILT = ["C0402_100N", "C0402_2N2"]
VQ_ASBUILT = ["C0805_10U", "C0603_1U", "C0603_100N"]

VARIANTS = [
    dict(tag="simu_L100n", desc="jak w schemacie simu: L=100n + R=0.1 (ZASTEPNIK)",
         fb2="L100n", fbc="L100n", ch=CH_ASBUILT, vq=VQ_ASBUILT),
    dict(tag="asbuilt", desc="realna plytka: ferryty BLM18AG102SN1D",
         fb2="ferrite", fbc="ferrite", ch=CH_ASBUILT, vq=VQ_ASBUILT),
    dict(tag="nofilter", desc="bez filtru per uklad (tylko wspolne VCCQ)",
         fb2="ferrite", fbc="short", ch=CH_ASBUILT, vq=VQ_ASBUILT),
    dict(tag="r10", desc="rezystor 10 Ohm zamiast ferrytu per uklad",
         fb2="ferrite", fbc="res", fbcv="10", ch=CH_ASBUILT, vq=VQ_ASBUILT),
    dict(tag="r22", desc="rezystor 22 Ohm zamiast ferrytu per uklad",
         fb2="ferrite", fbc="res", fbcv="22", ch=CH_ASBUILT, vq=VQ_ASBUILT),
    dict(tag="ferrite_damp", desc="ferryt + 4.7 Ohm szeregowo (tlumienie rezonansu)",
         fb2="ferrite", fbc="rl", fbcv="4.7", ch=CH_ASBUILT, vq=VQ_ASBUILT),
    dict(tag="asbuilt_bigC", desc="ferryty + dodatkowe 1u 0402 przy kazdym ukladzie",
         fb2="ferrite", fbc="ferrite",
         ch=CH_ASBUILT + ["C0603_1U"], vq=VQ_ASBUILT),
    dict(tag="damp_rc", desc="ferryt + 1u 0603 + snubber RC (10R+10n) per uklad",
         fb2="ferrite", fbc="ferrite",
         ch=CH_ASBUILT + ["C0603_1U", "SNUB"], vq=VQ_ASBUILT),
    dict(tag="rc22_10u", desc="R=22 Ohm + 10uF + 100n + 2n2 per uklad (zamiast ferrytu)",
         fb2="ferrite", fbc="res", fbcv="22",
         ch=["C0402_100N", "C0402_2N2", "C0805_10U"], vq=VQ_ASBUILT),
    dict(tag="rc10_10u", desc="R=10 Ohm + 10uF + 100n + 2n2 per uklad",
         fb2="ferrite", fbc="res", fbcv="10",
         ch=["C0402_100N", "C0402_2N2", "C0805_10U"], vq=VQ_ASBUILT),
    dict(tag="fb_rc22", desc="ferryt + R=22 Ohm + 10uF + 100n + 2n2 (pas i szelki)",
         fb2="ferrite", fbc="rl", fbcv="22",
         ch=["C0402_100N", "C0402_2N2", "C0805_10U"], vq=VQ_ASBUILT),
    dict(tag="fb_bulk22u", desc="ferryt + 22uF + 100n + 2n2 (bez rezystora)",
         fb2="ferrite", fbc="ferrite",
         ch=["C0402_100N", "C0402_2N2", "C0805_10U", "C0805_10U"], vq=VQ_ASBUILT),
]

SNUB = """.subckt SNUB 1 2
Rs 1 s 10
Cs s 2 10n
.ends
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for v in VARIANTS:
        ch = v["ch"]
        src = build(v, ch, v["vq"])
        if "SNUB" in ch:
            src = src.replace("* auto-generated PDN study",
                              "* auto-generated PDN study\n" + SNUB)
        res = run(src, v["tag"], v["tag"])
        rows.append((v, res))
        print(f"[{v['tag']}] ok")

    print("\n" + "=" * 118)
    print(" IMPEDANCJA SIECI ZASILANIA WIDZIANA PRZEZ PIN VCC UKLADU Z RINGAMI")
    print(f" (f_ring = {F_RING/1e6:.1f} MHz)")
    print("=" * 118)
    hdr = (f"{'wariant':16s} {'Zpin@1k':>9s} {'@100k':>8s} {'@1M':>8s} {'@10M':>8s} "
           f"{'@fring':>8s} {'@2f':>8s} {'@3f':>8s} {'Zmax':>8s} {'@fmax':>10s} {'K12@fr':>9s}")
    print(hdr)
    print("-" * 118)
    for v, r in rows:
        fm = f"{r['zp_maxf']/1e6:8.3f}M" if r["zp_maxf"] else "     ---"
        print(f"{v['tag']:16s} "
              f"{r['zp_1k']:9.4f} {r['zp_100k']:8.4f} {r['zp_1m']:8.4f} "
              f"{r['zp_10m']:8.4f} {r['zp_f1']:8.4f} {r['zp_f2']:8.4f} "
              f"{r['zp_f3']:8.4f} {r['zp_max']:8.3f} "
              f"{fm:>10s} {r['kx_f1']:9.2e}")
    print("-" * 118)
    print("Zpin w omach. Zmax/@fmax = szczyt rezonansu sieci (im wyzszy, tym gorzej).")
    print("K12@fr = przenoszenie zaklocenia z pinu VCC ukladu U1 na pin VCC U2")
    print("         przy czestotliwosci ringu (sprzezenie miedzy ukladami).")
    print("\nSzczyt rezonansu na wspolnej szynie VCCQ:")
    for v, r in rows:
        fq = f"{r['zq_maxf']/1e6:.3f} MHz" if r["zq_maxf"] else "brak"
        print(f"  {v['tag']:16s} Zq_max = {r['zq_max']:8.3f} Ohm  @ {fq}")
    print("\nOpis wariantow:")
    for v, _ in rows:
        print(f"  {v['tag']:16s} {v['desc']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
