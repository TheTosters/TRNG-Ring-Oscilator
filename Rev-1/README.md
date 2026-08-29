# multi_rng Rev-1 — hardware true random number generator

The first board of the project: a USB true random number generator built on six
ring oscillators. It works, it is measured and documented, and it is superseded
by [Rev-2](../Rev-2/README.md), which produces **6.1× more entropy per second**.

Rev-1 is worth reading for two reasons. It is the baseline every Rev-2 figure is
compared against, and its measurements are what drove the Rev-2 redesign — the
coupling analysis here is the reason Rev-2 gives every ring its own inverter
package.

---

## 1. What it is

Six ring oscillators, each three inverters in a loop, free-running and drifting
under thermal and flicker noise in the transistors. That phase jitter is the
entropy source; everything else is sampling and conditioning.

All six outputs are latched by one shared 74HC174 pulse and read by an
**STM32F070F6P6** (Cortex-M0 at 48 MHz):

| pin | role |
|---|---|
| PA0, PA1, PA2, PA3, PA4, PA7 | ring inputs, channels **A**–**F** |
| PA5 | latch output — one pulse captures all six at once |
| PA6 | status LED |
| PA11 / PA12 | USB (CDC-ACM) |

Channel F sits on PA7 rather than PA5 because PA5 is taken by the latch — the
same "not on consecutive bits" quirk that Rev-2 inherited, handled the same way,
with a lookup table.

The inverters are **three `74LVC04` hex packages (U1, U2, U3)**: six gates each,
eighteen gates total, so **two complete rings share every package**. That detail
turns out to matter — see section 4.

---

## 2. Processing chain

```
6 × ring oscillator (3 inverters each)
        │
        ▼   one 74HC174 latch on PA5, pulsed every 10 µs (100 kHz, TIM14)
   GPIOA->IDR read in the TIM14 interrupt        ~327 cycles — 54% of the CPU
        │
        ▼   256-entry LUT: selects enabled rings, packs them
   bit accumulator → 288 B buffer
        │
        ▼   SP 800-90B health tests: RCT and APT, per channel, in the main loop
        ▼
   SHA-256, 9:1 compression                      288 B in → 32 B out
        │
        ▼   batches of 5 blocks
   USB CDC-ACM                                   8.3 kB/s
```

The sampling interrupt eating **54% of the CPU** is the defining constraint of
this board. It is why compression below 3:1 is not viable at 100 kHz, and why
the sampling rate could not be raised — the SHA-256 ceiling sits at 130–190 kHz
depending on compression. The timing analysis is in
[`firmware/analiza.md`](firmware/analiza.md) (in Polish). Rev-2's Cortex-M4 at
168 MHz drops the same interrupt to 16% of the CPU, which is what let the
sampling rate become a tunable parameter at all.

---

## 3. Talking to the device

The board appears as `/dev/ttyACM0`. The entropy stream and the command channel
share one endpoint, so **every command byte must be preceded by `!`** — without
it, any echo of the random stream back to the device would reprogram it, flash
writes included.

| command | effect |
|---|---|
| `!a` … `!f` | enable ring A…F |
| `!A` … `!F` | disable ring A…F |
| `!1` … `!9` | SHA-256 compression 1:1 … 9:1 |
| `!r` / `!R` | RAW mode on / off |
| `!s` | save settings to flash (page at `0x08007000`) |
| `!l` | reload settings from flash |
| `!?` or `!/` | status report, freezes the stream until the next byte |

Rev-1 has **no `!t` command** — the sampling rate is fixed at build time
(`htim14.Init.Period`, with values for 60–100 kHz in a comment in `main.c`).

### The status report

```
CH=ABCDEF N=6
FS=100000Hz
BUF=288B RATIO=9:1 CFG=288
MODE=SHA256
UP=8333B/s
DROP usb=0 col=0 health=0
RX=74 CNT=19
```

| field | meaning |
|---|---|
| `CH=` | enabled rings as their command letters, `.` for disabled |
| `N=` | rings enabled, i.e. bits per sample |
| `FS=` | sampling frequency, derived from the timer registers |
| `BUF=` | bytes collected before conditioning |
| `RATIO=` | how many 32 B blocks fold into one output block |
| `CFG=` | requested buffer size; differs from `BUF` only if a command has not been applied yet |
| `MODE=` | `SHA256` or `RAW` |
| `UP=` | bytes per second delivered at these settings |
| `DROP usb=` | batches dropped because USB was busy |
| `DROP col=` | buffers dropped because the main loop was too slow — the one to watch on this board |
| `DROP health=` | buffers discarded on an RCT or APT failure |
| `RX=` / `CNT=` | last non-report byte received, in hex, and total bytes received |

Counters reset when the report is read. Rev-2 adds `mcv=` and `MCV=` fields; the
rest is identical, so a parser for one report reads the other.

---

## 4. What was measured

Full detail, methodology and caveats: **[`entopy_tests/summary.md`](entopy_tests/summary.md)**.
Raw `ea_non_iid` output for five sampling frequencies is in the same directory
(`results_*kHz_lsb.txt`), and the analysis outputs are under
`../host_tools/Rev_1/`.

### Per-channel min-entropy

Bits of min-entropy per bit of raw stream, at 100 kHz:

| A | B | C | D | E | F | sum |
|---|---|---|---|---|---|---|
| 0.117 | 0.120 | 0.083 | 0.111 | 0.152 | 0.174 | **0.757** |

A raw bit carries roughly **one sixth of its nominal value**. The stream is
heavily correlated and unusable without conditioning. The binding estimator is
t-Tuple with `t` = 148–214 — sequences of 150+ samples recur three to five times
more often than chance allows, against a chance floor of ~46. That is the
signature of **oversampling**: consecutive samples of one ring read almost the
same phase, because jitter had no time to accumulate between latch pulses.

### Sampling frequency

Entropy per sample does not grow with frequency, but entropy per second does:

| f_s | bit/sample (sum) | entropy [kbit/s] |
|---|---|---|
| 60 kHz | 0.907 | 54.4 |
| 70 kHz | 0.797 | 55.8 |
| 80 kHz | 0.850 | 68.0 |
| 90 kHz | 0.805 | 72.4 |
| **100 kHz** | 0.757 | **75.7** |

Throughput rises monotonically and flattens at the top — +33% from 60 to 90 kHz
but only +4.5% from 90 to 100. 100 kHz is the optimum among the rates tested,
and higher was not reachable because of the CPU ceiling.

### Coupling between rings — the finding that shaped Rev-2

Measured with `channel_crosstalk_rev1.py` on the simultaneous six-channel
capture. Five of fifteen pairs are statistically coupled, with the strongest at
r = 0.0227 (27 σ). Consistently significant across frequencies: **A-B, C-D and
E-F** — three disjoint pairs.

That maps exactly onto the packaging. Three `74LVC04` packages, two rings each:
U1 = {A,B}, U2 = {C,D}, U3 = {E,F}. Rings sharing a die, supply pins and ground
bounce are the coupled ones.

The cost was small — total mutual information 0.00096 bit per sample, **0.13% of
the budget** — so all six channels stayed enabled. But the conclusion for the
next revision was obvious, and Rev-2 acted on it: one inverter package per ring.
(Rev-2 then found a *different* coupling mechanism, adjacent data inputs on the
latch, roughly 4× stronger and still costing well under 1%. See
[`../Rev-2/README.md`](../Rev-2/README.md), section 6.)

### What an output block carries

At 100 kHz with six channels the conservative budget is 75.7 kbit/s. One SHA-256
block is 256 bits:

| compression | entropy per block | entropy bits per output bit |
|---|---|---|
| 4:1 | 129 bit | 0.50 |
| 8:1 | 258 bit | 1.01 |
| **9:1 (default, maximum)** | **290 bit** | **1.13** |

This is why the default is 9:1: it is the only setting that clears one entropy
bit per output bit, and even then by just 1.13×. It never reaches the 2× margin
usually required to treat conditioned output as full entropy — the firmware
cannot compress further. Rev-2 clears 2× at 6:1 while producing six times more.

---

## 5. Reproducing the measurements

From `../host_tools/`, with `ea_non_iid` on `PATH` and the virtualenv set up
(see [`../Rev-2/README.md`](../Rev-2/README.md) section 7):

```bash
./collect_all_rev1.sh both Rev_1/100kHz
.venv/bin/python channel_crosstalk_rev1.py Rev_1/100kHz/channelA-F.bin
```

`collect_all_rev1.sh` captures each ring in isolation plus one simultaneous
six-channel capture, unpacks everything and runs `ea_non_iid`. The coupling
analysis needs the simultaneous capture — the per-channel files were taken at
different times and cannot be cross-correlated.

**Bit order.** The firmware packs LSB-first; `unpack_single_channel.py` defaults
to MSB-first, so `--bit-order little` is mandatory. This is not pedantry: the
first published series for this board was unpacked MSB-first and came out
inflated by 8–20% on single channels and 20–33% on the combined stream. The
`results_*kHz.txt` files in `../host_tools/Rev_1/` are that defective series,
kept only for comparison; the authoritative ones are `results_*kHz_lsb.txt` in
`entopy_tests/`.

---

## 6. What else is here

| directory | contents |
|---|---|
| `firmware/` | STM32CubeIDE project. `analiza.md` is a cycle-level timing analysis of the sampling path (Polish) |
| `hardware/` | KiCad project, `RO-Trng.kicad_sch` / `.kicad_pcb`, JLCPCB production files |
| `entopy_tests/` | the entropy assessment: `summary.md` plus raw `ea_non_iid` output |
| `simu/` | SPICE simulations of the ring oscillators — supply sensitivity, coupling through the supply rail, and why the measured frequency disagreed with simulation |
| `prebuilt/` | ready-to-flash image, see below |

The SPICE work in `simu/ro-simu/spice/README.md` (Polish) is worth a look if you
care about the analogue side. Two results stand out: **dF/dVCC = 81.4 MHz/V**,
meaning a millivolt of supply ripple shifts a ring by 81 kHz, and an unresolved
3× discrepancy between simulated (157 MHz) and oscilloscope-measured (~50 MHz)
ring frequency, most likely probe loading. That discrepancy is worth knowing
about when comparing against Rev-2's measured 90–130 MHz — the two may not have
been measured the same way.

---

## 7. Flashing

```bash
dfu-util -a 0 -s 0x08000000:leave -D prebuilt/trng.bin
```

`prebuilt/trng.bin` (18 752 B) and `prebuilt/trng.elf` are the STM32CubeIDE
Release build. Unlike Rev-2, this board has a usable BOOT0 pin, so holding it
while plugging in the USB cable enters the DFU bootloader.

To rebuild: open `firmware/` in STM32CubeIDE and build Release, which already
produces a `.bin`.

---

## 8. Status

Rev-1 is complete and measured, and is not being developed further. Its known
limitations were the starting point for Rev-2:

- raw bits carry ~1/6 of their nominal entropy, and 9:1 conditioning only just
  reaches 1.13× the output length
- the sampling interrupt takes 54% of the CPU, capping both the sampling rate
  and how low compression can go
- two rings per inverter package produce measurable, if cheap, coupling
- no restart tests, one specimen, room temperature only

Rev-2 addresses the first three. The fourth applies to both.

---

*Rev-2, its measurements and how it differs: [`../Rev-2/README.md`](../Rev-2/README.md).*
