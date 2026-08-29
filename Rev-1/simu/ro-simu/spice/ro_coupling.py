#!/usr/bin/env python3
"""Sprzezenie miedzy ringami przez zasilanie -- analiza transientowa.

Porownuje trzy przypadki, kazdy z pelna siecia zasilania (LDO + ferryty +
kondensatory z ESR/ESL):

  A) 'hex_asbuilt'  -- 2 ringi w JEDNYM ukladzie 74LVC04A (tak jest na
                       realnej plytce Rev-1), filtr jak zmontowany:
                       ferryt BLM18AG102SN1D + 100n + 2n2
  B) 'hex_improved' -- to samo, ale z dodanym 1 uF przy ukladzie
  C) 'split'        -- 2 ringi w DWOCH osobnych ukladach, kazdy z wlasnym
                       ferrytem (tak zaklada schemat w simu/ro-simu)

Ringi sa celowo rozstrojone (rozne pojemnosci wezla), zeby dalo sie zobaczyc
czy sprzezenie przez zasilanie sciaga ich czestotliwosci do siebie
(wciaganie / injection pulling) -- dla TRNG to najgorszy mozliwy efekt,
bo koreluje kanaly ktore mialy byc niezalezne.

Mierzone:
  f1, f2       -- srednie czestotliwosci obu ringow
  tetnienie    -- amplituda miedzyszczytowa na wewnetrznej szynie zasilania
  jitter okresu-- odchylenie standardowe okresu kazdego ringu
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
OUT = os.path.join(HERE, "out")

TSTOP = 1.2e-6
TSTART = 0.3e-6          # okno pomiarowe -- po wygaszeniu transientu startowego
MAXSTEP = 2e-12

# rozstrojenie: ring B dostaje 0.6 pF wiecej na jednym wezle
CXA = "1f"
CXB = "0.6p"


def pdn(chip_extra_caps, tag, split):
    """Wspolna czesc: LDO + VCC + FB2 + VCCQ + odgalezienia do ukladow."""
    n = [
        "* auto-generated ring coupling study",
        f'.include "{MODELS}/AP2112K.lib"',
        f'.include "{MODELS}/passives.lib"',
        f'.include "{MODELS}/74LVC04A.lib"',
        "",
        "* Zasilanie szyny VCC: idealne 3.3 V + rezystancja/indukcyjnosc",
        "* odpowiadajaca wyjsciu LDO. Pelny model AP2112 jest uzyty w analizie",
        "* AC (pdn_study.py); w tranzjencie zastapiony, bo jego nieliniowosci",
        "* (min(), dioda podlozowa) rozwalaja zbieznosc przy krokach 2 ps, a",
        "* powyzej ~260 kHz i tak nie wnosi nic poza Cout -- czyli dokladnie w",
        "* pasmie ktore tu badamy (159 MHz) jest calkowicie przezroczysty.",
        "Vldo vldo 0 DC 3.3",
        "Rldo vldo rl_ 0.023",
        "Lldo rl_  vcc 2n",
        "Xc9  vcc 0 C0603_100N",
        "Xc10 vcc 0 C0805_1U",
        "Xc14 vcc 0 C0402_100N",
        "Xc15 vcc 0 C0402_2N2",
        "Idig vcc 0 DC 25m",
        "Xtr1 vcc vccqa TRACE len=8",
        "Xfb2 vccqa vccq BLM18AG102SN1D",
        "Xq0 vccq 0 C0805_10U",
        "Xq1 vccq 0 C0603_1U",
        "Xq2 vccq 0 C0603_100N",
    ]
    branches = ["vu1", "vu2"] if split else ["vu1"]
    for k, chip in enumerate(branches, start=3):
        n.append(f"Xtr{k} vccq q{chip} TRACE len=5")
        n.append(f"Xfb{k} q{chip} {chip} BLM18AG102SN1D")
        n.append(f"Xca{chip} {chip} 0 C0402_100N")
        n.append(f"Xcb{chip} {chip} 0 C0402_2N2")
        for i, c in enumerate(chip_extra_caps):
            n.append(f"Xce{chip}{i} {chip} 0 {c}")
    return n, branches


def build(case):
    split = case["split"]
    n, branches = pdn(case["extra"], case["tag"], split)
    if split:
        # dwa osobne uklady, kazdy z jednym ringiem
        pp = case.get("pkg", "")
        n.append(f"Xu1 tap1 vu1 0 LVC04A_HEX_1RING cxa={CXA} {pp}")
        n.append(f"Xu2 tap2 vu2 0 LVC04A_HEX_1RING cxa={CXB} {pp}")
        dienodes = ["v(xu1.vda)", "v(xu2.vda)"]
        ringnodes = ["v(xu1.a1)", "v(xu2.a1)"]
    else:
        # jeden uklad hex z dwoma ringami (realna plytka)
        pp = case.get("pkg", "")
        n.append(f"Xu1 tap1 tap2 vu1 0 LVC04A_HEX_2RING cxa={CXA} cxb={CXB} {pp}")
        dienodes = ["v(xu1.vda)", "v(xu1.vdb)"]
        ringnodes = ["v(xu1.a1)", "v(xu1.b1)"]
    # obciazenie wyjsc: wejscie 74HC174 + sciezka
    n.append("Ct1 tap1 0 5p")
    n.append("Ct2 tap2 0 5p")
    # warunki startowe (rozrywaja symetrie ringu)
    if split:
        n.append(".ic v(xu1.a1)=0 v(xu1.a2)=3.3 v(xu1.a3)=0")
        n.append(".ic v(xu2.a1)=0 v(xu2.a2)=3.3 v(xu2.a3)=0")
    else:
        n.append(".ic v(xu1.a1)=0 v(xu1.a2)=3.3 v(xu1.a3)=0")
        n.append(".ic v(xu1.b1)=0 v(xu1.b2)=3.3 v(xu1.b3)=0")
    n += [
        "",
        "* cshunt: mala pojemnosc z kazdego wezla do masy -- niezbedna przy",
        "* sieci z ferrytami (2.2 uH), inaczej tranzjent sie rozjezdza na",
        "* wezlach ktore nie maja wlasnej pojemnosci.",
        ".options cshunt=1f gmin=1e-12 reltol=1e-3 trtol=7 method=gear",
        "",
        ".control",
        "set noaskquit",
        "set nobreak",
        f"tran {MAXSTEP:.3e} {TSTOP:.3e} 0 {MAXSTEP:.3e}",
        "set wr_singlescale",
        "set wr_vecnames",
        f"wrdata {OUT}/cpl_{case['tag']}.dat {ringnodes[0]} {ringnodes[1]} "
        f"{dienodes[0]} {dienodes[1]} v(vu1) v(vccq)",
        ".endc",
        ".end",
    ]
    return "\n".join(n) + "\n"


def crossings(t, v, thr, lo=0.6):
    """Czasy przejsc rosnacych przez prog, z HISTEREZA.

    Histereza jest konieczna: wezel pomiarowy ma indukcyjnosc wyprowadzenia
    (2 nH) i pojemnosc obciazenia (5 pF), wiec zbocze o czasie 0.35 ns
    wywoluje dzwonienie ~1.6 GHz. Bez histerezy detektor liczy kilka przejsc
    na okres i zawyza czestotliwosc.
    """
    out = []
    armed = True
    for i in range(1, len(t)):
        if armed and v[i - 1] < thr <= v[i]:
            f = (thr - v[i - 1]) / (v[i] - v[i - 1])
            out.append(t[i - 1] + f * (t[i] - t[i - 1]))
            armed = False
        elif not armed and v[i] < lo:
            armed = True
    return out


def stats(xs):
    if len(xs) < 2:
        return float("nan"), float("nan")
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return m, math.sqrt(var)


def analyse(path):
    with open(path) as f:
        lines = [ln.split() for ln in f if ln.strip()]
    hdr, rows = lines[0], [[float(x) for x in ln] for ln in lines[1:]]
    col = {h: i for i, h in enumerate(hdr)}
    t = [r[0] for r in rows]
    sel = [i for i, tt in enumerate(t) if tt >= TSTART]
    ts = [t[i] for i in sel]

    def c(name):
        return [rows[i][col[name]] for i in sel]

    res = {}
    tap1, tap2 = c(hdr[1]), c(hdr[2])
    die1, die2 = c(hdr[3]), c(hdr[4])
    vu1, vccq = c(hdr[5]), c(hdr[6])

    for label, sig in (("1", tap1), ("2", tap2)):
        cr = crossings(ts, sig, 1.65)
        per = [cr[i + 1] - cr[i] for i in range(len(cr) - 1)]
        m, sd = stats(per)
        res[f"f{label}"] = 1 / m if m == m and m else float("nan")
        res[f"jit{label}"] = sd
        res[f"n{label}"] = len(per)

    for label, sig in (("die1", die1), ("die2", die2), ("vu1", vu1), ("vccq", vccq)):
        res[f"{label}_pp"] = max(sig) - min(sig)
        res[f"{label}_avg"] = sum(sig) / len(sig)
    return res


CASES = [
    dict(tag="hex_asbuilt", split=False, extra=[],
         desc="2 ringi w JEDNYM ukladzie (realna plytka), filtr jak zmontowany"),
    dict(tag="hex_improved", split=False, extra=["C0603_1U"],
         desc="2 ringi w JEDNYM ukladzie, dodane 1 uF przy ukladzie"),
    dict(tag="split", split=True, extra=[],
         desc="2 ringi w DWOCH osobnych ukladach (zalozenie schematu simu)"),
    dict(tag="hex_optimistic", split=False, extra=["C0603_1U"],
         pkg="lpin=1n cdie=1n rrail=0.15",
         desc="2 ringi w jednym ukladzie, SKRAJNIE optymistyczne pasozyty"
              " obudowy (lpin 1nH, cdie 1nF) -- test odpornosci wniosku"),
    dict(tag="split_optimistic", split=True, extra=["C0603_1U"],
         pkg="lpin=1n cdie=1n rrail=0.15",
         desc="jak wyzej, ale ringi w osobnych ukladach"),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[1:] or None
    results = []
    for case in CASES:
        if only and case["tag"] not in only:
            continue
        src = build(case)
        p = os.path.join(OUT, f"_cpl_{case['tag']}.cir")
        with open(p, "w") as f:
            f.write(src)
        print(f"[{case['tag']}] symulacja ...", flush=True)
        r = subprocess.run([sys.executable, os.path.join(HERE, "ngrun.py"), p],
                           capture_output=True, text=True, timeout=7200)
        dat = os.path.join(OUT, f"cpl_{case['tag']}.dat")
        if not os.path.exists(dat):
            print(r.stdout[-3000:])
            raise RuntimeError(f"{case['tag']}: brak wynikow")
        results.append((case, analyse(dat)))
        print(f"[{case['tag']}] ok", flush=True)

    print("\n" + "=" * 100)
    print(" SPRZEZENIE MIEDZY RINGAMI PRZEZ SIEC ZASILANIA")
    print("=" * 100)
    print(f"{'przypadek':15s} {'f_ring1':>11s} {'f_ring2':>11s} {'|df|':>10s} "
          f"{'jit1':>8s} {'jit2':>8s} {'Vdie1 pp':>9s} {'Vdie2 pp':>9s} {'Vpin pp':>8s}")
    print("-" * 100)
    for case, r in results:
        df = abs(r["f1"] - r["f2"])
        print(f"{case['tag']:15s} {r['f1']/1e6:9.3f}MHz {r['f2']/1e6:9.3f}MHz "
              f"{df/1e6:8.3f}MHz {r['jit1']*1e12:7.2f}p {r['jit2']*1e12:7.2f}p "
              f"{r['die1_pp']*1e3:8.1f}m {r['die2_pp']*1e3:8.1f}m "
              f"{r['vu1_pp']*1e3:7.1f}m")
    print("-" * 100)
    print("Vdie pp = tetnienie na WEWNETRZNEJ szynie zasilania ringu (za pinem)")
    print("Vpin pp = tetnienie na pinie VCC ukladu (tam gdzie siega filtr)")
    print("jit     = odchylenie standardowe okresu (rozrzut deterministyczny,")
    print("          bo w modelu nie ma zrodel szumu -- to jest jitter wnoszony")
    print("          wylacznie przez zaklocenia zasilania)")
    print("\nOpis przypadkow:")
    for case, _ in results:
        print(f"  {case['tag']:15s} {case['desc']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
