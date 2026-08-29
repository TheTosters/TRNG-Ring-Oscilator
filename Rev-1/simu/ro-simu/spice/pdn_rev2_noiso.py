#!/usr/bin/env python3
"""Rev-2 BEZ IZOLACJI GALWANICZNEJ -- dobor filtru zasilania ringow.

Topologia wg AKTUALNEGO schematu Rev-2/hardware/RO_hardware.kicad_sch
(netlista wyeksportowana kicad-cli), po uzupelnieniu trzech brakow
znalezionych w netliscie (patrz README / odpowiedz):

    USB1 VBUS --+-- FB2 (GZ1608D601TF) -- C3 1u -- U2 AP2112K -> VCC   (cyfra)
                |
                +-- C58 10u -- L2 22uH -- C2 1u -- U1 AP2112K -> VDDA (ringi)

    VDDA [C4 4u7 + C43/C7 100n + C42/C6 2n2] zasila:
        FB3..FB6  -> po 3 x 74LVC1G04 (ringi 1..4)
        FB7..FB10 -> po 1 x 74LVC3G04 (ringi 5..8)
        U3, U22   -> 2 x 74HC174  (BEZ zadnego filtru -- wprost z VDDA)

Zrodla zaklocen po usunieciu przetwornicy 270 kHz:
    * szerokopasmowy szum hosta na VBUS (nieznany, 10 kHz .. kilka MHz)
    * wlasna sekcja cyfrowa: STM32 na VCC (przez wspolny VBUS)
    * 2 x 74HC174 wprost na VDDA, przelaczajace sie z czestotliwoscia ringow

Kryterium doboru zmienia sie wzgledem wersji z przetwornica: nie ma juz
jednego tonu do wytlumienia, wiec liczy sie PLASKOSC -- brak jakiegokolwiek
rezonansu w calym pasmie, bo nie wiadomo, gdzie host bedzie mial swoje.
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
OUT = os.path.join(HERE, "out", "rev2_noiso")

F_RING = 157e6
I_RING = 24e-3       # jeden ring (3 bramki) @157 MHz
N_RING = 8
I_HC174 = 8e-3       # jeden 74HC174, wejscia przelaczane f_ring
I_MCU = 30e-3

CAP = {"100n": "C0402_100N", "2n2": "C0402_2N2", "1u": "C0603_1U",
       "4u7": "C0805_4U7", "10u": "C0805_10U", "1u6": "C1206_1U",
       "4u7b": "C1206_4U7", "10ub": "C1206_10U"}


def ser(name, a, b, spec):
    k = spec[0]
    if k == "short":
        return [f"R{name} {a} {b} 1m"]
    if k == "fb":
        return [f"X{name} {a} {b} GZ1608D601TF"]
    if k == "r":
        return [f"R{name} {a} {b} {spec[1]}"]
    if k == "fbr":
        return [f"X{name} {a} m{name} GZ1608D601TF", f"R{name} m{name} {b} {spec[1]}"]
    raise ValueError(k)


def build(v, mode):
    """mode: 'z' (Z i K12, prad w pin U5), 'vbus' (VBUS->ring),
             'dig' (prad cyfrowki na VCC -> ring), 'hc' (prad 74HC174 -> ring)"""
    n = ["* Rev-2 bez izolacji",
         f'.include "{MODELS}/AP2112K.lib"',
         f'.include "{MODELS}/passives.lib"', ""]

    # ---- kabel USB + VBUS ----
    ac5 = 1 if mode == "vbus" else 0
    n += [f"V5 v5s 0 DC 5 AC {ac5}",
          "Lusb v5s lu 500n", "Rusb lu vbus 0.2"]

    # ---- galaz cyfrowa: FB2 -> C3 -> U2 -> VCC ----
    n += ser("fb2", "vbus", "n2", ("fb",))
    n += [f"Xc3 n2 0 {CAP['1u6']}",
          "Xldo2 n2 0 n2 nc2 vcc AP2112K_33",
          f"Xc5 vcc 0 {CAP['4u7b']}",
          f"Xc46 vcc 0 {CAP['100n']}", f"Xc47 vcc 0 {CAP['100n']}",
          f"Xc48 vcc 0 {CAP['100n']}", f"Xc49 vcc 0 {CAP['100n']}",
          f"Xc53 vcc 0 {CAP['1u6']}", f"Xc55 vcc 0 {CAP['1u6']}",
          f"Imcu vcc 0 DC {I_MCU}"]

    # ---- galaz analogowa: C58 -> L2 -> C2 -> U1 -> VDDA ----
    n += [f"Xc58 vbus 0 {CAP['10ub']}",
          "Xl2 vbus n1 L1812_22U" + (f" rdc={v['l2r']}" if "l2r" in v else ""),
          f"Xc2 n1 0 {CAP['1u6']}"]
    if v.get("l2_damp"):                     # tlumik RC rownolegle do C2
        n += [f"Rdmp n1 dmp {v['l2_damp'][0]}",
              f"Xcdmp dmp 0 {CAP[v['l2_damp'][1]]}"]
    n += ["Xldo1 n1 0 n1 nc1 vdda AP2112K_33",
          f"Xc4 vdda 0 {CAP['4u7b']}",
          f"Xc43 vdda 0 {CAP['100n']}", f"Xc7 vdda 0 {CAP['100n']}",
          f"Xc42 vdda 0 {CAP['2n2']}", f"Xc6 vdda 0 {CAP['2n2']}"]
    for k, c in enumerate(v.get("vdda_extra", [])):
        n.append(f"Xvx{k} vdda 0 {CAP[c]}")

    # ---- 2 x 74HC174 na VDDA ----
    hcnode = "vdda"
    if v.get("hc_filt"):
        n += ser("fbhc", "vdda", "vhc", v["hc_filt"])
        for k, c in enumerate(v.get("hc_caps", ["100n", "1u"])):
            n.append(f"Xchc{k} vhc 0 {CAP[c]}")
        hcnode = "vhc"
    else:
        n += [f"Xchc0 {hcnode} 0 {CAP['100n']}"]
    achc = 1 if mode == "hc" else 0
    n += [f"Ihc {hcnode} 0 DC {2*I_HC174} AC {achc}"]

    # ---- 8 galezi ringow; 1 i 2 szczegolowo, reszta zbiorczo ----
    for br in (1, 2):
        rail = f"r{br}"
        n.append(f"Xtb{br} vdda p{rail} TRACE len=4")
        if v["per_chip"]:
            n += ser(f"pre{br}", f"p{rail}", rail, v.get("pre", ("short",)))
            for k, c in enumerate(v.get("rail_caps", [])):
                n.append(f"Xrc{br}{k} {rail} 0 {CAP[c]}")
            for g in range(3):
                nd = f"g{br}{g}"
                n.append(f"Xtg{br}{g} {rail} q{nd} TRACE len=3")
                n += ser(f"fg{br}{g}", f"q{nd}", nd, v["filt"])
                for k, c in enumerate(v["chip_caps"]):
                    n.append(f"Xcg{br}{g}{k} {nd} 0 {CAP[c]}")
                n.append(f"Ig{br}{g} {nd} 0 DC {I_RING/3}")
        else:
            n += ser(f"rf{br}", f"p{rail}", rail, v["filt"])
            for k, c in enumerate(v.get("rail_caps", [])):
                n.append(f"Xrc{br}{k} {rail} 0 {CAP[c]}")
            for g in range(3):
                nd = f"g{br}{g}"
                n.append(f"Xtg{br}{g} {rail} {nd} TRACE len=3")
                for k, c in enumerate(v["chip_caps"]):
                    n.append(f"Xcg{br}{g}{k} {nd} 0 {CAP[c]}")
                n.append(f"Ig{br}{g} {nd} 0 DC {I_RING/3}")
    n += ["Rp1 g10 u5 1u", "Rp2 g20 u8 1u"]

    for k in range(N_RING - 2):
        n.append(f"Xto{k} vdda o{k}a TRACE len=4")
        n += ser(f"fo{k}", f"o{k}a", f"o{k}",
                 v["filt"] if not v["per_chip"] else v.get("pre", ("short",)))
        for j, c in enumerate(list(v.get("rail_caps", [])) + v["chip_caps"] * 3):
            n.append(f"Xco{k}{j} o{k} 0 {CAP[c]}")
        n.append(f"Io{k} o{k} 0 DC {I_RING}")

    if mode == "z":
        n.append("Iinj 0 u5 AC 1")
    elif mode == "dig":
        n.append("Iinj 0 vcc AC 1")

    n += ["", ".control", "set noaskquit", "set nobreak", "op",
          "ac dec 40 1k 1g",
          "let vu5 = abs(v(u5))", "let vu8 = abs(v(u8))",
          "let vda = abs(v(vdda))", "let vbu = abs(v(vbus))",
          "set wr_singlescale", "set wr_vecnames",
          f'wrdata {OUT}/{mode}_{v["tag"]}.dat vu5 vu8 vda vbu',
          ".endc", ".end"]
    return "\n".join(n) + "\n"


def read_dat(p):
    with open(p) as f:
        L = [ln.split() for ln in f if ln.strip()]
    return L[0], [[float(x) for x in ln] for ln in L[1:]]


def at(hdr, rows, col, f):
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
        if val >= rows[i-1][j] and val >= rows[i+1][j] and val > best[0]:
            best = (val, f)
    return best


def run(v, mode):
    src = build(v, mode)
    path = os.path.join(OUT, f"_{mode}_{v['tag']}.cir")
    with open(path, "w") as f:
        f.write(src)
    r = subprocess.run([sys.executable, os.path.join(HERE, "ngrun.py"), path],
                       capture_output=True, text=True, timeout=1800)
    dat = os.path.join(OUT, f"{mode}_{v['tag']}.dat")
    if not os.path.exists(dat):
        print(r.stdout[-4000:]); raise RuntimeError(f"{mode}/{v['tag']}")
    return read_dat(dat)


HF = ["100n", "2n2"]
VARIANTS = [
    dict(tag="A_asbuilt", desc="A. JAK TERAZ: FB600 na ring, 3x(100n+2n2)",
         per_chip=False, filt=("fb",), chip_caps=HF, rail_caps=[]),
    dict(tag="B_perchip_fb", desc="B. FB600 przy KAZDYM ukladzie (3 na ring)",
         per_chip=True, pre=("short",), filt=("fb",), chip_caps=HF, rail_caps=[]),
    dict(tag="C_fb_4u7", desc="C. FB600 + 4u7 na szynie ringu",
         per_chip=False, filt=("fb",), chip_caps=HF, rail_caps=["4u7"]),
    dict(tag="D_fb_r2_4u7", desc="D. FB600 + R 2.2R + 4u7 na szynie ringu",
         per_chip=False, filt=("fbr", 2.2), chip_caps=HF, rail_caps=["4u7"]),
    dict(tag="E_fb_r4_4u7", desc="E. FB600 + R 4.7R + 4u7 na szynie ringu",
         per_chip=False, filt=("fbr", 4.7), chip_caps=HF, rail_caps=["4u7"]),
    dict(tag="F_fb_r2_10u", desc="F. FB600 + R 2.2R + 10u na szynie ringu",
         per_chip=False, filt=("fbr", 2.2), chip_caps=HF, rail_caps=["10u"]),
    dict(tag="G_perchip_fbr", desc="G. FB600 + R 2.2R + 1u przy KAZDYM ukladzie",
         per_chip=True, pre=("short",), filt=("fbr", 2.2),
         chip_caps=HF + ["1u"], rail_caps=[]),
    dict(tag="H_full", desc="H. D + filtr na 74HC174 + 10u na VDDA",
         per_chip=False, filt=("fbr", 2.2), chip_caps=HF, rail_caps=["4u7"],
         hc_filt=("fbr", 4.7), hc_caps=["100n", "1u"], vdda_extra=["10u"]),
    dict(tag="I_full_damp", desc="H + tlumik RC (2.2R+10u) na wejsciu U1",
         per_chip=False, filt=("fbr", 2.2), chip_caps=HF, rail_caps=["4u7"],
         hc_filt=("fbr", 4.7), hc_caps=["100n", "1u"], vdda_extra=["10u"],
         l2_damp=(2.2, "10ub")),
]

db = lambda x: 20 * math.log10(max(x, 1e-30))


def main():
    os.makedirs(OUT, exist_ok=True)
    R = {}
    for v in VARIANTS:
        d = {}
        hz, rz = run(v, "z")
        d["zmax"], d["zmaxf"] = peak(hz, rz, "vu5")
        d["z157"] = at(hz, rz, "vu5", F_RING)
        d["k12_100k"] = at(hz, rz, "vu8", 1e5)
        d["k12_1m"] = at(hz, rz, "vu8", 1e6)
        d["k12_157m"] = at(hz, rz, "vu8", F_RING)
        hb, rb = run(v, "vbus")
        d["vb_pk"], d["vb_pkf"] = peak(hb, rb, "vu5")
        for f, t in ((1e4, "10k"), (1e5, "100k"), (1e6, "1m")):
            d[f"vb_{t}"] = at(hb, rb, "vu5", f)
        hd, rd = run(v, "dig")
        d["dg_pk"], d["dg_pkf"] = peak(hd, rd, "vu5")
        d["dg_100k"] = at(hd, rd, "vu5", 1e5)
        hh, rh = run(v, "hc")
        d["hc_pk"], d["hc_pkf"] = peak(hh, rh, "vu5")
        d["hc_1m"] = at(hh, rh, "vu5", 1e6)
        R[v["tag"]] = d
        print(f"[{v['tag']}] ok")

    print("\n" + "=" * 120)
    print(" 1. TOR: VBUS (szum hosta) -> VCC RINGU   [dB, wzgledem napiecia na VBUS]")
    print("=" * 120)
    print(f"{'wariant':16s} {'10kHz':>9s} {'100kHz':>9s} {'1MHz':>9s} "
          f"{'szczyt':>9s} {'@ f':>11s}   opis")
    print("-" * 120)
    for v in VARIANTS:
        d = R[v["tag"]]
        fm = f"{d['vb_pkf']/1e3:9.1f}k" if d["vb_pkf"] else "        -"
        print(f"{v['tag']:16s} {db(d['vb_10k']):+9.1f} {db(d['vb_100k']):+9.1f} "
              f"{db(d['vb_1m']):+9.1f} {db(d['vb_pk']):+9.1f} {fm:>11s}   {v['desc']}")

    print("\n" + "=" * 120)
    print(" 2. IMPEDANCJA PDN NA PINIE VCC RINGU + SPRZEZENIE RING1 -> RING2")
    print("=" * 120)
    print(f"{'wariant':16s} {'Zmax':>9s} {'@ f':>11s} {'Z@157M':>9s} "
          f"{'K12@100k':>11s} {'K12@1M':>11s} {'K12@157M':>11s}")
    print("-" * 120)
    for v in VARIANTS:
        d = R[v["tag"]]
        fm = f"{d['zmaxf']/1e3:9.1f}k" if d["zmaxf"] else "        -"
        print(f"{v['tag']:16s} {d['zmax']:9.3f} {fm:>11s} {d['z157']:9.3f} "
              f"{d['k12_100k']:11.2e} {d['k12_1m']:11.2e} {d['k12_157m']:11.2e}")

    print("\n" + "=" * 120)
    print(" 3. ZAKLOCENIA WLASNE PLYTKI -> VCC RINGU  [Ohm: ile mV na 1 mA zaklocenia]")
    print("=" * 120)
    print(f"{'wariant':16s} {'MCU/VCC szczyt':>15s} {'@ f':>11s} "
          f"{'74HC174 szczyt':>15s} {'@ f':>11s} {'74HC174@1M':>12s}")
    print("-" * 120)
    for v in VARIANTS:
        d = R[v["tag"]]
        f1 = f"{d['dg_pkf']/1e3:9.1f}k" if d["dg_pkf"] else "        -"
        f2 = f"{d['hc_pkf']/1e3:9.1f}k" if d["hc_pkf"] else "        -"
        print(f"{v['tag']:16s} {d['dg_pk']:15.4f} {f1:>11s} "
              f"{d['hc_pk']:15.4f} {f2:>11s} {d['hc_1m']:12.4f}")

    print("\n" + "=" * 120)
    print(" 4. PRZELICZENIE NA DEWIACJE CZESTOTLIWOSCI (dF/dVCC = 81.4 MHz/V)")
    print("    zalozenia: 50 mV szumu hosta na VBUS; 1 mA zaklocenia z 74HC174")
    print("=" * 120)
    print(f"{'wariant':16s} {'od hosta (szczyt)':>19s} {'od 74HC174 (szczyt)':>21s} "
          f"{'spadek DC na filtrze':>21s}")
    print("-" * 120)
    for v in VARIANTS:
        d = R[v["tag"]]
        dev_h = 0.050 * d["vb_pk"] * 81.4e6
        dev_c = 1e-3 * d["hc_pk"] * 81.4e6
        rdc = 0.45
        if v["filt"][0] == "fbr":
            rdc += v["filt"][1]
        elif v["filt"][0] == "r":
            rdc = v["filt"][1]
        i = I_RING / 3 if v["per_chip"] else I_RING
        drop = rdc * i + (0.45 * I_RING if v["per_chip"] and
                          v.get("pre", ("short",))[0] == "fb" else 0)
        print(f"{v['tag']:16s} {dev_h/1e3:16.1f} kHz {dev_c/1e3:18.1f} kHz "
              f"{drop*1e3:18.1f} mV")
    return 0


if __name__ == "__main__":
    sys.exit(main())
