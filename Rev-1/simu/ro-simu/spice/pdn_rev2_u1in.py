#!/usr/bin/env python3
"""Rev-2: weryfikacja stopnia wejsciowego U1 (LDO zasilajacego VDDA).

Topologia zgloszona przez uzytkownika:
    VBUS -- R 4.7 -- [C61 4u7 + C58 10u] -- L2 22uH -- [C2 1u] -- VIN(U1)
    VOUT(U1) -- C4 4u7 -- VDDA -> 8 x (FB + R + bulk) -> ringi

Sprawdzane:
  1. punkt pracy DC: spadek na R, moc w R, margines dropout LDO przy
     dolnej granicy napiecia VBUS wg specyfikacji USB
  2. tor VBUS -> VCC ringu (czy filtr robi to, co ma robic)
  3. warianty: R szeregowy vs tlumik RC do masy
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
OUT = os.path.join(HERE, "out", "rev2_u1in")

I_RING = 24e-3
N_RING = 8
I_VDDA = I_RING * N_RING          # 192 mA
R_RING = 4.7                      # rezystor filtru ringu (zalecony wczesniej)
F_RING = 157e6

CAP = {"100n": "C0402_100N", "2n2": "C0402_2N2", "1u": "C0603_1U",
       "4u7": "C0805_4U7", "10u": "C0805_10U", "1u6": "C1206_1U",
       "4u7b": "C1206_4U7", "10ub": "C1206_10U"}


def build(v, mode, vbus=5.0):
    ac = 1 if mode == "ac" else 0
    n = ["* stopien wejsciowy U1",
         f'.include "{MODELS}/AP2112K.lib"',
         f'.include "{MODELS}/passives.lib"', "",
         f"V5 v5s 0 DC {vbus} AC {ac}",
         "Lusb v5s lu 500n", "Rusb lu vbus 0.05"]

    # --- R szeregowy (albo zwarcie) ---
    rs = v.get("rs", 0)
    if rs:
        n.append(f"Rser vbus na {rs}")
    else:
        n.append("Rser vbus na 1m")
    # --- C61 + C58 na wezle A ---
    for k, c in enumerate(v.get("ca", ["4u7b", "10ub"])):
        n.append(f"Xca{k} na 0 {CAP[c]}")
    # --- L2 ---
    n.append("Xl2 na nb L1812_22U")
    # --- C2 na wejsciu LDO ---
    n.append(f"Xc2 nb 0 {CAP['1u6']}")
    # --- opcjonalny tlumik RC do masy na wejsciu LDO ---
    if v.get("damp"):
        n.append(f"Rdmp nb dmp {v['damp'][0]}")
        n.append(f"Xcdmp dmp 0 {CAP[v['damp'][1]]}")
    # --- LDO + wyjscie ---
    n += ["Xldo nb 0 nb nc vdda AP2112K_33",
          f"Xc4 vdda 0 {CAP['4u7b']}",
          f"Xc43 vdda 0 {CAP['100n']}", f"Xc42 vdda 0 {CAP['2n2']}"]

    # --- 8 galezi ringow: FB + R + 10u + 3x(100n+2n2) ---
    for br in range(N_RING):
        nd = f"r{br}"
        n.append(f"Xt{br} vdda p{nd} TRACE len=4")
        n.append(f"Xfb{br} p{nd} m{nd} GZ1608D601TF")
        n.append(f"Rr{br} m{nd} {nd} {R_RING}")
        n.append(f"Xcb{br} {nd} 0 {CAP['10u']}")
        for g in range(3):
            n.append(f"Xch{br}{g}a {nd} 0 {CAP['100n']}")
            n.append(f"Xch{br}{g}b {nd} 0 {CAP['2n2']}")
        n.append(f"I{br} {nd} 0 DC {I_RING}")

    n += ["", ".control", "set noaskquit", "set nobreak", "op"]
    if mode == "dc":
        n += ["print v(vbus) v(na) v(nb) v(vdda) v(r0)",
              f'wrdata {OUT}/dc_{v["tag"]}_{int(vbus*100)}.dat v(nb) v(vdda) v(r0)']
    else:
        n += ["ac dec 50 100 1g",
              "let vr = abs(v(r0))", "let vd = abs(v(vdda))",
              "let vb = abs(v(vbus))", "let vn = abs(v(nb))",
              "set wr_singlescale", "set wr_vecnames",
              f'wrdata {OUT}/ac_{v["tag"]}.dat vr vd vb vn']
    n += [".endc", ".end"]
    return "\n".join(n) + "\n"


def run(v, mode, vbus=5.0):
    src = build(v, mode, vbus)
    p = os.path.join(OUT, f"_{mode}_{v['tag']}_{int(vbus*100)}.cir")
    with open(p, "w") as f:
        f.write(src)
    r = subprocess.run([sys.executable, os.path.join(HERE, "ngrun.py"), p],
                       capture_output=True, text=True, timeout=1800)
    return r.stdout


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


def peak(h, r, c, fmin=2e2, fmax=5e7):
    j = h.index(c); best = (0.0, 0.0)
    for i in range(1, len(r) - 1):
        f = r[i][0]
        if not (fmin <= f <= fmax):
            continue
        if r[i][j] >= r[i-1][j] and r[i][j] >= r[i+1][j] and r[i][j] > best[0]:
            best = (r[i][j], f)
    return best


db = lambda x: 20 * math.log10(max(x, 1e-30))

VARIANTS = [
    ("R 4.7 szeregowo (obecnie)", dict(tag="rs47", rs=4.7)),
    ("R 2.2 szeregowo",           dict(tag="rs22", rs=2.2)),
    ("R 1.0 szeregowo",           dict(tag="rs10", rs=1.0)),
    ("bez R szeregowego",         dict(tag="rs00", rs=0)),
    ("bez R + tlumik RC 4.7R+4u7 do masy",
     dict(tag="dmp", rs=0, damp=(4.7, "4u7b"))),
    ("R 1.0 szeregowo + tlumik RC 4.7R+4u7",
     dict(tag="rs10d", rs=1.0, damp=(4.7, "4u7b"))),
]


def main():
    os.makedirs(OUT, exist_ok=True)

    # ---------------- 1. punkt pracy DC ----------------
    print("=" * 112)
    print(f" PUNKT PRACY DC   (I_VDDA = {I_VDDA*1e3:.0f} mA = 8 ringow x {I_RING*1e3:.0f} mA)")
    print("=" * 112)
    print(f"{'wariant':38s} {'VBUS':>6s} {'VIN LDO':>9s} {'VDDA':>8s} "
          f"{'VCC ringu':>10s} {'moc w R':>9s} {'dropout':>9s}")
    print("-" * 112)
    import re
    for name, v in VARIANTS:
        for vbus in (5.00, 4.75, 4.40):
            out = run(v, "dc", vbus)
            m = re.search(r"v\(vbus\)\s*=\s*([-\d.e+]+)", out)
            vals = dict(re.findall(r"v\((\w+)\)\s*=\s*([-\d.e+]+)", out))
            if not vals:
                print(out[-1500:]); raise RuntimeError(name)
            vin = float(vals["nb"]); vdda = float(vals["vdda"]); vr = float(vals["r0"])
            pr = (float(vals.get("na", vbus)) - vbus) ** 2
            rs = v.get("rs", 0)
            pw = I_VDDA ** 2 * rs
            marg = vin - vdda
            flag = "  <-- DROPOUT" if vdda < 3.20 else ""
            lbl = name if vbus == 5.00 else ""
            print(f"{lbl:38s} {vbus:6.2f} {vin:9.3f} {vdda:8.3f} {vr:10.3f} "
                  f"{pw*1e3:7.0f} mW {marg:8.3f}{flag}")
        print()

    # ---------------- 2. tor VBUS -> ring ----------------
    print("=" * 112)
    print(" TOR VBUS -> VCC RINGU  [dB]   (filtr ringu: FB + 4.7R + 10uF)")
    print("=" * 112)
    print(f"{'wariant':38s} {'1kHz':>8s} {'10kHz':>8s} {'30kHz':>8s} "
          f"{'100kHz':>8s} {'1MHz':>8s} {'szczyt':>9s} {'@ f':>10s}")
    print("-" * 112)
    for name, v in VARIANTS:
        run(v, "ac")
        h, r = rd(os.path.join(OUT, f"ac_{v['tag']}.dat"))
        pk, pf = peak(h, r, "vr")
        fm = f"{pf/1e3:8.2f}k" if pf else "       -"
        print(f"{name:38s} {db(at(h,r,'vr',1e3)):+8.1f} "
              f"{db(at(h,r,'vr',1e4)):+8.1f} {db(at(h,r,'vr',3e4)):+8.1f} "
              f"{db(at(h,r,'vr',1e5)):+8.1f} {db(at(h,r,'vr',1e6)):+8.1f} "
              f"{db(pk):+9.1f} {fm:>10s}")
    print("-" * 112)
    print("szczyt = najwyzszy lokalny rezonans w torze (200 Hz .. 50 MHz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
