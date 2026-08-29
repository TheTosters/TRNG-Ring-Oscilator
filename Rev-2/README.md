# multi_rng Rev-2 — hardware true random number generator

A USB true random number generator whose entropy comes from eight free-running
ring oscillators. The board enumerates as a plain CDC-ACM serial port and streams
conditioned random bytes at **50 kB/s**; a handful of single-character commands
change how it runs and print a status report.

This document is meant to be read by someone who has never seen the project. It
covers what the device is, how it works, what was measured and how to reproduce
those measurements, how to talk to it over USB, and how to read what it says
back.

---

## 1. What it is

The physical noise source is **eight ring oscillators**, each a loop of three
inverters. Such a ring free-runs at a frequency set by gate propagation delay,
and its phase drifts unpredictably under thermal and flicker noise in the
transistors. That phase jitter is the only source of entropy in the whole design
— everything else is sampling, health checking and conditioning.

The rings are split into two groups of four, each group latched by its own
74HC174 and read by an **STM32G431CBT6** (Cortex-M4 at 168 MHz):

| group | rings | topology | latch | clock pin |
|---|---|---|---|---|
| T | 1–4 | `SN74LVC3G04` — three inverters in one package | U22 | `latch_b` / PA1 |
| plain | 5–8 | 3 × `SN74LVC1G04` — one inverter per package | U3 | `latch_a` / PB0 |

Two topologies on one board is deliberate: it lets the two be compared under
identical conditions. Measured ring frequencies are **90–130 MHz**.

### Ring to pin map

The latch outputs cross over on the board, so the letter order is **not** the
schematic sheet order. This table is the authority; it is also reproduced in
`firmware/Core/Src/entropy_collector.c` (`ro_port_bit[]`) and in the host tools.

| command letter | ring | MCU pin | latch | ring sheet |
|---|---|---|---|---|
| `a` | 1 | PA8 | B | T-OscilatorRing |
| `b` | 2 | PA0 | B | T-OscilatorRing2 |
| `c` | 3 | PA2 | B | T-OscilatorRing3 |
| `d` | 4 | PA3 | B | T-OscilatorRing1 |
| `e` | 5 | PA4 | A | OscilatorRing1 |
| `f` | 6 | PA5 | A | OscilatorRing2 |
| `g` | 7 | PA6 | A | OscilatorRing4 |
| `h` | 8 | PA7 | A | OscilatorRing3 |

All eight ring outputs sit on port A but not on consecutive bits — PA1 in the
middle is the `latch_b` output — so one `GPIOA->IDR` read captures a whole
sample and a 512-entry lookup table unpacks it.

---

## 2. What changed from Rev-1

| | Rev-1 | Rev-2 |
|---|---|---|
| rings | 6 | **8** |
| latches | 1 | **2** (one per group of four) |
| inverter packaging | `74LVC04` hex, **two rings share a package** | one package per ring |
| MCU | STM32F070F6P6, 48 MHz, Cortex-M0 | STM32G431CBT6, 168 MHz, Cortex-M4 |
| sampling rate | 100 kHz, fixed | 50–600 kHz, **selectable over USB** |
| default operating point | 100 kHz, 9:1 | **300 kHz, 6:1** |
| min-entropy per raw bit | 0.083–0.174 | **0.39–0.66** |
| entropy budget | 0.757 bit per 6-bit sample | **3.12 bit per 8-bit sample** |
| entropy throughput | 75.7 kbit/s | **937 kbit/s** |
| output rate | 8.3 kB/s at 1.13× margin | **50 kB/s at 2.34× margin** |
| health tests | RCT + APT per channel | RCT + APT + **MCV distribution monitor** |
| front panel | one LED | 3 LEDs + 2 buttons (self test, restart) |

The headline is **6.1× more entropy per second**, but the more important change
is qualitative. In Rev-1 the binding estimator was t-Tuple with `t` = 148–214,
meaning sequences of 150+ samples recurred far more often than chance allows —
massive real structure. In Rev-2 `t` = 23–27, **below** the chance floor of ~46
for this sample count, so that estimator finds nothing at all. The millisecond-
scale structure suspected of coming from supply modulation is gone, which is
what the separate packages, ferrite beads and dual LDOs were for.

A second, independent confirmation: in Rev-1 the sum of per-channel estimates
(0.757) and the joint assessment (2.299) disagreed by a factor of 3, proving one
of them wrong. In Rev-2 they agree to within 3% (4.65 vs 4.80 at 100 kHz), so
the budget rests on two measurements that corroborate each other.

---

## 3. How it works

```
8 × ring oscillator (3 inverters each, 90–130 MHz)
        │
        ▼   two 74HC174 latches, clocked together every 3.33 µs (300 kHz)
   GPIOA->IDR read in the TIM6 interrupt          ~90 CPU cycles, 16% of CPU
        │
        ▼   512-entry LUT: selects enabled rings, packs them in ring order
   bit accumulator → 192 B buffer
        │
        ▼   SP 800-90B health tests: RCT, APT, MCV — per buffer, in the main loop
        │       a buffer that fails is discarded here and never conditioned
        ▼
   SHA-256, 6:1 compression                        192 B in → 32 B out
        │
        ▼   batches of 5 blocks
   USB CDC-ACM                                     50 kB/s
```

The sampling interrupt does nothing but pulse both latches, read the port and
hand the value to the collector. Everything expensive — health tests, hashing,
USB — runs in the main loop, so the sampling path stays at 16% of the CPU even
at 300 kHz.

### Why 300 kHz and 6:1

Entropy per sample falls as the sampling rate rises (jitter gets less time to
accumulate) while samples per second rise. The product is what the device can
deliver, and it happens to be exactly what determines output:

```
compression needed for a 2× margin:  m = ceil(16 / H_sample)
output rate:                         f_s / m  bytes/s   ∝  f_s × H_sample
```

A 256-bit block needs 512 bits of entropy for the 2× margin normally required to
treat the output of a vetted conditioning function as full entropy. Measured
across the whole rate table (section 6), 300 kHz maximises output at 50 kB/s.
250 kHz gives the same 50 kB/s but only 1.0% of headroom before it would need
7:1, where 300 kHz has 14.6% — that margin, not the throughput, is why 300 kHz
was chosen.

---

## 4. Talking to the device

The board appears as `/dev/ttyACM0` (Linux). Baud rate is irrelevant — it is
USB CDC, not a real UART. **The entropy stream and the command channel share one
endpoint**, which shapes the whole protocol.

### Commands

Every command byte must be immediately preceded by `!`. This is not decoration.
If anything echoes the entropy stream back to the device — a tty still in cooked
mode between `open()` and raw mode, ModemManager probing the port, a second
reader — then random bytes arrive at the command parser. Roughly 1 byte in 9
would be a valid command without the prefix, flash writes included. Requiring
`!` drops that to 29 in 65536 per position.

| command | effect |
|---|---|
| `!a` … `!h` | enable ring 1…8 |
| `!A` … `!H` | disable ring 1…8 |
| `!1` … `!9` | SHA-256 compression 1:1 … 9:1 (buffer = N × 32 B) |
| `!t` + digit | sampling rate, see table below — **the digit is sent unprefixed** |
| `!r` | RAW mode on: raw bits go straight to USB, no SHA |
| `!R` | RAW mode off (back to SHA-256) |
| `!s` | save current settings to flash |
| `!l` | reload settings from flash, discarding unsaved changes |
| `!?` or `!/` | print the status report and freeze the stream until the next byte |

Sampling rates for `!t`. Every value divides the 168 MHz timer clock exactly, so
the rate is nominal with no rounding error:

| digit | rate | digit | rate |
|---|---|---|---|
| `0` | 50 kHz | `5` | **300 kHz** (default) |
| `1` | 100 kHz | `6` | 350 kHz |
| `2` | 150 kHz | `7` | 400 kHz |
| `3` | 200 kHz | `8` | 500 kHz |
| `4` | 250 kHz | `9` | 600 kHz |

So `!t5` selects 300 kHz: `!`, `t`, then a bare `5`. A non-digit after `!t`
cancels the command rather than being reinterpreted, so a truncated `!t` cannot
leave the parser primed to swallow the next real command.

Settings live in RAM until `!s` writes them to flash. `console.py` in
`host_tools/` sends one raw byte per keystroke and handles the `!t` parameter
for you.

### Reading the status report

`!?` prints something like:

```
CH=ABCDEFGH N=8
FS=300000Hz
BUF=192B RATIO=6:1 CFG=192
MODE=SHA256
UP=50000B/s
DROP usb=0 col=0 health=0 mcv=0
MCV=7.45/8
RX=74 CNT=19
```

| field | meaning |
|---|---|
| `CH=` | enabled rings as their command letters, `.` for disabled. Reads directly as the commands that produced it |
| `N=` | how many rings are enabled, i.e. bits per sample |
| `FS=` | sampling frequency, derived from the timer registers — this is measured, not assumed |
| `BUF=` | bytes collected before conditioning |
| `RATIO=` | compression: how many 32 B blocks fold into one output block |
| `CFG=` | the *requested* buffer size. Differs from `BUF` only if a command reached the config but has not been applied yet |
| `MODE=` | `SHA256` or `RAW` |
| `UP=` | bytes per second the device will deliver at these settings |
| `DROP usb=` | batches discarded because USB was still busy |
| `DROP col=` | buffers discarded because the main loop did not collect the previous one in time |
| `DROP health=` | buffers discarded because RCT or APT failed |
| `DROP mcv=` | windows where the sample distribution collapsed — see below |
| `MCV=x/y` | min-entropy of the most common sample value over the last window, out of `y` bits. `-` until the first window closes |
| `RX=` | last non-report byte the host sent, in hex — useful when a command seems ignored |
| `CNT=` | total bytes received since the last report |

**All four DROP counters reset when the report is read**, so every report answers
"what was dropped since the previous report". Non-zero `usb` or `col` means the
stream has gaps in time; in RAW mode expect this above roughly 250 kHz, where
the raw stream outruns what CDC can carry.

**How to read `MCV`.** It is a *health* indicator, not a quality one. Measured on
this board it sits at 7.43–7.49 out of 8 and barely moves from 50 kHz to 600 kHz
— even though real min-entropy over that range falls from 6.61 to 3.12 bit per
sample. The reason is that MCV only sees the distribution of a single sample,
while the entropy loss at higher rates comes from *serial* correlation, which is
invisible to it. What MCV does catch is rings synchronising, to each other or to
an injected signal: eight synchronised rings read about 1.0, and four out of
eight already read 4.95. A green light here does **not** mean "entropy is fine";
it means "the rings are still independent and alive".

---

## 5. Front panel

| LED | colour | meaning |
|---|---|---|
| led1 (PB5, D2) | green | heartbeat — one flash per 65536 collected bytes (~4.6 Hz at 300 kHz), so it shows both that the stream is alive and roughly how fast. Also the self-test PASS verdict |
| led2 (PB6, D3) | yellow | RAW mode (steady), acquisition paused (blinking), self test running (steady) |
| led3 (PB7, D4) | red | dropped batches, health failures, self-test FAIL verdict |
| D1 | green | power, wired to VCC, not controllable |

| button | label on board | pin | function |
|---|---|---|---|
| SW1 | SW1 | PB13 | **self test** — enables all eight rings, samples for ~1 s (yellow steady), then shows green or red for 3 s |
| SW2 | SW2 | PB11 | **restart acquisition** — stops sampling, drops buffers and health history, waits 3 s (yellow blinking), resumes. Settings are untouched |

The self test checks that every ring changes, that its bias is in a wide band,
and that the sample distribution has not collapsed. It detects a dead, stuck or
synchronised ring — **not** entropy quality; that is what the host tools are for.

Two hardware notes that are easy to trip over:

- The board has a **third button (SW3, PB12) that the firmware cannot see** — the
  `.ioc` does not configure PB12, and instead configures PB8, which the schematic
  leaves unconnected. Using it means adding PB12 in CubeMX.
- **Nothing is wired to BOOT0**, so the board cannot be put into the DFU
  bootloader by holding a button. See section 8.

---

## 6. What was measured, and where the evidence is

All figures come from `ea_non_iid` of the NIST SP 800-90B *EntropyAssessment*
suite, driven by the scripts in `../host_tools/`. Raw captures are not in the
repository — over 400 MB and reproducible from the board in minutes — but every
analysis output is, under `../host_tools/Rev_2/`.

### Rate sweep — the basis for the operating point

`../host_tools/Rev_2/sweep/sweep_summary.txt`, one subdirectory per rate.

| f_s | sum H [bit/sample] | % of estimator ceiling | m for 2× | output | margin | stream lost |
|---|---|---|---|---|---|---|
| 50 kHz | 6.609 | 94.4% | 3 | 16.7 kB/s | 2.48× | 0% |
| 100 kHz | 5.101 | 72.9% | 4 | 25.0 kB/s | 2.55× | 0% |
| 150 kHz | 4.122 | 58.9% | 4 | 37.5 kB/s | 2.06× | 0% |
| 200 kHz | 3.083 | 44.0% | 6 | 33.3 kB/s | 2.31× | 0% |
| 250 kHz | 3.233 | 46.2% | 5 | 50.0 kB/s | 2.02× | 0% |
| **300 kHz** | **3.122** | **44.6%** | **6** | **50.0 kB/s** | **2.34×** | 0.004% |
| 350–600 kHz | — | — | — | — | — | 0.6–14% ⚠ |

**The estimator has a ceiling below 1.0**, and it matters. On perfectly random
data of the same size `ea_non_iid` scores 0.874 bit/bit, not 1.000 — so the
`% of ceiling` column, not the raw figure, says how much room is left.

**Rows above 300 kHz are unusable and their numbers are inflated, not merely
noisy.** Above that rate the RAW stream outruns USB, the firmware drops blocks,
and gaps in the stream decorrelate consecutive samples — so the estimators find
less structure and report *higher* entropy. At 600 kHz, with 14% of the stream
lost, `sum H` reads 4.03, as good as 150 kHz. Always read the `lost` column
first.

### Coupling between rings

`../host_tools/Rev_2/100kHz/crosstalk.txt` and the per-rate `crosstalk.txt`.

Three ring pairs are strongly correlated: **B-C, E-F and G-H**, at r ≈ 0.07–0.10
(74–102 σ). The pattern has a single, exceptionless explanation — those are
exactly the pairs sitting on **adjacent data inputs of the same 74HC174**:

| distance in latch bit index | pairs | measured \|r\| |
|---|---|---|
| 1 (adjacent) | B-C, E-F, G-H | 0.072 … 0.100 |
| 2 | A-B, C-D, F-G | 0.0025 … 0.0062 |
| ≥ 3 | the other 22 pairs | ≤ 0.0018 (noise) |

It is **not** physical proximity: the two closest ring pairs on the PCB (A-B at
10.4 mm, C-D at 11.0 mm) are *not* coupled, while B-C at 12.9 mm is one of the
strongest. And it does not follow the sampling rate — across a 12× change the
spread is 1–2% of its own size — which points at crosstalk at the instant of
latching rather than rings pulling each other's phase.

**It costs 0.38% of the entropy budget**, and a control experiment
(`subset_control_rev2.py`) found no measurable loss of joint entropy: the coupled
four-ring subset scored *higher* than the clean one. So it is a documented
curiosity, not a problem to fix. Selecting rings to avoid it would give up half
the budget to recover 0.38%.

### Per-channel figures at the default rate

`../host_tools/Rev_2/sweep/300kHz/results.txt`. Note that the budget is the
**sum of per-channel estimates**, which assumes independence — an assumption
checked in the coupling analysis above, not merely asserted.

---

## 7. Reproducing the measurements

Everything runs from `../host_tools/`. You need `ea_non_iid` from NIST
EntropyAssessment on `PATH`, and the Python environment:

```bash
cd host_tools
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Use `.venv/bin/python`, not the system `python3` — numpy and pyserial live in the
virtualenv.

### The full rate sweep (~13 minutes)

```bash
.venv/bin/python sweep_rate_rev2.py --outdir Rev_2/sweep
```

Captures at every rate, assesses each, and writes `sweep_summary.txt` with a
recommended operating point. Rates are visited in **shuffled order** on purpose:
sweeping monotonically would let the board's warm-up drift correlate with the
rate and be mistaken for a rate effect.

### A single rate, in detail (~4 minutes)

```bash
./collect_all_rev2.sh both Rev_2/100kHz
.venv/bin/python channel_crosstalk_rev2.py Rev_2/100kHz/channelA-H.bin \
    --entropy-file Rev_2/100kHz/results.txt
```

`collect_all_rev2.sh` captures each ring in isolation, all eight together, and
each group of four, then runs `ea_non_iid` on all of them.

### Checking a claim about ring subsets

```bash
.venv/bin/python verify_subset_claim_rev2.py Rev_2/100kHz/channelA-H.bin \
    --entropy-file Rev_2/100kHz/results.txt
```

Enumerates all 70 four-ring subsets and — the point of the tool — runs a
**split-half stability test**: pick the best subset on one half of the data, look
up its rank on the other half. It exists because "best subset" numbers picked
from the same data they are then quoted from are a classic way to fool yourself.

### Does coupling actually cost entropy? (~30 minutes)

```bash
.venv/bin/python subset_control_rev2.py --outdir Rev_2/100kHz/control \
    --entropy-file Rev_2/100kHz/results.txt --baseline-reps 3
```

Captures a clean and a deliberately coupled four-ring subset with nearly equal
channel sums, and compares each against a random-data baseline of the same size.
The baseline is not optional: a joint assessment comes out *higher* per bit than
the channels it is made of even when they are provably independent, so only the
difference between the two subsets means anything.

### Tool reference

| tool | what it does |
|---|---|
| `console.py` | interactive raw-byte console; sends nothing on its own, unlike a modem terminal |
| `usb_read.py` | capture a raw byte stream to a file |
| `unpack_single_channel.py` | bit-packed stream → one sample per byte, for `ea_non_iid` |
| `unpack_stream.py` | extract one channel from an interleaved stream |
| `collect_all_rev2.sh` | capture + assess everything at one rate |
| `channel_crosstalk_rev2.py` | correlation and mutual information between rings |
| `check_streams_corelation_rev2.py` | de-interleave, correlation matrix, autocorrelation, Markov, Welch spectrum |
| `sweep_rate_rev2.py` | the rate sweep and operating point recommendation |
| `verify_subset_claim_rev2.py` | split-half test of "best subset" claims |
| `subset_control_rev2.py` | control experiment on the entropy cost of coupling |

The `_rev1` variants of the same tools target the Rev-1 board (6 rings, one
latch); `usb_read.py`, `console.py` and the two `unpack_*` tools serve both.

**Bit order matters.** The firmware packs LSB-first. Every tool defaults to
`little` where it counts, but `unpack_single_channel.py` and `unpack_stream.py`
default to MSB-first for historical reasons — always pass `--bit-order little`.
Getting this wrong silently reorders samples in time, which is exactly what the
serial-dependence estimators measure, so the resulting figures are meaningless.
This bit Rev-1 once: its first published series was inflated by 8–33%.

---

## 8. Flashing

A ready-to-flash image is in `prebuilt/`, so you do not need a toolchain:

| file | |
|---|---|
| `prebuilt/trngrev2.bin` | raw binary for 0x08000000, 22 108 B |
| `prebuilt/trngrev2.elf` | same image with symbols, for a debugger |

With an ST-Link:

```bash
STM32_Programmer_CLI -c port=SWD -w prebuilt/trngrev2.bin 0x08000000 -rst
# or
openocd -f interface/stlink.cfg -f target/stm32g4x.cfg \
        -c "program prebuilt/trngrev2.bin 0x08000000 verify reset exit"
```

Over USB DFU:

```bash
dfu-util -a 0 -s 0x08000000:leave -D prebuilt/trngrev2.bin
```

**DFU needs a way into the bootloader, and this board has none via a button** —
BOOT0 is not wired to anything. Either set `nBOOT_SEL=0` and pull PB8 high by
hand, or use SWD.

### Building it yourself

The project is an STM32CubeIDE project (`firmware/trngrev2.ioc`). Build the
Release configuration. To get a `.bin`, enable *Project Properties → C/C++ Build
→ Settings → MCU/MPU Post build outputs → Convert to binary file*, which the
project does not do by default; otherwise convert by hand:

```bash
arm-none-eabi-objcopy -O binary Release/trngrev2.elf Release/trngrev2.bin
```

The image in `prebuilt/` was built with `arm-none-eabi-gcc 10.2.1`, `-Os`,
newlib-nano. A CubeIDE build will differ in byte layout while behaving
identically — treat CubeIDE Release as canonical if the two ever disagree.

---

## 9. Firmware layout

| file | contents |
|---|---|
| `Core/Src/entropy_collector.c` | the substance: LUT, bit accumulator, health tests, MCV monitor, self test, USB command handlers, status report |
| `Core/Src/haptic.c` | LED channels — heartbeat, blink sequences, timed verdicts |
| `Core/Src/buttons.c` | debounced polling of the two front panel buttons |
| `Core/Src/sender.c` | non-blocking USB transmit state machine |
| `Core/Src/flash_storage.c` | settings in the last 2 KB flash page, 64-bit programming |
| `Core/Src/stm32g4xx_it.c` | `TIM6_DAC_IRQHandler` — the sampling interrupt |
| `Core/Src/tinycrypt/` | SHA-256 |
| `USB_Device/App/usbd_cdc_if.c` | `CDC_Receive_FS` — the command parser |

Settings are stored in flash page 63 at `0x0801F800`, 8 bytes, guarded by a magic
cookie that is bumped whenever the record changes meaning or the recommended
operating point moves. **After flashing a firmware with a new cookie the device
comes up on defaults once**, discarding a previously saved configuration — that
is deliberate, so a saved record cannot silently override a new operating point.

---

## 10. Known limitations

These do not overturn anything above, but they bound what may be claimed.

1. **One board, one temperature.** Every figure comes from a single specimen at
   room temperature on USB power. Ring oscillators are sensitive to supply and
   temperature; a formal assessment needs the full operating envelope and more
   than one unit.
2. **No restart tests.** SP 800-90B wants 1000 restarts × 1000 samples, a
   different capture procedure with a power cycle between samples.
3. **Health test cutoffs are deliberately loose.** RCT = 376 and APT = 1007 are
   derived for H = 0.08 bit/channel, the worst Rev-1 channel; Rev-2 measures
   0.390 at 300 kHz, which would give RCT = 78. Tighter cutoffs would catch a
   degrading ring sooner — and there is an argument that they should, since the
   6:1 compression assumes the 300 kHz budget — but they would also raise false
   alarms on a colder or slower specimen. Left conservative until point 1 is
   resolved. The reasoning is in the code.
4. **MCV misses a single synchronised ring.** One deterministic channel out of
   eight lowers sample min-entropy by about one bit, which sits inside the normal
   spread. Four or more synchronised rings are caught with a wide margin.
5. **The budget assumes independence.** It is the sum of per-channel estimates.
   Coupling was measured (0.38% of budget, no detectable joint loss), but the
   sum remains an assumption resting on that measurement.
6. **Above 300 kHz the source cannot be assessed**, because assessment needs RAW
   mode and RAW at those rates outruns USB. Those rates remain usable in SHA
   mode; their entropy is simply unknown. Extrapolation suggests 400 kHz might
   reach ~57 kB/s. Capturing with four rings enabled halves the stream and would
   let them be measured.

**What this device is good for as it stands:** a seed source for a host DRBG, or
bit-for-bit randomness at the default 6:1, which puts 599 assessed entropy bits
into every 256-bit output block. **What it is not:** a certified generator —
points 1 through 3 would all have to be closed first.

---

*Rev-1, its measurements and how it differs: [`../Rev-1/README.md`](../Rev-1/README.md).*
*Rev-1 entropy assessment in detail: [`../Rev-1/entopy_tests/summary.md`](../Rev-1/entopy_tests/summary.md).*
*The supply filtering on this board — ferrites, capacitors and the ring bias
resistors — was chosen with SPICE. Those scripts live under
[`../Rev-1/simu/ro-simu/spice/`](../Rev-1/simu/ro-simu/spice/README.md) despite
the directory name: `pdn_rev2_*.py` and `rev2_ring_freq.py` are Rev-2 design work.*
