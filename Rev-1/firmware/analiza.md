# Analiza czasowa firmware TRNG (Rev-1)

Pytanie wyjściowe: **czy przy multiplikacji bufora = 4 (komenda `'4'` po USB) stałe czasowe są wystarczające, aby wysłać bufor po USB, zanim zapełni się on nowymi danymi — i czy okno między napełnianiem bufora a `SHA256 + USB send` się wyrabia?**

Analiza dotyczy stanu kodu z commita `022efae` ("firmware v1, WIP"). Wszystkie liczby cyklowe pochodzą z disasemblacji `Release/trng.elf` (build zgodny z sourcem, `-Oz`, gcc 14.3, Cortex-M0 @ 48 MHz), model cyklowy: `ldr`/`str` = 2, branch taken = 3, `bl` = 4, wejście/wyjście z wyjątku = 16/12.

---

## 1. Podsumowanie

**Blokada nadrzędna:** TIM14 nigdy nie jest uruchamiany — `HAL_TIM_Base_Start_IT()` nie występuje w kodzie (`main.c:181` woła tylko `HAL_TIM_Base_Init`, a `TIM_Base_SetConfig` nie ustawia ani `CR1.CEN`, ani `DIER.UIE`). W obecnym buildzie `TIM14_IRQHandler` nie odpala się nigdy, więc żadna entropia nie jest zbierana. Cała analiza poniżej zakłada, że start timera zostanie dodany.

**Odpowiedź na pytanie:** dla m=4 nominalnie się wyrabia, ale margines na transakcję USB to ~1,09 ms, czyli dokładnie jedna ramka USB FS — zero realnego zapasu. W wariancie pesymistycznym okno się nie domyka. Dominującym kosztem nie jest SHA-256, a przerwanie próbkujące 80 kHz, które zjada 54% CPU.

---

## 2. Zmierzone stałe czasowe

| Element | Cykle | Czas |
|---|---|---|
| Okres TIM14 (ARR=599, presc=0, APB1=48 MHz) | 600 | 12,5 µs (80 kHz) |
| Wejście/wyjście z IRQ + wrapper `TIM14_IRQHandler` | ~58 | 1,2 µs |
| `collectEntropyBits` (w tym pętla `select_data` ~99 cyk) | ~197 | 4,1 µs |
| `HAL_TIM_IRQHandler` (ścieżka UIF: 8 testów flag + weak callback) | ~72 | 1,5 µs |
| **ISR razem** | **~327** | **6,8 µs** |
| `compress()` — 1 blok 64 B: 16×117 + 48×157 + 123 | ~9 500 | 198 µs |
| `tc_sha256_update` — pętla bajtowa | 22 / bajt | — |

**ISR zjada 327/600 = 54% CPU.** Na pętlę główną zostaje ~45%; przy `FLASH_LATENCY_1` i karze za pobrania po skokach realnie 33–40%. To, a nie SHA, jest głównym czynnikiem.

Weryfikacja `compress()`: 9500 cykli / 64 B = 148 cykli/bajt, co jest zgodne z publikowanymi pomiarami tinycrypt/mbedTLS SHA-256 w czystym C na Cortex-M0 (130–200 cykli/bajt).

---

## 3. Budżet dla multiplikacji = 4

Bufor 128 B = 1024 bity. Maska domyślna daje **5 bitów na próbkę** (patrz błąd nr 2 w sekcji 5), więc:

- **Okno napełniania: 205 próbek × 12,5 µs = 2,56 ms**
- SHA-256 nad 128 B = 3 wywołania `compress` (2 z `update`, 1 z `final`) + pętla bajtowa ≈ **32 000 cykli CPU = 0,67 ms czystego czasu procesora**
- Czas zegarowy przy ISR kradnącym CPU: **32 000 / 0,45 ≈ 1,47 ms** (pesymistycznie, przy 1 WS flash: **2,2–2,3 ms**)
- `CDC_Transmit_FS` jest wołane synchronicznie w `sendEntropyToHost`, więc kolejkowanie kosztuje ~40 µs — pomijalne

**Zapas na transakcję USB: 2,56 − 1,47 = ~1,09 ms nominalnie, ~0,3 ms pesymistycznie.**

Worst-case latencja bulk IN to jedna ramka USB FS = **1 ms** (host odpytuje endpoint, gdy ma zakolejkowane URB-y; przy chwilowym braku — cała ramka). Czyli:

> **Nominalnie się wyrabia, ale margines to ~1 ramka USB.** W wariancie pesymistycznym (kara flash, 6 kanałów włączonych, zbieg z długim `HAL_PCD_IRQHandler`) okno się **nie** domyka. Dodatkowo nie ma żadnego backpressure: jeśli host się spóźni, `sendEntropyToHost` trafia w `else` → `blinkFast(99)` i blok **jest po cichu gubiony** (`sender.c:43`).

### Pozostałe multiplikacje

| m | okno | SHA (wall) | zapas na USB | ocena |
|---|---|---|---|---|
| 1 | 0,64 ms | 0,50 ms | 0,14 ms | ❌ okno < 1 ramki USB |
| 2 | 1,28 ms | 0,97 ms | 0,31 ms | ❌ |
| 3 | 1,92 ms | 1,00 ms | 0,92 ms | ⚠️ granicznie |
| **4** | **2,56 ms** | **1,47 ms** | **1,09 ms** | ⚠️ granicznie |
| 5 | 3,20 ms | 1,50 ms | 1,70 ms | ✅ |
| 6 | 3,84 ms | 1,97 ms | 1,87 ms | ✅ |
| 8 | 5,12 ms | 2,47 ms | 2,65 ms | ✅ |
| 9 | 5,76 ms | 2,50 ms | 3,26 ms | ✅ |

Liczba wywołań `compress` per m (`floor(32m/64)` z `update` + 1 z `final`): m=1→1, m=2→2, m=3→2, m=4→3, m=5→3, m=6→4, m=7→4, m=8→5, m=9→5. Stąd niemonotoniczność zapasu.

**Tryb RAW (`'r'`) jest strukturalnie nie do wyrobienia** — okno 0,64 ms przy oknie USB 1 ms; będzie stale gubił bloki i mrugał błędem 99.

---

## 4. Architektura przepływu (dla kontekstu)

```
TIM14 @80 kHz (prio 0)
  └─ TIM14_IRQHandler          stm32f0xx_it.c:149
       ├─ latch PA5 low/high, odczyt GPIOA->IDR
       └─ collectEntropyBits   entropy_collector.c:160
            ├─ select_data           (bit-gather po masce kanałów)
            └─ copyEntropyBitsToBuffer
                 └─ [bufor pełny] buffer_to_process = ...; swapInputBuffer()

pętla główna (main.c:102)
  ├─ updateCollector           entropy_collector.c:153
  │    └─ [buffer_to_process != NULL] processEntropyBlock()
  │         ├─ RAW:  sendEntropyToHost(buffer_to_process, 32)
  │         └─ SHA:  init/update/final → sendEntropyToHost(digest, 32)
  ├─ updateSender              sender.c:25
  └─ ledUpdate

USB (prio 0)
  └─ CDC_Receive_FS            usbd_cdc_if.c:261  ('a'-'f','A'-'F','1'-'9','r','R')
       └─ useRO / setBufferMultiplicity / useRawEntropy → save_config() [!]
```

Podwójne buforowanie (`raw_entropy_block` / `raw_entropy_block2`) jest poprawne: ISR napełnia jeden bufor, pętla główna liczy SHA na drugim. Realnym deadline'em jest więc **jedno pełne okno napełniania** (640·m µs), a nie jakiś krótszy odcinek.

`updateCollector` przeładowuje `buffer_to_process` przy każdym wywołaniu (`0x8000362`), więc nie jest wyoptymalizowane do hoistingu poza pętlę — patrz jednak błąd nr 6.

---

## 5. Błędy wpływające na tę analizę

1. **`main.c` — brak `HAL_TIM_Base_Start_IT(&htim14)`.** Blocker; bez tego nic nie działa.

2. **`entropy_collector.c:90` — `select_data` iteruje `i < 6`, ale kanał 5 ma maskę `0b10000000`** (`entropy_collector.c:63`, PA7 — bo bit 5 to PA5/latch). Kanał PA7 nigdy nie trafia do bufora: komenda `'f'` tylko kasuje flash i mruga. Maska domyślna `0b10011111` daje więc 5, nie 6 bitów na próbkę. Fix: `i < 8` w pętli.

3. **`flash_storage.c` wołany z kontekstu USB IRQ** (`CDC_Receive_FS` → `useRO`/`setBufferMultiplicity` → `save_config`). Erase strony na F0 to ~20–40 ms busy-waitu. `USB_IRQn` i `TIM14_IRQn` mają obie priorytet 0 (`trng.ioc:49-50`), więc na czas erase zbieranie entropii jest całkowicie zatrzymane, a SysTick (prio 3) nie tyka → `HAL_GetTick()` stoi i timeouty we `FLASH_WaitForLastOperation` nie działają. Przenieść zapis do pętli głównej (flaga „dirty”).

4. **`setBufferMultiplicity` (`entropy_collector.c:78`) nie woła `swapInputBuffer()`** (w odróżnieniu od `useRO`). Przy zwiększaniu bufora bajty powyżej starego rozmiaru nie były wyzerowane (`swapInputBuffer` memsetuje tylko `selected_entropy_buffor_size`), a `copyEntropyBitsToBuffer` robi `|=` — pierwszy blok po zmianie miesza się ze starymi danymi. Przy zmniejszaniu następuje przedwczesny flush częściowego bufora. Fix: memset zawsze `MAX_ENTROPY_BLOCK_SIZE` i wołać `swapInputBuffer()` również tutaj.

5. **`swapInputBuffer()` wołane z USB IRQ ściga się z pętlą główną** — może przełączyć się na bufor, na który wciąż wskazuje `buffer_to_process`, i wymemsetować go w trakcie liczenia SHA. Przy resecie trzeba czyścić też `buffer_to_process`.

6. **`buffer_to_process` / `raw_entropy_bit_index` nie są `volatile`** przy wymianie ISR↔main. Aktualnie działa (`updateCollector` nie jest inline'owany), ale to przypadek, nie gwarancja.

7. **Utrata bitów na granicy bufora:** gdy `remaining_space < data_bits_count`, nadwyżkowe bity są odrzucane, a `raw_entropy_bit_index` mimo to jest inkrementowany o `bits_to_write_count`. Do 5 bitów entropii tracone raz na bufor — kosmetyka, nie błąd bezpieczeństwa.

8. **`start_blink` (`haptic.c:32`)** porównuje `mode == BLINK_MODE_FAST_OFF`, podczas gdy `blinkFast`/`blinkSlow` przekazują `BLINK_MODE_FAST_OFF`/`BLINK_MODE_SLOW_OFF` — gałąź `default` ustawia tryb i wychodzi bez skonfigurowania `blink_interval`. Poboczne wobec analizy czasowej, ale warto poprawić, skoro błąd 99 jest jedynym kanałem diagnostycznym.

---

## 6. Rekomendacje

Największy zwrot ma odchudzenie ISR — to on zabiera połowę CPU.

1. **Wyrzucić `HAL_TIM_IRQHandler` z ISR** i zrobić `TIM14->SR = 0;` — ~72 cykle mniej za darmo.
2. **Zamienić pętlę `select_data` na LUT** — ~99 cykli mniej (szczegóły w sekcji 7).
3. Po (1)+(2): ISR ~148 cykli = 25% CPU, dla m=4 SHA spada do ~0,89 ms, zapas rośnie do ~1,24 ms i przestaje być wrażliwy na karę flasha.
4. **Alternatywnie/dodatkowo: zejść z 80 kHz na 40 kHz** (ARR=1199). ISR to wtedy 27% CPU, okno rośnie do 5,12 ms, SHA do 0,92 ms → zapas 4,2 ms. Kosztem połowy przepustowości (6,25 kB/s zamiast 12,5 kB/s przy m=4). Przy 80 kHz i tak warto sprawdzić, czy próbkowanie nie jest szybsze niż czas dekorelacji ringów.
5. **Dodać FIFO 2–4 bloków 32-bajtowych między SHA a USB.** To usuwa wrażliwość na latencję ramki USB całkowicie — liczy się wtedy tylko średnia przepustowość (12,5 kB/s przy m=4, bez problemu dla bulk FS), a nie czas pojedynczej transakcji. Obecnie `sendEntropyToHost` gubi blok, zamiast go zakolejkować. **LUT tego nie zastępuje** — LUT kupuje zapas w budżecie CPU, FIFO kupuje odporność na hosta.
6. Rozważyć `USB_IRQn` na priorytecie 1, żeby długie obsługi kontrolne nie opóźniały próbkowania (albo odwrotnie — TIM14 wyżej, jeśli ważniejsza jest regularność próbkowania).
7. Przenieść `save_config` i rebuild LUT z ISR USB do pętli głównej.

---

## 7. Szczegóły: `select_data` → LUT

### 7.1 Co robi ta pętla

`select_data` (`entropy_collector.c:86`) to **bit-gather** (odpowiednik `PEXT` z BMI2): bierze surowy bajt z `GPIOA->IDR` i upakowuje w ciągłe niskie bity tylko te pozycje, które są ustawione w masce `selected_ro_channels`.

```
maska  = 0b00011111        (PA0..PA4 aktywne)
IDR    = 0b?_?_1_0_1_1_0   → wynik = 0b01011, bits = 5
```

Kosztuje tyle, bo robi to **bit po bicie w runtime**, choć maska jest stała między komendami z USB. Z disasemblacji (`0x80003be`–`0x80003de`), koszt jednej iteracji:

| przypadek | cykle |
|---|---|
| kanał włączony, bit = 1 | 19 |
| kanał włączony, bit = 0 | 17 |
| kanał wyłączony | 11 |

Przy masce domyślnej (5 aktywnych + 1 nieaktywny w zakresie `i<6`): **~99 cykli na próbkę**, czyli przy 80 kHz **7,9 mln cykli/s = 16,5% całego CPU** zmarnowane na przepakowanie 5 bitów. Cała `collectEntropyBits` to 197 cykli, więc pętla to połowa jej kosztu.

### 7.2 Idea

Skoro maska jest stała przez miliony próbek, cały gather można policzyć **raz**, przy zmianie konfiguracji, i w ISR zostawić jedno indeksowanie tablicą.

Indeks bierzemy wprost z surowego bajtu IDR — nie trzeba go maskować, bo wpisy tablicy są zbudowane z uwzględnieniem maski, więc bity 5/6 (PA5 = latch, PA6 = LED, oba wyjścia, ale odczytywalne w IDR) są w niej ignorowane. Istniejący `uxtb` w `TIM14_IRQHandler` (`0x800089e`) już ogranicza indeks do 0..255.

### 7.3 Wariant A — tablica 256 B (najszybszy)

```c
static uint8_t gather_lut[256];   /* IDR -> upakowane bity */
static uint8_t gather_bits;       /* popcount(maska) = bitów na próbkę */

static void rebuildGatherLut(void) {
    const uint8_t mask = configuration.selected_ro_channels;
    for (unsigned v = 0; v < 256; v++) {
        uint8_t res = 0, pos = 0;
        for (unsigned i = 0; i < 8; i++) {      /* 8, nie 6 — naprawia PA7 */
            if (mask & (1u << i)) {
                if (v & (1u << i)) res |= (uint8_t)(1u << pos);
                pos++;
            }
        }
        gather_lut[v] = res;
    }
    uint8_t n = 0;
    for (unsigned i = 0; i < 8; i++) if (mask & (1u << i)) n++;
    gather_bits = n;
}

void collectEntropyBits(uint8_t data) {          /* cała ISR-owa ścieżka */
    copyEntropyBitsToBuffer(gather_lut[data], gather_bits);
}
```

Skompilowane tym samym toolchainem i flagami co projekt (gcc 14.3, `-Oz -mcpu=cortex-m0`) — gather to dokładnie 4 instrukcje:

```
ldr   r3, =gather_bits      2 cyk
ldrb  r1, [r3, #0]          2 cyk
ldr   r3, =gather_lut       2 cyk
ldrb  r0, [r3, r0]          2 cyk      <- ldrb Rd,[Rb,Ro], indeksowanie rejestrem
```

**8 cykli zamiast 116** (99 pętli + 17 prologu/odczytu maski). **Oszczędność ≈ 99 cykli na próbkę.**

Efekt uboczny: pętla budująca tablicę leci po **8 bitach**, więc **naprawia za darmo błąd z PA7**. Bits/próbkę wraca do 6 przy masce domyślnej, co skraca okno napełniania — dla m=4 z 2,56 ms do 2,13 ms. To działa **przeciwko** marginesowi, więc obie zmiany trzeba policzyć razem (sekcja 7.6).

**RAM:** zajęte 5380 B z 6144 (`.data` 396 + `.bss` 3444 + heap/stack 1540), zostaje ~764 B. Tablica 256 B wchodzi z zapasem ~508 B. Heap (`ProjectManager.HeapSize=0x200`) jest nieużywany — do odzyskania 512 B, jeśli potrzeba więcej luzu.

### 7.4 Wariant B — 2×16 B (jeśli szkoda RAM-u)

Rozbicie na nibble: dolna tablica dla bitów 0–3, górna dla 4–7, **z góry przesunięta** o liczbę bitów z dolnego nibbla, żeby w ISR zostało samo `orrs`:

```c
static uint8_t gather_lo[16], gather_hi[16], gather_bits;

/* w rebuild: */
uint8_t lo_bits = popcount8(mask & 0x0F);
for (unsigned v = 0; v < 16; v++) {
    gather_lo[v] = gather(v, mask & 0x0F);
    gather_hi[v] = (uint8_t)(gather(v, mask >> 4) << lo_bits);
}

void collectEntropyBits(uint8_t data) {
    copyEntropyBitsToBuffer(gather_lo[data & 0x0F] | gather_hi[data >> 4], gather_bits);
}
```

Zmierzone na wygenerowanym kodzie: **16 cykli** (3 `ldr` adresów + 3 `ldrb` + `ands` + `lsrs` + `orrs`). Czyli 8 cykli drożej niż wariant A, za 224 B RAM-u mniej. Przy 80 kHz te 8 cykli to 1,3% CPU — wariant A jest wart tych bajtów, ale B jest bezpiecznym fallbackiem.

### 7.5 Spójność tablicy względem ISR

Rebuild trwa ~15 tys. cykli (~0,3 ms); przez ten czas tablica jest w połowie stara, w połowie nowa, a `gather_bits` rozjeżdża się z zawartością wpisów. Opcje:

- **(a) zatrzymać TIM14 na czas rebuildu** — `__HAL_TIM_DISABLE(&htim14)` / rebuild / reset bufora / `__HAL_TIM_ENABLE`. Najprostsze i naturalne, bo zmiana konfiguracji **i tak** unieważnia aktualny bufor (`useRO` już woła `swapInputBuffer`). Utrata 0,3 ms próbkowania przy ręcznej komendzie jest bez znaczenia. **Zalecane.**
- (b) podwójna tablica + podmiana wskaźnika (zapis słowa jest atomowy na M0) — potrzebuje 512 B, w tym budżecie RAM za ciasno.
- (c) budowa na stosie + `memcpy` pod `__disable_irq()` — ~6 µs maskowania, gubi maks. jedną próbkę.

Niezależnie od wariantu: **rebuild musi lecieć z pętli głównej, nie z `CDC_Receive_FS`** — tak samo jak `save_config`. Wzorzec: `CDC_Receive_FS` ustawia tylko flagę `config_dirty`, a pętla główna robi rebuild + zapis + reset bufora.

### 7.6 Bilans po zmianie

Warto to zrobić razem z wyrzuceniem `HAL_TIM_IRQHandler` z ISR:

| | teraz | po LUT + bez HAL |
|---|---|---|
| wejście/wyjście z wyjątku | 28 | 28 |
| wrapper `TIM14_IRQHandler` | 30 | 26 |
| gather (`select_data`) | 116 | 8 |
| reszta `collectEntropyBits` | 81 | 81 |
| `HAL_TIM_IRQHandler` | 72 | 5 |
| **ISR razem** | **327 cyk (54% CPU)** | **~148 cyk (25% CPU)** |
| dla pętli głównej | 45% | **75%** |

Dla m=4, uwzględniając naprawę PA7 (6 bitów/próbkę → okno 2,13 ms zamiast 2,56 ms):

- SHA-256 nad 128 B: 32 000 cykli CPU / 0,75 = **0,89 ms** (było 1,47 ms)
- **Zapas na transakcję USB: 2,13 − 0,89 = 1,24 ms** (było 1,09 ms)

Mimo skrócenia okna o 17% margines rośnie, a co ważniejsze — przestaje być wrażliwy na karę za wait-state flasha: w wariancie pesymistycznym (+20%) zapas to wciąż ~1,0 ms, zamiast 0,3 ms jak teraz.

### 7.7 Dodatek: `buffer_bits_target`

Skoro konfiguracja jest już cache'owana, `copyEntropyBitsToBuffer` przy każdej próbce czyta `use_raw_entropy` i `configuration.selected_entropy_buffor_size` (`ldrb` + `cmp` + `ldrh` + `lsls` ≈ 8 cykli) tylko po to, żeby policzyć `buffor_size_to_use * 8`. Trzymać obok LUT-a gotowe `static uint32_t buffer_bits_target` / `buffer_bytes_target`, aktualizowane w tym samym rebuildzie — kolejne ~8 cykli mniej i, co istotniejsze, **ISR przestaje w ogóle czytać strukturę `configuration`**, co likwiduje wyścig z `setBufferMultiplicity` (błąd nr 4/5).

---

## Aneks: założenia i metoda

- Zegar: HSE 12 MHz × PLL4 = 48 MHz SYSCLK = HCLK = PCLK1 = APB1 timer clock (`trng.ioc:134-148`, `main.c:119`).
- TIM14: presc 0, ARR 599 → update co 600 taktów = 80 kHz (`main.c:175-180`).
- Model cyklowy Cortex-M0 (ARMv6-M), zero wait state; kara `FLASH_LATENCY_1` szacowana osobno jako +15–20% dla kodu z gałęziami.
- Liczby ISR i `compress` policzone ze `Release/trng.list` / `objdump -d` po ścieżkach faktycznie wykonywanych (dla `HAL_TIM_IRQHandler` — ścieżka z samą flagą UIF).
- `compress`: pętla 1 (i=0..15) 117 cykli/iterację, pętla 2 (i=16..63) 157 cykli/iterację, prolog+epilog 123 → ~9 500 cykli/blok 64 B.
- Warianty LUT skompilowane i zdisasemblowane tym samym toolchainem/flagami co projekt, żeby liczby cykli nie były szacunkiem.
- Latencja USB: przyjęte worst-case 1 ramka FS = 1 ms dla bulk IN przy single-buforowanym endpointcie re-armowanym z pętli głównej.
