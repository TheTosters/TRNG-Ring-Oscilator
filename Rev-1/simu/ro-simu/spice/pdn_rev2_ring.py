#!/usr/bin/env python3
"""Dobor filtru zasilania pojedynczego ringu w Rev-2 (arkusz OscilatorRing).

Topologia z netlisty Rev-2/hardware/RO_hardware.kicad_sch:

    LDO AP2112K (U1) -> VDDA  [C4 4u7 + C43 100n + C42 2n2 + C7 100n + C6 2n2]
    VDDA -> FB3 (600R@100MHz) -> Net-(U5-VCC)
        Net-(U5-VCC) zasila U5, U6, U7  (3 x 74LVC1G04 = jeden ring)
        przy kazdym ukladzie: 100n + 2n2 w 0402
    analogicznie FB4..FB6 dla ringow 1G04, FB7..FB10 dla ringow 3G04
    (w sumie 8 galezi na wspolnej szynie VDDA)

Pytanie do rozstrzygniecia:
  (a) 1 x FB na ring + kondensatory przy kazdym ukladzie   -- jak teraz
  (b) FB + R + C osobno przy KAZDYM z trzech ukladow
  (c) 1 x FB + R szeregowy + bulk na ringu + HF przy ukladach

Mierzone:
  Zpin   -- impedancja PDN widziana przez pin VCC ukladu U5 (srodek ringu)
  H_vdda -- transmitancja VDDA -> VCC ringu (czy filtr TLUMI czy WZMACNIA
            zaklocenie 270 kHz z przetwornicy IB0505XT i jej harmoniczne)
  K12    -- przenoszenie z pinu VCC ringu 1 na pin VCC ringu 2 (dekorelacja)
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
OUT = os.path.join(HERE, "out", "rev2")

F_RING = 157e6      # czestotliwosc ringu (Rev-1 zmierzona w symulacji)
I_RING = 24e-3      # prad jednego ringu (3 x 1G04) przy 157 MHz
F_SMPS = 270e3      # czestotliwosc przetwornicy IB0505XT-1WR3

N_BRANCH = 8        # ile galezi (ringow) wisi na VDDA


# --------------------------------------------------------------------------
# elementy filtru
# --------------------------------------------------------------------------
def series(name, a, b, spec):
    """spec: ('fb', L) | ('r', R) | ('fbr', L, R) | ('short',)"""
    kind = spec[0]
    if kind == "short":
        return [f"R{name} {a} {b} 1m"]
    if kind == "fb":
        return [f"X{name} {a} {b} FB600 l={spec[1]}"]
    if kind == "r":
        return [f"R{name} {a} {b} {spec[1]}"]
    if kind == "fbr":
        return [f"X{name} {a} m{name} FB600 l={spec[1]}",
                f"R{name} m{name} {b} {spec[2]}"]
    raise ValueError(kind)


CAPLIB = {
    "100n": "C0402_100N",
    "2n2":  "C0402_2N2",
    "1u":   "C0603_1U",
    "4u7":  "C1206_4U7",
    "10u":  "C0805_10U",
}


def build(v, probe="u5"):
    n = ["* Rev-2 ring PDN",
         f'.include "{MODELS}/AP2112K.lib"',
         f'.include "{MODELS}/passives.lib"',
         # 1206 4.7uF X5R -- derating ~-25% przy 3.3V
         ".subckt C1206_4U7 1 2",
         "Xc 1 2 CAP c=3.5u esr=6m esl=1.1n lmnt=0.8n",
         ".ends",
         # snubber RC rownolegly (opcjonalny)
         ".subckt SNUB 1 2",
         "Rs 1 s 1.0",
         "Xs s 2 C0805_10U",
         ".ends",
         ""]

    # --- zasilanie strony izolowanej: przetwornica + LDO ---
    n.append("V5 v5 0 DC 5 AC 1")          # AC 1 -> mierzymy transmitancje z wejscia
    n.append("Xldo v5 0 v5 nc vdda AP2112K_33")

    # --- szyna VDDA: C4 4u7 + C43 100n + C42 2n2 + C7 100n + C6 2n2 ---
    for i, c in enumerate(v.get("vdda", ["4u7", "100n", "2n2", "100n", "2n2"])):
        n.append(f"Xq{i} vdda 0 {CAPLIB[c]}")
    # 74HC174 x2 + strona izolowana ISO7761 x2 wiszace na VDDA (Problem 4)
    n.append("Idig vdda 0 DC 30m")

    # --- galaz 1: badany ring (U5, U6, U7) ---
    # --- galaz 2: identyczny ring obok (do pomiaru sprzezenia K12) ---
    for br, tag in ((1, "u5"), (2, "u8")):
        rail = f"ring{br}"
        n.append(f"Xtb{br} vdda p{rail} TRACE len=4")
        if v["per_chip"]:
            # filtr osobno przy kazdym ukladzie -- wariant (b)
            n += series(f"fbm{br}", f"p{rail}", rail, v.get("pre", ("short",)))
            for k, c in enumerate(v.get("rail_caps", [])):
                n.append(f"Xrc{br}{k} {rail} 0 {CAPLIB[c]}")
            for g in range(3):
                node = f"g{br}{g}"
                n.append(f"Xtg{br}{g} {rail} q{node} TRACE len=3")
                n += series(f"fbg{br}{g}", f"q{node}", node, v["filt"])
                for k, c in enumerate(v["chip_caps"]):
                    n.append(f"Xcg{br}{g}{k} {node} 0 {CAPLIB[c]}")
                if v.get("snub_chip"):
                    n.append(f"Xsn{br}{g} {node} 0 SNUB")
                n.append(f"Ig{br}{g} {node} 0 DC {I_RING/3}")
        else:
            # jeden filtr na ring -- warianty (a) i (c)
            n += series(f"fb{br}", f"p{rail}", rail, v["filt"])
            for k, c in enumerate(v.get("rail_caps", [])):
                n.append(f"Xrc{br}{k} {rail} 0 {CAPLIB[c]}")
            if v.get("snub_rail"):
                n.append(f"Xsn{br} {rail} 0 SNUB")
            for g in range(3):
                node = f"g{br}{g}"
                n.append(f"Xtg{br}{g} {rail} {node} TRACE len=3")
                for k, c in enumerate(v["chip_caps"]):
                    n.append(f"Xcg{br}{g}{k} {node} 0 {CAPLIB[c]}")
                n.append(f"Ig{br}{g} {node} 0 DC {I_RING/3}")
        # wezel sondujacy: pierwszy uklad galezi
    n.append("Rprobe1 g10 u5 1u")
    n.append("Rprobe2 g20 u8 1u")

    # --- pozostale 6 galezi jako obciazenie VDDA (uproszczone) ---
    for k in range(N_BRANCH - 2):
        n.append(f"Xto{k} vdda o{k}a TRACE len=4")
        n += series(f"fbo{k}", f"o{k}a", f"o{k}", v["filt"] if not v["per_chip"]
                    else v.get("pre", ("short",)))
        for j, c in enumerate(v.get("rail_caps", []) + v["chip_caps"] * 3):
            n.append(f"Xco{k}{j} o{k} 0 {CAPLIB[c]}")
        n.append(f"Io{k} o{k} 0 DC {I_RING}")

    # --- wstrzykniecie pradu AC w pin badanego ukladu (pomiar Z i K12) ---
    n.append(f"Iinj 0 {probe} AC 1")

    n += ["",
          ".control", "set noaskquit", "set nobreak", "op",
          "ac dec 60 1k 1g",
          f"let zp  = abs(v({probe}))",
          "let zr1 = abs(v(ring1))",
          "let zvd = abs(v(vdda))",
          "let k12 = abs(v(u8))",
          "set wr_singlescale", "set wr_vecnames",
          f'wrdata {OUT}/z_{v["tag"]}.dat zp zr1 zvd k12',
          ".endc", ".end"]
    return "\n".join(n) + "\n"


def build_tf(v):
    """Druga symulacja: transmitancja VDDA -> VCC ringu (zrodlo napieciowe
    na VDDA, bez LDO -- interesuje nas sam filtr, nie PSRR)."""
    src = build(v)
    src = src.replace("Xldo v5 0 v5 nc vdda AP2112K_33",
                      "Vdda vdda 0 DC 3.3 AC 1")
    src = src.replace("V5 v5 0 DC 5 AC 1", "V5 v5 0 DC 5 AC 0")
    src = src.replace("Iinj 0 u5 AC 1", "Iinj 0 u5 AC 0")
    src = src.replace(f'wrdata {OUT}/z_{v["tag"]}.dat zp zr1 zvd k12',
                      f'wrdata {OUT}/tf_{v["tag"]}.dat zp zr1 zvd k12')
    return src


# --------------------------------------------------------------------------
def read_dat(path):
    with open(path) as f:
        lines = [ln.split() for ln in f if ln.strip()]
    return lines[0], [[float(x) for x in ln] for ln in lines[1:]]


def at_freq(hdr, rows, col, f):
    j = hdr.index(col)
    prev = None
    for r in rows:
        if r[0] >= f:
            if prev is None:
                return r[j]
            t = (math.log(f) - math.log(prev[0])) / (math.log(r[0]) - math.log(prev[0]))
            return prev[j] + t * (r[j] - prev[j])
        prev = r
    return rows[-1][j]


def peak(hdr, rows, col, fmin=2e3, fmax=5e7):
    j = hdr.index(col)
    best = (0.0, 0.0)
    for i in range(1, len(rows) - 1):
        f = rows[i][0]
        if not (fmin <= f <= fmax):
            continue
        val = rows[i][j]
        if val >= rows[i - 1][j] and val >= rows[i + 1][j] and val > best[0]:
            best = (val, f)
    return best


def run_one(src, tag, kind):
    path = os.path.join(OUT, f"_{kind}_{tag}.cir")
    with open(path, "w") as f:
        f.write(src)
    r = subprocess.run([sys.executable, os.path.join(HERE, "ngrun.py"), path],
                       capture_output=True, text=True, timeout=1800)
    dat = os.path.join(OUT, f"{kind}_{tag}.dat")
    if not os.path.exists(dat):
        print(r.stdout[-4000:], r.stderr[-2000:])
        raise RuntimeError(f"{kind}/{tag}: brak wynikow")
    return read_dat(dat)


# --------------------------------------------------------------------------
HF = ["100n", "2n2"]
L_NOM = "0.95u"

VARIANTS = [
    dict(tag="a_asbuilt", desc="(a) JAK TERAZ: 1 x FB600 na ring, 100n+2n2 przy kazdym ukl.",
         per_chip=False, filt=("fb", L_NOM), chip_caps=HF, rail_caps=[]),

    dict(tag="a_4u7", desc="(a+) 1 x FB600 + 4u7 1206 na szynie ringu",
         per_chip=False, filt=("fb", L_NOM), chip_caps=HF, rail_caps=["4u7"]),

    dict(tag="a_10u", desc="(a+) 1 x FB600 + 10u 0805 na szynie ringu",
         per_chip=False, filt=("fb", L_NOM), chip_caps=HF, rail_caps=["10u"]),

    dict(tag="c_fb_r2_4u7", desc="(c) FB600 + R 2.2R + 4u7 na szynie ringu",
         per_chip=False, filt=("fbr", L_NOM, 2.2), chip_caps=HF, rail_caps=["4u7"]),

    dict(tag="c_fb_r4_4u7", desc="(c) FB600 + R 4.7R + 4u7 na szynie ringu",
         per_chip=False, filt=("fbr", L_NOM, 4.7), chip_caps=HF, rail_caps=["4u7"]),

    dict(tag="c_fb_r10_10u", desc="(c) FB600 + R 10R + 10u na szynie ringu",
         per_chip=False, filt=("fbr", L_NOM, 10), chip_caps=HF, rail_caps=["10u"]),

    dict(tag="c_r10_10u", desc="(c-) sam R 10R (bez ferrytu) + 10u na szynie",
         per_chip=False, filt=("r", 10), chip_caps=HF, rail_caps=["10u"]),

    dict(tag="c_fb_r2_1u", desc="(c) FB600 + R 2.2R + 1u 0603 na szynie ringu",
         per_chip=False, filt=("fbr", L_NOM, 2.2), chip_caps=HF, rail_caps=["1u"]),

    dict(tag="c_snub", desc="(c) FB600 + 4u7 + snubber (1R + 10u) na szynie",
         per_chip=False, filt=("fb", L_NOM), chip_caps=HF, rail_caps=["4u7"],
         snub_rail=True),

    dict(tag="b_perchip_fb", desc="(b) FB600 przy KAZDYM ukl. (3 na ring), 100n+2n2",
         per_chip=True, pre=("short",), filt=("fb", L_NOM), chip_caps=HF, rail_caps=[]),

    dict(tag="b_perchip_fbr1u", desc="(b) FB600 + R 2.2R + 1u przy KAZDYM ukl.",
         per_chip=True, pre=("short",), filt=("fbr", L_NOM, 2.2),
         chip_caps=HF + ["1u"], rail_caps=[]),

    dict(tag="b_perchip_r_only", desc="(b) tylko R 4.7R + 1u przy kazdym ukl., FB wspolny",
         per_chip=True, pre=("fb", L_NOM), filt=("r", 4.7),
         chip_caps=HF + ["1u"], rail_caps=["4u7"]),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    res = {}
    for v in VARIANTS:
        hz, rz = run_one(build(v), v["tag"], "z")
        ht, rt = run_one(build_tf(v), v["tag"], "tf")
        d = {}
        d["zmax"], d["zmaxf"] = peak(hz, rz, "zp")
        for f, t in ((F_SMPS, "smps"), (2 * F_SMPS, "smps2"), (3 * F_SMPS, "smps3")):
            d[f"tf_{t}"] = at_freq(ht, rt, "zp", f)
        d["tf_max"], d["tf_maxf"] = peak(ht, rt, "zp")
        d["z_fr"] = at_freq(hz, rz, "zp", F_RING)
        d["z_2fr"] = at_freq(hz, rz, "zp", 2 * F_RING)
        d["z_100k"] = at_freq(hz, rz, "zp", 1e5)
        d["k12_fr"] = at_freq(hz, rz, "k12", F_RING)
        d["k12_smps"] = at_freq(hz, rz, "k12", F_SMPS)
        res[v["tag"]] = d
        print(f"[{v['tag']}] ok")

    db = lambda x: 20 * math.log10(max(x, 1e-30))

    print("\n" + "=" * 122)
    print(" A. TRANSMITANCJA VDDA -> VCC RINGU  (wartosc DODATNIA w dB = filtr WZMACNIA zaklocenie)")
    print("=" * 122)
    print(f"{'wariant':22s} {'270kHz':>9s} {'540kHz':>9s} {'810kHz':>9s} "
          f"{'szczyt':>9s} {'@ f':>11s}   opis")
    print("-" * 122)
    for v in VARIANTS:
        d = res[v["tag"]]
        fm = f"{d['tf_maxf']/1e3:9.1f}k" if d["tf_maxf"] else "        -"
        print(f"{v['tag']:22s} {db(d['tf_smps']):+9.1f} {db(d['tf_smps2']):+9.1f} "
              f"{db(d['tf_smps3']):+9.1f} {db(d['tf_max']):+9.1f} {fm:>11s}   {v['desc']}")

    print("\n" + "=" * 122)
    print(" B. IMPEDANCJA PDN NA PINIE VCC UKLADU  [Ohm]  +  SPRZEZENIE MIEDZY RINGAMI")
    print("=" * 122)
    print(f"{'wariant':22s} {'Z@100k':>9s} {'Zmax':>9s} {'@ f':>11s} "
          f"{'Z@157M':>9s} {'Z@314M':>9s} {'K12@157M':>11s} {'K12@270k':>11s}")
    print("-" * 122)
    for v in VARIANTS:
        d = res[v["tag"]]
        fm = f"{d['zmaxf']/1e3:9.1f}k" if d["zmaxf"] else "        -"
        print(f"{v['tag']:22s} {d['z_100k']:9.3f} {d['zmax']:9.3f} {fm:>11s} "
              f"{d['z_fr']:9.3f} {d['z_2fr']:9.3f} "
              f"{d['k12_fr']:11.2e} {d['k12_smps']:11.2e}")

    print("\n" + "=" * 122)
    print(" C. SKUTEK DLA CZESTOTLIWOSCI RINGU  (dF/dVCC = 81.4 MHz/V)")
    print("    zalozenie: na VDDA zostaje 0.5 mV tetnienia 270 kHz po LDO (PSRR ~45 dB)")
    print("=" * 122)
    print(f"{'wariant':22s} {'mV na VCC ringu':>16s} {'dewiacja f':>14s} "
          f"{'spadek DC':>11s}")
    print("-" * 122)
    for v in VARIANTS:
        d = res[v["tag"]]
        mv = 0.5 * d["tf_smps"]
        dev = mv * 1e-3 * 81.4e6
        # spadek DC na filtrze
        f = v["filt"]
        rdc = 0.30
        if f[0] == "fbr":
            rdc += f[2]
        elif f[0] == "r":
            rdc = f[1]
        i = I_RING / 3 if v["per_chip"] else I_RING
        if v["per_chip"] and v.get("pre", ("short",))[0] == "fb":
            drop = 0.30 * I_RING + rdc * i
        else:
            drop = rdc * i
        print(f"{v['tag']:22s} {mv:16.3f} {dev/1e3:11.1f} kHz {drop*1e3:8.1f} mV")

    # --- wrazliwosc na indukcyjnosc ferrytu (nieznany numer katalogowy C1002) ---
    print("\n" + "=" * 122)
    print(" D. WRAZLIWOSC NA INDUKCYJNOSC FERRYTU (BOM podaje tylko '600@100MHz', LCSC C1002)")
    print("=" * 122)
    print(f"{'wariant':22s} " + " ".join(f"{f'L={l}':>12s}" for l in ("0.5u", "0.95u", "2.0u")))
    print("-" * 122)
    for base in ("a_asbuilt", "a_4u7", "c_fb_r2_4u7", "c_fb_r4_4u7", "b_perchip_fb"):
        v0 = next(v for v in VARIANTS if v["tag"] == base)
        cells = []
        for lval in ("0.5u", "0.95u", "2.0u"):
            v = dict(v0)
            v["tag"] = f"{base}_L{lval}"
            fl = list(v0["filt"])
            fl[1] = lval
            v["filt"] = tuple(fl)
            if v0.get("pre", ("short",))[0] == "fb":
                v["pre"] = ("fb", lval)
            ht, rt = run_one(build_tf(v), v["tag"], "tf")
            pk, pf = peak(ht, rt, "zp")
            cells.append(f"{db(pk):+6.1f}dB@{pf/1e3:.0f}k")
        print(f"{base:22s} " + " ".join(f"{c:>12s}" for c in cells))
    print("\n(szczyt transmitancji VDDA->ring; przetwornica pracuje przy 270 kHz,")
    print(" jej harmoniczne przy 540 / 810 / 1080 kHz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
