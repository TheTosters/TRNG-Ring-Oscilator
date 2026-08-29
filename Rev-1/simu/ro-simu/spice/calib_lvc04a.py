#!/usr/bin/env python3
"""Kalibracja modelu 74LVC04A do danych katalogowych.

Dwa cele jednoczesnie:
    t_pd = 2.0 ns  @ CL = 50 pF, RL = 500 R, VM = 1.5 V   (Tab.7/8)
    C_PD = 9.9 pF  @ wyjscie nieobciazone                 (Tab.7)

Dwie zmienne decyzyjne:
    k  -- skala szerokosci wszystkich tranzystorow (sila stopni)
    m  -- skala pojemnosci wezlow wewnetrznych + cself

Rozwiazanie metoda Newtona na siatce 2x2 (numeryczny jakobian).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# bazowe (nominalne) wymiary z modelu
BASE = dict(wn3=66.7e-6, wp3=175e-6, wn2=7.4e-6, wp2=19.4e-6, wn1=2.2e-6, wp1=5.8e-6)
BASE_C = dict(c1=1.0e-12, c2=3.0e-12, cself=2.5e-12)

TPL = r"""* auto-generated calibration run
.include {models}/74LVC04A.lib
.subckt DUT IN OUT VCC GND
Xg IN OUT VCC GND LVC04A_GATE
+ wn3={wn3:.6e} wp3={wp3:.6e} wn2={wn2:.6e} wp2={wp2:.6e} wn1={wn1:.6e} wp1={wp1:.6e}
+ c1={c1:.6e} c2={c2:.6e} cself={cself:.6e}
.ends

Vcc1  vdd1 0 DC 3.3
Vin1  in1  0 PULSE(0 2.7 20n 2.5n 2.5n 250n 500n)
X1    in1 out1 vdd1 0 DUT
Cl1   out1 0 50p
Rl1   out1 0 500

Vcc2  vdd2 0 DC 3.3
Vin2  in2  0 PULSE(0 3.3 20n 2n 2n 498n 1000n)
X2    in2 out2 vdd2 0 DUT

.control
set noaskquit
tran 2p 1.05u 0 2p
meas tran tphl trig v(in1) val=1.5 rise=1 targ v(out1) val=1.5 fall=1
meas tran tplh trig v(in1) val=1.5 fall=1 targ v(out1) val=1.5 rise=1
meas tran qcc integ i(Vcc2) from=50n to=1050n
.endc
.end
"""


def run(k, m, tag="cal"):
    p = {kk: vv * k for kk, vv in BASE.items()}
    p.update({kk: vv * m for kk, vv in BASE_C.items()})
    src = TPL.format(models=os.path.join(HERE, "models"), **p)
    path = os.path.join(HERE, "out", f"_{tag}.cir")
    with open(path, "w") as f:
        f.write(src)
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "ngrun.py"), path],
        capture_output=True, text=True, timeout=900,
    ).stdout
    def grab(name):
        mm = re.search(rf"^{name}\s*=\s*(\S+)", out, re.M)
        if not mm:
            raise RuntimeError(f"brak {name} w wyniku:\n{out[-2000:]}")
        return float(mm.group(1))
    tphl, tplh, qcc = grab("tphl"), grab("tplh"), grab("qcc")
    tpd = 0.5 * (tphl + tplh)
    cpd = abs(qcc) / 1e-6 / (3.3 * 1e6)
    return tpd, cpd


def main():
    k, m = 1.0, 1.0
    tgt_tpd, tgt_cpd = 2.0e-9, 9.9e-12
    print(f"{'iter':>4} {'k':>8} {'m':>8} {'tpd[ns]':>9} {'cpd[pF]':>9}")
    for it in range(12):
        tpd, cpd = run(k, m, "c0")
        print(f"{it:>4} {k:8.4f} {m:8.4f} {tpd*1e9:9.3f} {cpd*1e12:9.3f}")
        e1 = (tpd - tgt_tpd) / tgt_tpd
        e2 = (cpd - tgt_cpd) / tgt_cpd
        if abs(e1) < 0.02 and abs(e2) < 0.02:
            print(f"\nZBIEZNE: k={k:.4f} m={m:.4f}")
            print("Szerokosci do wstawienia w model:")
            for kk, vv in BASE.items():
                print(f"   {kk} = {vv*k*1e6:.3f}u")
            for kk, vv in BASE_C.items():
                print(f"   {kk} = {vv*m*1e12:.3f}p")
            return 0
        # jakobian numeryczny
        d = 0.08
        t_k, c_k = run(k * (1 + d), m, "ck")
        t_m, c_m = run(k, m * (1 + d), "cm")
        j11 = ((t_k - tpd) / tgt_tpd) / (d * k)
        j12 = ((t_m - tpd) / tgt_tpd) / (d * m)
        j21 = ((c_k - cpd) / tgt_cpd) / (d * k)
        j22 = ((c_m - cpd) / tgt_cpd) / (d * m)
        det = j11 * j22 - j12 * j21
        if abs(det) < 1e-12:
            print("jakobian zdegenerowany")
            return 1
        dk = (-e1 * j22 + e2 * j12) / det
        dm = (-e2 * j11 + e1 * j21) / det
        # ograniczenie kroku
        dk = max(-0.4 * k, min(0.6 * k, dk))
        dm = max(-0.4 * m, min(0.6 * m, dm))
        k, m = max(0.05, k + dk), max(0.05, m + dm)
    print("brak zbieznosci w limicie iteracji")
    return 1


if __name__ == "__main__":
    sys.exit(main())
