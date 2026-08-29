> **Uwaga o zawartości i umiejscowieniu.** Katalog leży pod `Rev-1/`, ale
> zawiera dwie warstwy pracy z różnych etapów projektu:
>
> * **Analiza Rev-1** — ten dokument, modele w `models/`, testbenche `tb/` oraz
>   `calib_lvc04a.py`, `pdn_study.py`, `ro_coupling.py`. Dotyczy płytki z sześcioma
>   ringami na trzech układach `74LVC04`.
> * **Projektowanie zasilania Rev-2** — dziewięć skryptów `pdn_rev2_*.py` oraz
>   `rev2_ring_freq.py`. To one uzasadniają dobór ferrytów, kondensatorów i
>   rezystorów filtrujących w Rev-2, częstotliwości ośmiu ringów z docelowymi
>   częściami (`SN74LVC1G04`, `SN74LVC3G04`) i odsprzęganie obu zatrzasków
>   74HC174. Mimo nazwy katalogu jest to dokumentacja projektowa **Rev-2**.
>
> Wszystko poniżej opisuje warstwę pierwszą. Nazwy plików z przedrostkiem
> `rev2_` odnoszą się do rewizji płytki, nie do drugiej wersji symulacji.
>
> **Katalog `out/` nie jest w repozytorium** — 384 MB wyników `ngspice` i
> generowanych netlist. Powstaje w całości z powyższych skryptów: 254 z 262
> plików `.cir` są tworzone z szablonów w kodzie, reszta to `tb/`. Aby odtworzyć,
> uruchom wybrany skrypt (potrzebny `libngspice`, patrz `ngrun.py`).

---

# Symulacja ringów RO-TRNG — modele, wyniki, wnioski dla filtrów

Wszystko liczone w ngspice 42 (ta sama biblioteka, której używa KiCad 9).

## Co tu jest

```
spice/
  models/74LVC04A.lib      model inwertera + model układu HEX z dwoma ringami
  models/AP2112K.lib       model LDO AP2112K-3.3
  models/passives.lib      ferryt BLM18AG102SN1D, MLCC z ESR/ESL, ścieżki
  tb/tb_lvc04a_valid.cir   walidacja modelu inwertera wzgl. karty katalogowej
  tb/tb_ap2112_valid.cir   walidacja modelu LDO wzgl. karty katalogowej
  tb/tb_ro_single.cir      pojedynczy ring: częstotliwość, prąd zasilania
  tb/tb_ro_sens.cir        czułość dF/dVCC
  tb/tb_ro_detune.cir      eksperyment kontrolny do analizy sprzężenia
  calib_lvc04a.py          automatyczna kalibracja modelu inwertera
  pdn_study.py             impedancja sieci zasilania, porównanie filtrów
  ro_coupling.py           sprzężenie ring↔ring (analiza transientowa)
  ngrun.py                 uruchamianie ngspice wsadowo
```

W systemie **nie ma** programu `ngspice` (jest tylko `libngspice.so`, której
używa KiCad). `ngrun.py` woła tę bibliotekę przez ctypes, więc symulacje da się
uruchamiać z linii poleceń:

```bash
cd spice/tb && python3 ../ngrun.py tb_ro_single.cir
cd spice    && python3 pdn_study.py
```

---

## 1. Modele — zgodność z kartami katalogowymi

### 74LVC04A

Jedna bramka = 3 wewnętrzne inwertery CMOS (tak jak Fig.3 w karcie). To jest
istotne: model z jednym inwerterem i „pojemnością opóźniającą" nie generuje
impulsów prądu zasilania, bo pojemność wpięta na stałe między VCC a GND się nie
przełącza. A to właśnie te impulsy są przedmiotem filtracji.

Parametry dobrane automatycznie (`calib_lvc04a.py`, Newton na dwóch celach):

| parametr | model | karta katalogowa |
|---|---|---|
| t_pd @ CL=50pF, RL=500Ω | **2.00 ns** | 2.0 ns (typ) |
| C_PD | **9.90 pF** | 9.9 pF (typ) |
| C_I | 4.0 pF | 4.0 pF |
| V_OL @ 24 mA, VCC=3.0V | 0.238 V | ≤ 0.55 V (limit) |
| V_OH @ 24 mA, VCC=3.0V | 2.763 V | ≥ 2.2 V (limit) |
| R_on wyjścia | 9.9 Ω | — |

### AP2112K-3.3

Model z **strukturą pętli regulacji**, nie idealne źródło napięcia — dzięki temu
ma realistyczną impedancję wyjściową i jej zależność od częstotliwości.

| parametr | model | karta katalogowa |
|---|---|---|
| VOUT | 3.2999 V | 3.300 V ±2 % |
| regulacja obciążenia | 0.201 % / 22.9 mΩ | 0.2 % |
| regulacja wejściowa | 0.027 % | 0.2 % (typ) |
| VDROP @ 600 mA | 0.337 V | 0.32 V |
| ILIM | 1.099 A | 1.1 A (typ) |
| t_on (95 %) | 61 µs | dziesiątki µs (oscylogram) |
| PSRR @ 100 Hz / 1 k / 10 k / 100 k | 72 / 69 / 53 / 43 dB | 80 / 78 / 58 / 38 dB |

Uwaga do PSRR: krzywa z karty ma nachylenie ~14 dB/dekadę, czego **nie da się
uzyskać żadnym fizycznym mechanizmem przy 1 µF na wyjściu** (każdy daje
20 dB/dek). Model przecina krzywą katalogową — jest o ~8 dB pesymistyczny na
niskich f i ~5 dB optymistyczny na 100 kHz. Dla tego projektu to bez znaczenia,
bo ringi pracują >150 MHz, gdzie LDO jest całkowicie poza grą.

---

## 2. Pojedynczy ring

Przy idealnym, sztywnym zasilaniu 3.3 V (`tb_ro_single.cir`):

| wielkość | wartość |
|---|---|
| częstotliwość | **159.5 MHz** |
| czasy zboczy tr / tf | 0.35 / 0.36 ns |
| prąd średni (3 bramki) | **24.1 mA** |
| prąd szczytowy | **103 mA** |
| **dF/dVCC** | **81.4 MHz/V** |

Prąd średni zgadza się niezależnym rachunkiem z C_PD:
`3·(C_PD + C_I + C_ścieżki)·VCC·f = 23.5 mA` wobec 24.1 mA zmierzonych.

**dF/dVCC = 81.4 MHz/V to najważniejsza liczba tego dokumentu.** Oznacza, że
każdy 1 mV tętnienia na VCC przesuwa częstotliwość ringu o 81 kHz. Filtr
zasilania nie służy tu „czystości zasilania" w abstrakcyjnym sensie — on
bezpośrednio decyduje, ile deterministycznej modulacji częstotliwości trafia do
strumienia bitów.

---

## 3. Impedancja sieci zasilania — i problem, który znalazłem

`pdn_study.py`, topologia odwzorowuje `Rev-1/hardware/RO-Trng.kicad_sch`.

| wariant | Z@1kHz | Z@1MHz | Z@159MHz | **Z_max** | **@ f** | sprzężenie U1→U2 @159MHz |
|---|---|---|---|---|---|---|
| jak w schemacie simu (L=100n+0.1Ω) | 0.25 | 0.99 | 0.45 | 8.1 Ω | 1.69 MHz | 9.3e-06 |
| **realna płytka (ferryty)** | 1.25 | 2.09 | 0.46 | **37.0 Ω** | **369 kHz** | 7.2e-08 |
| bez filtru per układ | 0.65 | 0.02 | 0.41 | 1.2 Ω | 20 kHz | 4.8e-03 |
| rezystor 10 Ω per układ | 10.6 | 1.79 | 0.44 | 11.0 Ω | 17 kHz | 8.3e-04 |
| ferryt + 4.7 Ω | 5.9 | 2.05 | 0.46 | 7.1 Ω | 307 kHz | 7.2e-08 |
| **ferryty + 1 µF per układ** | 1.25 | 0.20 | **0.35** | **3.9 Ω** | 122 kHz | 4.2e-08 |

### Znalezisko: rezonans 37 Ω przy 369 kHz

BLM18AG102SN1D ma na niskich częstotliwościach ~2.2 µH. Z odsprzęgnięciem
100 nF + 2.2 nF przy układzie daje to obwód rezonansowy 369 kHz o dobroci ~30.
Impedancja szyny rośnie tam z ~1.2 Ω do **37 Ω**.

Konsekwencja przez dF/dVCC: 1 mA składowej prądu przy 369 kHz → 37 mV tętnienia
→ **±3.0 MHz dewiacji** częstotliwości ringu (na nośnej 159.5 MHz). To jest
periodyczna, deterministyczna modulacja — dokładnie to, czego w TRNG być nie
powinno, bo wprowadza strukturę i koreluje próbki.

**Schemat w `simu/ro-simu` tego problemu nie pokaże**, bo ferryty są w nim
zastąpione przez `L=100n + R=0.1`. To 22× za mała indukcyjność — przesuwa
rezonans na 1.69 MHz i zaniża go do 8 Ω.

### Co z tym zrobić

Najlepszy stosunek efektu do kosztu: **dołożyć 1 µF przy każdym układzie**
(obok istniejących 100 n + 2n2). Rezonans spada z 37 Ω do 3.9 Ω (−19 dB),
a impedancja przy częstotliwości ringu poprawia się z 0.46 Ω do 0.35 Ω.
Testowany dodatkowo snubber RC (10 Ω + 10 nF) nie wnosi nic ponad samo 1 µF —
nie warto.

**Rezystory szeregowe zamiast ferrytów odpadają** — nie ze względu na
impedancję, tylko na DC: przy 48 mA na układ 10 Ω daje 0.48 V spadku, 22 Ω daje
1.06 V. Układ nie miałby zasilania.

**Ferryty per układ zostawić.** Ich prawdziwa robota to izolacja układów od
siebie: sprzężenie U1→U2 przy częstotliwości ringu wynosi 7.2e-08 (−143 dB) z
ferrytami, a 4.8e-03 (−46 dB) bez nich. To 97 dB różnicy.

---

## 4. Sprzężenie między ringami — najważniejszy wniosek

**Na realnej płytce dwa ringi siedzą w JEDNYM układzie 74LVC04A.**

Z netlisty `Rev-1/hardware/RO-Trng.kicad_sch`: każdy z układów U1, U2, U3 to
74LVC04 hex w TSSOP-14 zawierający dwa niezależne ringi:

- ring A = bramki 6→5→4, punkt pomiarowy na wyjściu bramki 4
- ring B = bramki 1→2→3, punkt pomiarowy na wyjściu bramki 3

Oba dzielą **jeden pin VCC (14), jeden pin GND (7)** i jedną szynę na krzemie.

`ro_coupling.py` mierzy, co z tego wynika. Rozstrojenie: ring B ma 0.6 pF więcej
na jednym węźle.

| przypadek | f_ring1 | f_ring2 | **\|Δf\|** |
|---|---|---|---|
| **kontrola** — osobne, idealne zasilania (`tb_ro_detune.cir`) | — | — | **710 kHz** |
| 2 ringi w JEDNYM układzie, filtr jak zmontowany | 193.092 MHz | 193.091 MHz | **1 kHz** |
| 2 ringi w JEDNYM układzie, +1 µF | 148.376 MHz | 148.378 MHz | **2 kHz** |
| 2 ringi w OSOBNYCH układach | 176.378 MHz | 175.574 MHz | **804 kHz** |
| jeden układ, skrajnie optymistyczne pasożyty obudowy | 145.946 MHz | 145.963 MHz | **17 kHz** |
| osobne układy, te same optymistyczne pasożyty | 144.585 MHz | 143.946 MHz | **639 kHz** |

Własna różnica częstotliwości tych dwóch ringów to **710 kHz**. W osobnych
układach ta różnica się utrzymuje (804 / 639 kHz). W jednym układzie hex
zapada się do 1–17 kHz, czyli **40–700 razy**.

To jest wciąganie (injection locking) przez wspólną indukcyjność pinów zasilania
i wspólną szynę na krzemie. Dwa ringi w tej samej obudowie nie są niezależnymi
źródłami entropii — synchronizują się.

Sprawdziłem odporność tego wniosku: nawet przy 10× lepszych założonych
pasożytach obudowy (lpin 1 nH zamiast 2.5 nH, pojemność na krzemie 1 nF zamiast
100 pF) ringi w jednym układzie nadal się ściągają (17 kHz wobec 639 kHz).

**Żaden filtr zewnętrzny tego nie naprawi**, bo droga sprzężenia jest wewnątrz
obudowy — za pinem, do którego filtr sięga. Potwierdza to `tb_ro_detune.cir`:
przy **idealnym** zasilaniu na pinie tętnienie na szynie krzemowej i tak wynosi
736 mV międzyszczytowo (same pasożyty obudowy przy prądach 103 mA i zboczach
0.35 ns). Filtr zewnętrzny obniża tętnienie z 1749 mV do ~1114 mV — reszta jest
poza jego zasięgiem.

Jedyne skuteczne rozwiązanie to **jeden ring na obudowę** (np. 74LVC1G04 w
SOT-353), albo świadome przyjęcie, że kanały D0/D1, D2/D3, D4/D5 są parami
skorelowane i traktowanie każdej pary jako jednego źródła entropii.

---

## 4b. Rozbieżność z pomiarem: symulacja 157 MHz vs oscyloskop ~50 MHz

Geometrię ścieżek wyciągnąłem wprost z `RO-Trng.kicad_pcb` (`tb_ro_real.cir`).

**Płytka 2-warstwowa, 1.6 mm, ścieżki ringów 0.2 mm, wylewka GND na B.Cu.**
Mikropasek w=0.2 mm nad h=1.6 mm: w/h = 0.125 → Z₀ = 142 Ω, ε_eff = 2.82,
**C' = 0.040 pF/mm** (0.4 pF/cm), opóźnienie 5.6 ps/mm.

| ring | węzeł 1 | węzeł 2 | węzeł 3 (do U4) | f symulacji |
|---|---|---|---|---|
| U2 A (/D0) | 3.26 mm | 2.83 mm | 11.99 mm | 157.6 MHz |
| U2 B (/D1) | 1.77 | 1.78 | 21.13 | 157.4 MHz |
| U1 A (/D2) | 2.57 | 2.57 | 20.57 (+2 przel.) | 156.3 MHz |
| U1 B (/D3) | 0.65 | 0.65 | 16.76 | 157.7 MHz |
| U3 A (/D4) | 3.51 | 3.63 | 12.89 | 157.6 MHz |
| U3 B (/D5) | 0.65 | 0.65 | 18.49 | 157.6 MHz |

Wniosek: **rzeczywista geometria zmienia wynik o 1.6 %** (159.5 → 157.4 MHz).
Przy 1.6 mm dielektryka i ścieżce 0.2 mm pojemność na milimetr jest bardzo
mała — nawet najdłuższa ścieżka /D1 (21 mm) to zaledwie 0.85 pF, mniej niż
wejście 74HC174.

Modelowanie ścieżki /D1 jako **linii długiej** zamiast pojemności skupionej
daje 148.1 MHz zamiast 157.4 — czyli −6 %. To większy efekt niż sama wartość
pojemności, ale nadal nie rząd wielkości.

### Ile trzeba, żeby ring zwolnił do 50 MHz

`tb_ro_cload.cir` — częstotliwość w funkcji pojemności każdego węzła:

| C/węzeł | 0 | 1p | 5p | 10p | 20p | 47p | 100p | 150p |
|---|---|---|---|---|---|---|---|---|
| f [MHz] | 163.1 | 159.5 | 147.3 | 135.6 | 118.8 | 92.5 | 67.6 | 55.1 |

50 MHz wymaga **~180 pF na węzeł**. Przy 0.040 pF/mm to **4.5 metra ścieżki
na węzeł**. Pojemność ścieżek tego nie tłumaczy — i żadne dokładniejsze dane
o ich wymiarach tego nie zmienią.

`tb_ro_real.cir` — ta sama zależność od napięcia zasilania:

| VCC | 3.3 V | 2.6 V | 2.2 V | 1.8 V |
|---|---|---|---|---|
| f | 157.4 MHz | 103.4 MHz | 74.9 MHz | 48.6 MHz |

50 MHz odpowiada VCC ≈ **1.82 V**.

### Ograniczenie niezależne od mojego modelu

Karta katalogowa podaje t_pd = 2.0 ns (typ) przy **CL = 50 pF**. Obciążenie
węzła w ringu to ~5 pF, czyli 10× mniej, a opóźnienie rośnie monotonicznie z
obciążeniem. Zatem t_stage w ringu < 2.0 ns, więc **f > 1/(6·2.0 ns) = 83 MHz**.
To wynika wprost z karty, niezależnie od tego jak podzieliłem opóźnienie na
wewnętrzne i zależne od obciążenia.

Uczciwe zastrzeżenie: to rachunek na wartościach typowych. Limit *maksymalny*
t_pd to 4.5 ns (−40…+85 °C) i 6.0 ns (−40…+125 °C). Egzemplarz skrajnie wolny,
u którego opóźnienie jest w większości wewnętrzne, mógłby w teorii zejść w
okolice 50 MHz. Przy 25 °C i typowym egzemplarzu jest to jednak mało
prawdopodobne, i nie tłumaczyłoby, dlaczego wszystkie sześć ringów miałoby być
jednakowo wolne.

### Kandydaci na wyjaśnienie 3× i jak je rozstrzygnąć

| hipoteza | co by dała | jak sprawdzić |
|---|---|---|
| obciążenie sondą oscyloskopu | sonda 10:1 (~12 pF) na jednym węźle → ~143 MHz; sonda 1:1 (~100 pF) → ~93 MHz | zmierzyć sondą aktywną / porównać 10:1 z 1:1 |
| pasmo oscyloskopu < 157 MHz | licznik częstotliwości pokazuje bzdury, przebieg mocno stłumiony | sprawdzić pasmo toru; 157 MHz wymaga ≥500 MHz |
| mierzony inny węzeł | wyjście 74HC174 pracuje z zegarem próbkowania STM32, nie z częstotliwością ringu | sprawdzić, na którym punkcie była sonda |
| obniżone VCC | 1.82 V dałoby 50 MHz | zmierzyć VCC multimetrem na pinie 14 |
| egzemplarz skrajnie wolny | patrz wyżej | porównać kilka sztuk |

**Najlepszy test rozstrzygający — pomiar prądu, nie napięcia.** Prąd zasilania
ringu jest wprost proporcjonalny do częstotliwości (I = 3·C_eq·V_CC·f) i jest
zupełnie odporny na obciążenie sondą oraz na pasmo oscyloskopu:

| f rzeczywista | prąd 1 ringu | prąd 1 układu (2 ringi) | prąd 3 układów |
|---|---|---|---|
| 157 MHz | ~24 mA | ~48 mA | ~145 mA |
| 50 MHz | ~7.6 mA | ~15 mA | ~46 mA |

Wystarczy zwykły multimetr w trybie **napięcia** na ferrycie, albo miernik USB
w torze zasilania.

### Przeliczenie spadku na ferrycie na częstotliwość

BLM18AG102SN1D wg karty Murata: **R_DC = 0.50 Ω** (wartość początkowa; 0.60 Ω
przy 125 °C), prąd znamionowy **450 mA**.

Prąd ringu jest proporcjonalny do częstotliwości: 23.8 mA przy 157.4 MHz,
czyli **6.61 MHz na każdy 1 mA** prądu jednego ringu.

| mierzony ferryt | co prowadzi | wzór | 157 MHz | 50 MHz |
|---|---|---|---|---|
| FB3 / FB4 / FB5 | 1 układ = 2 ringi | **f[MHz] ≈ 6.6 · U[mV]** | 23.8 mV | 7.6 mV |
| FB2 | 3 układy = 6 ringów | **f[MHz] ≈ 2.2 · U[mV]** | 71.4 mV | 22.7 mV |
| FB1 | tylko VDDA STM32 | — | nie dotyczy ringów | |

### Tablica diagnostyczna VCC → f, prąd, spadek na ferrycie

`tb_ro_vcc_iv.cir` — jeden układ (2 ringi, geometria ścieżek jak U2), ferryt 0.50 Ω:

| VCC | f ringu | prąd układu | **spadek na FB3/4/5** |
|---|---|---|---|
| 3.30 V | 157.6 MHz | 49.4 mA | **24.7 mV** |
| 3.00 V | 133.9 MHz | 37.6 mA | 18.8 mV |
| 2.60 V | 103.6 MHz | 24.7 mA | 12.4 mV |
| 2.20 V | 75.0 MHz | 14.9 mA | 7.46 mV |
| 2.00 V | 61.5 MHz | 11.0 mA | 5.50 mV |
| **1.80 V** | **48.6 MHz** | 7.85 mA | **3.92 mV** |
| 1.60 V | 36.6 MHz | 5.30 mA | 2.65 mV |

**Pomiar na płytce (2026-08, FB4 / układ U2): 4.3 mV.** Interpolacja daje
VCC ≈ 1.85 V i f ≈ 51 MHz — co niezależnie zgadza się z odczytem
z oscyloskopu (~50 MHz). Dwa niezależne pomiary spełnione jednym parametrem.

Potwierdza to karta katalogowa (Tab.7): przy VCC 1.65–1.95 V t_pd(typ) = 3.7 ns
(zamiast 2.0 ns przy 3.3 V), co samo z siebie daje f > 1/(6·3.7 ns) = 45 MHz.

**AKTUALIZACJA — hipoteza niskiego VCC obalona pomiarem.** Zmierzone na płytce:
J3 pin 1 = 3.3 V, FB2 = 14.1 mV, FB4 = 4.3 mV. Czyli zasilanie jest sprawne
(spadek na całym torze to ~18 mV, VCC na U2 ≈ 3.28 V), a stosunek
FB2/FB4 = 3.28 przy oczekiwanym 3.0 potwierdza, że multimetr mierzy poprawnie
i nie prostuje RF. Wnioski poniżej — sekcja 4c.

**Zastrzeżenie do kolumny prądu przy niskim VCC:** model ma stałe pojemności,
a karta podaje C_PD zależne od napięcia — 9.9 pF przy 3.0–3.6 V, 7.1 pF przy
2.3–2.7 V, **3.9 pF przy 1.65–1.95 V**. Rzeczywisty prąd przy 1.8 V jest więc
niższy niż w tabeli (ok. 5 mA zamiast 7.85 mA), co przesuwa oszacowanie z
pomiaru prądu w górę, do VCC ≈ 2.1 V / f ≈ 66 MHz. Kolumna f jest tym
nieobciążona, bo opóźnienie jest zakotwiczone w t_pd z karty.
Wniosek zakresowy: **VCC ≈ 1.85–2.1 V, f ≈ 50–66 MHz.**

### Który ferryt jest gdzie (z pliku PCB)

Obrys ekranu RF (J1) obejmuje X 106.8–145.7, Y 52.1–78.3 mm. Stąd:

| element | pozycja (mm) | pod ekranem? |
|---|---|---|
| FB5 | 115.98, 56.10 — ~4 mm od U1 | **tak** |
| FB4 | 130.68, 55.80 — ~4 mm od U2 | **tak** |
| FB3 | 122.70, 67.85 — ~4 mm od U3 | **tak** |
| FB2 | 111.00, 75.10 | **tak** |
| **FB1** | **128.20, 95.00** | **NIE — dostępny** |
| U1 / U2 / U3 | 115.3,59.8 / 130.1,59.6 / 121.9,71.8 | tak |
| U6 (LDO), J2 (USB), J3 (Tag-Connect) | poza obrysem | nie |

**Uwaga: FB1 jest jedynym ferrytem dostępnym bez zdjęcia ekranu, a zasila
wyłącznie VDDA mikrokontrolera — z ringami nie ma nic wspólnego.** Pomiar na
nim nic nie mówi o częstotliwości oscylacji.

J3 (Tag-Connect) jest poza ekranem i ma pin 1 = VCC oraz piny 3/5/9 = GND,
więc napięcie szyny 3.3 V da się sprawdzić bez rozbierania płytki.

## 4c. Sprzeczność w danych katalogowych 74LVC04A — czy na płytce jest ten układ?

Stan pomiarów (sierpień 2026):

| wielkość | wartość |
|---|---|
| szyna 3.3 V (J3 pin 1) | 3.30 V — LDO reguluje poprawnie |
| spadek na FB2 (3 układy, 6 ringów) | 14.1 mV → **28.2 mA** |
| spadek na FB4 (U2, 2 ringi) | 4.3 mV → **8.6 mA**, czyli **4.3 mA/ring** |
| FB2 / FB4 | 3.28 (oczekiwane 3.0) — miernik wiarygodny |
| oscyloskop | ~50 MHz |

Prąd ringu wiąże się z częstotliwością wzorem z karty (Tab.7, przypis 4):
I = 3·(C_PD + C_L)·V_CC·f. Stąd zmierzona pojemność przełączana na bramkę:

| zakładane f | wynikające C_PD + C_L |
|---|---|
| 157 MHz | 2.8 pF |
| 50 MHz | 8.7 pF |
| **30 MHz** | **14.5 pF** |

Karta 74LVC04A podaje C_PD = 9.9 pF, a obciążeniem jest C_I = 4.0 pF następnej
bramki plus ~0.5 pF ścieżki, razem **14.4 pF**. Zmierzony prąd zgadza się z tą
wartością znakomicie — ale **tylko przy f ≈ 30 MHz**.

Tymczasem drugi parametr z tej samej karty, t_pd = 2.0 ns (typ) przy CL = 50 pF,
wyklucza f poniżej ~83 MHz dla ringu trójinwerterowego obciążonego ~5 pF
(patrz sekcja 4b). Nawet limit maksymalny 4.5 ns nie schodzi do 30 MHz przy tak
lekkim obciążeniu.

**Dwa parametry z tej samej karty nie mogą być jednocześnie prawdziwe dla tej
płytki.**

### Hipotezy sprawdzone i odrzucone

| hipoteza | jak sprawdzona | wynik |
|---|---|---|
| obniżone VCC na układzie | J3 = 3.30 V, spadki na FB2+FB4 = 18.4 mV | **odrzucona** — VCC na U2 ≈ 3.28 V |
| multimetr prostuje RF / kłamie | FB2/FB4 = 3.28 przy oczekiwanym 3.0 | **odrzucona** — miernik spójny |
| zamontowano inną rodzinę logiki | odczyt oznaczenia U1: **`LVC04A` / `C2515`** | **odrzucona** — to jest 74LVC04A |
| błędne R_DC ferrytu w moim przeliczeniu | karta/LCSC: **DCR 500 mΩ**, 450 mA, 1 kΩ@100 MHz | **odrzucona** — 0.50 Ω poprawne |
| pojemność ścieżek | geometria z PCB, 0.040 pF/mm | **odrzucona** — daje 1.6 % (sekcja 4b) |
| histereza wejścia (Schmitt) | slew ~3 V/ns, hipotetyczne 0.5 V histerezy → +1 ns/okres | **odrzucona** — daje ~136 MHz, nie 30 |

### Stan otwarty

Po odrzuceniu powyższych zostaje sprzeczność, której **nie potrafię rozstrzygnąć
bez jeszcze jednego pomiaru**:

- pomiar prądu (dwa niezależne ferryty, spójne ze sobą) → **f ≈ 30 MHz**
- t_pd z karty tego samego, potwierdzonego układu → **f ≥ 83 MHz**
- oscyloskop → **~50 MHz**
- symulacja zakotwiczona w t_pd i C_PD → **157 MHz**

Nie zgadują: jedna z tych liczb jest błędna i nie wiem która. Pomiary
rozstrzygające — patrz niżej.

### Pomiar rozstrzygający

**A. Całkowity prąd z USB** (miernik USB-C w torze). Omija R_DC i poziom
miliwoltów, sygnał jest rzędu setek mA:

| f rzeczywista | ringi | +74HC174 +STM32 | **razem z USB** |
|---|---|---|---|
| 157 MHz | 134 mA | ~45 mA | **~180 mA** |
| 30 MHz | 26 mA | ~21 mA | **~47 mA** |

**B. Amplituda na oscyloskopie.** Jeśli ring pracuje na 157 MHz, a tor pomiarowy
ma za małe pasmo, to obraz jest silnie stłumiony. Więc: czy na ekranie jest
zdrowy przebieg o amplitudzie bliskiej 0–3.3 V, czy mała sinusoida? Amplituda
bliska szyny = sygnał naprawdę jest na tej częstotliwości. Potrzebne też pasmo
oscyloskopu i typ sondy (10:1 / 1:1 / aktywna).

Orientacyjne dopasowanie rodzin przy 3.3 V (wartości przybliżone, nie
weryfikowałem tych kart katalogowych — do potwierdzenia po odczytaniu oznaczeń):

| rodzina | t_pd @3.3 V | f ringu | C_PD | przewidywany I/ring | pomiar 4.3 mA |
|---|---|---|---|---|---|
| 74LVC04A | ~1.4 ns | ~120–160 MHz | 9.9 pF | ~20–24 mA | ✗ 5× za dużo |
| **74AHC04** | **~5.3 ns** | **~31 MHz** | **~12 pF** | **~5.1 mA** | **✓ pasuje** |
| 74LV04A | ~9 ns | ~18 MHz | ~14 pF | ~3.4 mA | częściowo |
| 74HC04 | ~13 ns | ~13 MHz | ~21 pF | ~3.0 mA | f za niskie |

Wskazówka poszlakowa: schemat symulacyjny `ro-simu.kicad_sch` ma w polu Value
bramek wpisane **`74AHC1G04`**, a nie 74LVC04.

**Do zrobienia: odczytać oznaczenie na wierzchu U1/U2/U3** i dopiero wtedy
przeliczać. Po ustaleniu rodziny model trzeba przekalibrować (`calib_lvc04a.py`
robi to automatycznie po podmianie celów t_pd i C_PD).

### Co z wcześniejszymi wnioskami

| wniosek | czy przetrwa zmianę rodziny układu |
|---|---|
| rezonans 37 Ω @369 kHz na szynie | **tak** — to własność samej sieci pasywnej (ferryt + MLCC), niezależna od prędkości ringu |
| zalecenie 1 µF przy każdym układzie | **tak** — z tego samego powodu |
| ferryty per układ dają izolację | **tak** |
| **synchronizacja ringów w jednym układzie** | **wymaga ponownego sprawdzenia** — mechanizmem jest indukcyjność wspólnych pinów przy dużym di/dt; przy 30–50 MHz zamiast 157 MHz di/dt jest kilkukrotnie mniejsze, więc sprzężenie może być słabsze |
| dF/dVCC = 81.4 MHz/V | **nie** — do przeliczenia dla właściwej rodziny |

## 4d. Konfrontacja z pomiarami entropii (`entopy_tests/summary.md`)

### Potwierdzone: przypisanie ringów do obudów

`summary.md` §6 zostawia to jako hipotezę („the gate-to-ring assignment in the
schematic was not verified"). Prześledziłem cały łańcuch z netlisty:

ring → `/Dx` → 74HC174 (U4) → rezystor 47 Ω (R6–R11) → pin STM32 → kanał.

| U4 D→Q | sieć | R | pin U5 | port | **kanał** |
|---|---|---|---|---|---|
| D1(3)→Q1(2) | /D0 | R11 | 13 | PA7 | **F** |
| D2(4)→Q2(5) | /D1 | R10 | 10 | PA4 | **E** |
| D3(6)→Q3(7) | /D2 | R9 | 9 | PA3 | **D** |
| D4(11)→Q4(10) | /D3 | R8 | 8 | PA2 | **C** |
| D5(13)→Q5(12) | /D4 | R7 | 7 | PA1 | **B** |
| D6(14)→Q6(15) | /D5 | R6 | 6 | PA0 | **A** |

Mapowanie jest **odwrócone**: A=/D5, B=/D4, C=/D3, D=/D2, E=/D1, F=/D0.
Łącząc to z przypisaniem ringów do obudów (sekcja 4 tego dokumentu):

| obudowa | ringi | **kanały** |
|---|---|---|
| U1 | /D2, /D3 | **C, D** |
| U2 | /D0, /D1 | **E, F** |
| U3 | /D4, /D5 | **A, B** |

Pary z jednej obudowy to więc **{A,B}, {C,D}, {E,F}** — dokładnie te trzy pary,
które `summary.md` wskazuje jako konsekwentnie istotne na 60, 80 i 100 kHz.
**Caveat #2 z §10 można zamknąć.** Uwaga: sam podział na pary był odgadnięty
poprawnie, ale przypisanie do obudów nie — jest U3={A,B}, U1={C,D}, U2={E,F},
a nie U1={A,B}, U2={C,D}, U3={E,F}.

### Obalone: moja teza o synchronizacji ringów

Symulacja (sekcja 4) przewidywała, że ringi w jednej obudowie **wciągają się**:
własna różnica częstotliwości 710 kHz zapadała się do 1 kHz. Gdyby tak było,
skorelowanie próbek obu kanałów byłoby bliskie 1.

Pomiar mówi: **r = 0.013…0.029**, informacja wzajemna 0.0001…0.0004 bit/próbkę,
łącznie 0.13 % budżetu entropii.

To jest sprzężenie **realne i dokładnie w przewidzianych parach, ale o dwa rzędy
wielkości słabsze niż wciąganie.** Wniosek: przyjęte pasożyty obudowy
(lpin 2.5 nH, cdie 100 pF, rrail 0.4 Ω) są zbyt pesymistyczne — i/lub ringi
pracują wolniej, przez co di/dt jest kilkukrotnie mniejsze. **Zmierzone r jest
teraz celem kalibracyjnym** dla tych parametrów, zamiast zgadywania.

### Wskazówka, której nie umiałem odczytać z samego schematu: opóźnienie sprzężenia

`summary.md` §6 podaje, że najsilniejsza korelacja występuje nie przy lag 0,
tylko przy **lag +2 lub −1**, co przy 100 kHz oznacza **10–20 µs**.

Sprzężenie przez szynę zasilania jest podnanosekundowe — miałoby maksimum przy
lag 0. Skala 10–20 µs wskazuje na **inny mechanizm, którego w modelu nie ma**:

| kandydat | skala czasowa | czy jest w modelu |
|---|---|---|
| sprzężenie cieplne na wspólnym krzemie | µs–ms | **nie** |
| pętla regulacji LDO (~200 kHz) | ~5 µs | tak (tylko w analizie AC) |
| rezonans ferryt+MLCC 369 kHz | 2.7 µs | tak |

Dodatkowo widać różnicę strukturalną: pary z jednej obudowy (A-B, C-D, E-F) mają
korelację **dodatnią**, a pary międzyobudowowe (A-F: A∈U3, F∈U2; D-E: D∈U1, E∈U2)
**ujemną** przy lag +2. Zmiana znaku to sygnatura sprzężenia przez sieć
rezonansową, a nie przez proste opóźnienie.

### Entropia jako niezależne oszacowanie częstotliwości ringu (słabe)

Estymator t-Tuple znajduje powtórzenia ciągów o długości **t = 148–214 próbek** —
to w przybliżeniu długość dekorelacji fazy ringu. Dla fazy błądzącej losowo
dekorelacja następuje po n ≈ 1/x² próbek, gdzie x to jitter narosły między
próbkami wyrażony w okresach ringu. Z n ≈ 180 wychodzi **x ≈ 0.075**.

Ponieważ x = ε·√(f_ring/f_s), gdzie ε = σ_T/T (względny jitter okresu):

| f_ring | wynikające ε | σ_T |
|---|---|---|
| 157 MHz | 1.9·10⁻³ | 12 ps |
| 50 MHz | 3.3·10⁻³ | 66 ps |
| 30 MHz | 4.3·10⁻³ | 143 ps |

Typowy ring CMOS ma ε rzędu 10⁻⁴…10⁻³, co **łagodnie przemawia za wyższą
częstotliwością**. Ale to słaba przesłanka: przy dF/dVCC = 81 MHz/V większość
jitteru w tym układzie pochodzi z zasilania, nie z szumu termicznego, a to
podnosi ε o nieznany czynnik. Nie rozstrzyga sporu z sekcji 4c.

### Najważniejszy wniosek dla jakości źródła

dF/dVCC = 81.4 MHz/V oznacza, że **1 mV szumu na VCC daje 81 kHz dewiacji**.
Jitter tego ringu jest więc w dużej mierze **napędzany zasilaniem, nie termiczny**.
To ma bezpośrednie konsekwencje dla TRNG:

- szum z zasilania jest **wspólny dla wszystkich ringów na tej samej szynie**,
  więc wnosi entropię **skorelowaną**, a nie niezależną — czyli sumowanie
  budżetów per kanał (§7 summary) jest tym bardziej ryzykowne
- rezonans **37 Ω @ 369 kHz** to impedancja **wspólna**: wszystko, co ją pobudzi,
  moduluje wszystkie ringi jednocześnie i spójnie
- jest to również punkt wstrzyknięcia dla atakującego — modulacja zasilania w
  okolicy 369 kHz ma tam 30× większą skuteczność niż gdzie indziej

To wzmacnia rekomendację z sekcji 7 (dołożyć 1 µF przy każdym układzie) o
argument, który nie wynikał z samej analizy sieci: chodzi nie tylko o czystość
zasilania, ale o **rozkorelowanie źródeł entropii**.

### Co dodać do symulacji

| # | co | po co |
|---|---|---|
| 1 | źródła szumu (termiczny w MOSFET-ach + wstrzykiwany szum zasilania) | bez nich model nie policzy jitteru ani entropii — dziś przewiduje tylko jitter deterministyczny |
| 2 | kalibracja pasożytów obudowy do zmierzonego r ≈ 0.02 | zastępuje zgadywanie lpin/cdie twardym celem liczbowym |
| 3 | model sprzężenia cieplnego na krzemie | jedyny kandydat na obserwowane maksimum przy 10–20 µs |
| 4 | próbkowanie wyników symulacji przy 100 kHz i liczenie tego samego r Pearsona | daje wielkość bezpośrednio porównywalną z `channel_crosstalk_rev1.py` |
| 5 | model fazowy (behawioralny) zamiast pełnego SPICE do statystyki | SPICE nie policzy 1.4 mln próbek; SPICE → dF/dVCC i transmitancje sprzężeń, potem Monte Carlo w Pythonie |
| 6 | zakres temperatury i napięcia | caveat #6 z summary; dF/dVCC już jest, brakuje temperatury |

## 4e. Rekomendacje dla Rev-2

### Q1. Filtr przy układach

Kluczowa obserwacja: **obecny filtr działa w złym paśmie.** Sprzężenie między
układami w funkcji częstotliwości (K12, mniej = lepiej):

| wariant | 10 kHz | 50 kHz | 100 kHz | 369 kHz | 1 MHz | 157 MHz | Z_max | spadek DC¹ |
|---|---|---|---|---|---|---|---|---|
| **as-built** (ferryt+100n+2n2) | **−4.5** | **−2.1** | −13.4 | −19.6 | −74.8 | −136.2 | 43.0 Ω @369k | 4 mV |
| +1 µF | −4.4 | −2.8 | −10.5 | −30.9 | −56.6 | −138.6 | 4.4 Ω @122k | 4 mV |
| 22 Ω zamiast ferrytu | −29.4 | −33.5 | −42.7 | −64.3 | −84.2 | −67.3 | 22.6 Ω @13k | 189 mV |
| R=22 Ω + 10 µF | −48.4 | −64.6 | −77.2 | −101.3 | −124.5 | −69.3 | 0.54 Ω | 189 mV |
| **ferryt + R=22 Ω + 10 µF** | **−48.8** | **−65.0** | **−77.6** | **−102.2** | **−127.7** | **−138.6** | **0.55 Ω** | 189 mV |
| bez filtru per układ | −0.1 | 0.0 | 0.0 | 0.9 | −1.4 | −38.7 | 1.2 Ω | 0 |

¹ przy zmierzonym prądzie 8.6 mA na układ

Ferryt daje −136 dB przy 157 MHz, gdzie sprzężenie i tak nie ma znaczenia, i
**praktycznie nic w paśmie 10–50 kHz** — czyli dokładnie tam, gdzie leży
zmierzone opóźnienie korelacji 10–20 µs (sekcja 4d). Powód jest prosty: przy
50 kHz ferryt to tylko swoje 0.5 Ω rezystancji DC.

**Zalecenie: ferryt + rezystor szeregowy + kondensator masowy.**

```
VCCQ ──[ BLM18AG102SN1D ]──[ R 22R 0402 ]──┬── VCC układu (pin 14)
                                           │
                              10 µF 0805 ──┼── 100 nF 0402 ── 2.2 nF 0402
                                           │   (wszystko przy pinach 14/7)
                                          GND
```

Zysk wobec obecnej płytki: **+44 dB przy 10 kHz, +63 dB przy 50 kHz, +83 dB przy
369 kHz**, rezonans 43 Ω znika (Z_max 0.55 Ω), a izolacja RF zostaje bez zmian.

**Dobór R zależy od nierozstrzygniętego sporu o prąd (sekcja 4c):**

| prąd na układ | R = 22 Ω | R = 10 Ω | R = 4.7 Ω |
|---|---|---|---|
| 8.6 mA (zmierzony) | 189 mV ✓ | 86 mV ✓ | 40 mV ✓ |
| 48 mA (symulowany) | 1.06 V ✗ | 480 mV ✗ | 226 mV ~ |

Dlatego: przewidzieć footprint 0402 i **dobrać R po zmierzeniu prądu**. Przy
jednym ringu na obudowę prąd na obudowę spada o połowę, co dodatkowo ułatwia
sprawę. Alternatywa bez tego kompromisu: mały LDO per układ.

### Q2. Szerokość ścieżek w ringach

**Szerokość jest praktycznie bez znaczenia. Znaczenie ma odstęp i długość.**

Pojemność ścieżki na tym stackupie to 0.040 pF/mm (sekcja 4b) — cała pojemność
ścieżek zmienia częstotliwość o 1.6 %. Zwężenie ścieżki obniżyłoby ją jeszcze
mniej, a **podniosłoby Z₀** (już teraz 142 Ω), czyli zwiększyło podatność węzła
na przesłuch. Zwężanie nic nie daje, a lekko szkodzi.

Realny problem znaleziony w PCB: **ścieżki różnych ringów biegną zbyt blisko.**

| para ringów | najmniejszy odstęp | równoległy bieg <1 mm | <2 mm |
|---|---|---|---|
| U2: /D0 ↔ /D1 (kanały F↔E) | **0.65 mm** | 1.1 mm | 6.2 mm |
| U3: /D4 ↔ /D5 (kanały B↔A) | **0.65 mm** | 1.8 mm | 3.7 mm |
| U1: /D2 ↔ /D3 (kanały D↔C) | 1.60 mm | 0.0 mm | 2.3 mm |
| U1 ↔ U2 (/D2 ↔ /D1) | 1.30 mm | 0.0 mm | 6.1 mm |

Na płytce dwuwarstwowej 1.6 mm płaszczyzna masy jest **daleko**, więc ścieżki
sprzęgają się ze sobą mocniej niż z masą. Kryterium to odstęp względem wysokości
nad masą: sprzężenie robi się małe dopiero przy s > 2h = **3.2 mm**. Płytka ma
0.65 mm, czyli **5× za blisko**.

To jest drugi, niezależny kanał sprzężenia, którego w modelu nie było — obok
wspólnych pinów zasilania. Zgodność poszlakowa: para A-B (U3) ma najdłuższy
bieg poniżej 1 mm (1.8 mm) **i** najwyższą zmierzoną korelację (0.0292).

**Zalecenia:**
1. **Płytka 4-warstwowa** z masą 0.2 mm pod warstwą sygnałową. To jedna zmiana,
   która naprawia jednocześnie przesłuch (h spada 8×) i całą sieć zasilania.
2. Odstęp między ścieżkami różnych ringów **≥ 2h**; przy 4 warstwach i h=0.2 mm
   wystarczy 0.4 mm, więc jest to łatwiejsze niż teraz.
3. Ścieżka masy (guard) między sąsiadującymi ringami, przeszyta przelotkami.
4. **Skrócić** nety /Dx (dziś 12–21 mm) — to one biegną równolegle.
5. Nie zwężać ścieżek.

### Q3. Separacja galwaniczna

Ile szumu z USB dociera dziś do zasilania ringów:

| tłumienie 5 V → VCC układu | 1 kHz | 10 kHz | 50 kHz | 100 kHz | 369 kHz | 1 MHz |
|---|---|---|---|---|---|---|
| as-built | −60.5 | **−42.8** | **−42.8** | −49.2 | −60.5 | −146.0 |
| z filtrem z Q1 | −63.2 | −62.4 | −75.5 | −88.3 | −129.8 | −193.2 |

Przy dF/dVCC = 81.4 MHz/V: dziś 100 mV tętnienia USB przy 50 kHz daje 0.7 mV na
VCC ringu, czyli **57 kHz dewiacji**. Z filtrem z Q1 — 6.5 kHz.

**Sam filtr z Q1 daje 20–33 dB, czyli większość tego, co dałaby separacja — bez
przetwornicy.**

Bilans separacji galwanicznej:

| za | przeciw |
|---|---|
| usuwa szum hosta i pętle masy | izolowana przetwornica DC-DC ma własne tętnienie 20–100 mV przy 100 kHz–1 MHz, czyli **dokładnie w paśmie, gdzie PSRR LDO jest najsłabszy, a obecny filtr nie działa** |
| odcina drogę ataku przez zasilanie USB | pojemność międzyuzwojeniowa transformatora (5–20 pF) i tak przepuszcza RF, więc sama separacja nie zatrzymuje wycieku fazy ringów na kabel |
| ogranicza wyjście RF ringów na kabel | koszt, miejsce, złożoność, izolowany USB ogranicza do Full Speed |

**Werdykt: to funkcja bezpieczeństwa, nie jakości entropii.** Wprowadzona
naiwnie — przetwornica bez dobrego filtra po niej — **pogorszy** jakość źródła,
bo wstrzyknie tętnienie w najgorsze możliwe pasmo. Dla Rev-2 pieniądze lepiej
wydać na: jeden ring na obudowę, filtr z Q1, 4 warstwy.

Jeśli model zagrożeń obejmuje atakującego kontrolującego zasilanie USB, to
separacja ma sens — ale dopiero **na wierzchu** poprawionego filtra i w
kolejności: izolowana DC-DC → filtr LC → LDO → filtr RC per układ. Dodatkowo
dławik wspólny i ekranowanie, bo inaczej RF wychodzi przez pojemność
transformatora.

### Kolejność prac dla Rev-2

| # | zmiana | zysk |
|---|---|---|
| 1 | jeden ring na obudowę (74LVC1G04, SOT-353) | usuwa sprzężenie wewnątrz obudowy, którego żaden filtr nie sięga |
| 2 | płytka 4-warstwowa z bliską masą | naprawia przesłuch ścieżek i sieć zasilania naraz |
| 3 | filtr ferryt + R + 10 µF per układ | +44…+83 dB izolacji, likwiduje rezonans 43 Ω |
| 4 | odstęp ≥2h między ringami, krótsze nety /Dx | drugi kanał sprzężenia |
| 5 | separacja galwaniczna | tylko jeśli wymaga tego model zagrożeń |

## 5. Zastrzeżenia do wyników

Rzeczy, które trzeba wiedzieć, żeby nie przecenić tych liczb:

1. **Bezwzględne częstotliwości w tabeli sprzężenia (193 / 148 / 176 MHz) nie
   są wartościami ustalonymi.** Symulacja trwa 1.2 µs, a rezonans sieci przy
   369 kHz ma okres 2.7 µs i wysoką dobroć — szyna wciąż dzwoni po starcie.
   Dlatego średnie napięcie na szynie krzemowej wychodzi 3.13–3.75 V zależnie od
   przypadku i to ono przesuwa częstotliwość. **Miarodajne jest porównanie Δf
   między przypadkami**, nie wartości bezwzględne. Rzetelne f ustalone dają
   `tb_ro_single.cir` (159.5 MHz) i `tb_ro_detune.cir`.
2. **Pasożyty obudowy TSSOP-14 (2.5 nH na pin, 100 pF na krzemie, 0.4 Ω szyny)
   to założenia inżynierskie, nie dane katalogowe** — Nexperia ich nie podaje.
   Bezwzględna amplituda tętnienia na krzemie zależy od nich wprost. Dlatego
   powyżej jest test odporności z 10× lepszymi wartościami.
3. **Model nie ma histerezy wejścia.** Karta wymienia „Schmitt-trigger action at
   all inputs", ale nie podaje wartości dla 74LVC04A. Histereza podnosi okres
   oscylacji, więc jeśli pomiar na płytce da częstotliwość niższą niż 159.5 MHz,
   to pierwszy kandydat na przyczynę (obok pojemności ścieżek).
4. **Model ferrytu jest liniowy** — nie odwzorowuje spadku indukcyjności przy
   podmagnesowaniu prądem DC. Prąd znamionowy BLM18AG102SN1D to 450 mA, a
   najbardziej obciążony ferryt (FB2, ~145 mA) pracuje przy ~32 % tej wartości,
   więc model nominalny jest tu uzasadniony. R_DC = 0.50 Ω (wartość początkowa
   wg karty Murata), nie 0.6 Ω jak zakładałem wstępnie.
5. **W transiencie LDO zastąpiony idealnym źródłem** z 23 mΩ + 2 nH. Pełny model
   AP2112 jest użyty w analizie AC. Powód: nieliniowości modelu (`min()`, dioda
   podłożowa) rozbijały zbieżność przy krokach 2 ps, a powyżej ~260 kHz LDO i
   tak nie wnosi nic poza kondensatorem wyjściowym.
6. **Brak źródeł szumu** — w modelu nie ma szumu termicznego ani migotania.
   Kolumna „jitter" w `ro_coupling.py` to więc rozrzut *deterministyczny*,
   wnoszony wyłącznie przez zaburzenia zasilania. Prawdziwej entropii ringu ta
   symulacja nie liczy.

---

## 6. Co poprawić w projekcie KiCad

Schemat `ro-simu.kicad_sch` był w trakcie tej pracy otwarty w KiCadzie, więc go
nie ruszałem. Do poprawienia ręcznie:

1. **Nazwa podobwodu LDO.** Pole `Sim.Name` ma wartość `AP2112K-3.3`.
   **ngspice nie potrafi zinstancjonować podobwodu, którego nazwa zawiera
   `-3.3`** — parsuje to jako liczbę i zgłasza `unknown subckt`. Czyli LDO w tym
   projekcie nigdy się nie symulował. Zmienić na `AP2112K_33`, a
   `Sim.Library` na `spice/models/AP2112K.lib`.
2. **Literówka w warunkach początkowych.** Dyrektywa brzmi
   `.ic V(/d1)=0 V(/d3)=3.3`, a węzeł `/d3` nie istnieje — punkty pomiarowe to
   `/D1` i `/D2`. Drugi ring nie dostaje warunku startowego. Powinno być
   `.ic V(/D1)=0 V(/D2)=3.3`.
3. **Ferryty.** L1/L2/L3 = 100 nH + R = 0.1 Ω to zastępnik za
   BLM18AG102SN1D. Podmienić na model z `models/passives.lib` — inaczej symulacja
   nie pokaże rezonansu 369 kHz.
4. **Kondensatory** są modelowane jako idealne. Bez ESL nie widać, że przy
   159 MHz o skuteczności odsprzęgania decyduje indukcyjność montażu, a nie
   pojemność.
5. **Topologia nie odpowiada płytce.** Schemat daje każdemu ringowi własną sieć
   VCC, czyli modeluje ringi w osobnych układach. Na płytce dwa ringi dzielą
   jeden układ hex. To różnica między Δf = 804 kHz a Δf = 1 kHz — czyli między
   dwoma niezależnymi źródłami entropii a jednym.
6. Pole `Value` bramek to `74AHC1G04`, a symulowany jest 74LVC04A. Warto
   ujednolicić, żeby schemat nie mylił.

---

## 7. Podsumowanie rekomendacji

| priorytet | działanie | efekt |
|---|---|---|
| 1 | Rozdzielić ringi na osobne obudowy (74LVC1G04) | usuwa synchronizację ringów — jedyna rzecz, której nie da się naprawić filtrem |
| 2 | Dodać 1 µF przy każdym układzie | rezonans szyny 37 Ω → 3.9 Ω, Z@159MHz 0.46 → 0.35 Ω |
| 3 | Zostawić ferryty per układ | izolacja między układami −143 dB; bez nich −46 dB |
| 4 | Nie zastępować ferrytów rezystorami | 10 Ω = 0.48 V spadku DC przy 48 mA |
| 5 | Poprawić projekt symulacji (pkt 6) | inaczej symulacja nie pokazuje realnych problemów |
