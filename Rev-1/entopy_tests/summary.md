# TRNG multi_rng Rev-1 — entropy source quality summary

Orientation document: what this project is, how the quality of its randomness source was
measured, and what those measurements imply for the default configuration. The figures come
from **`ea_non_iid`** of the NIST SP 800-90B *EntropyAssessment* suite, driven by
[`host_tools/collect_all.sh`](../../host_tools/collect_all.sh), plus a cross-channel coupling
analysis from [`host_tools/channel_crosstalk.py`](../../host_tools/channel_crosstalk.py).

**The authoritative results are `results_*kHz_lsb.txt`** (LSB-first unpacking, matching how
the firmware packs bits). The `results_*kHz.txt` files are a first, **defective** series —
unpacked MSB-first, which reordered samples in time and inflated the estimates by 8–20%
(single channels) and 20–33% (combined stream). They are kept for comparison only and should
not be quoted.

---

## 1. What the device is

**A hardware random number generator built on ring oscillators.**

The physical noise source is **six independent ring oscillators**, each built from **three
NOT gates** wired into a loop. Such a ring free-runs at a frequency set by the gates'
propagation delays, and its phase drifts unpredictably under thermal and flicker noise in
the transistors (phase jitter). That jitter is the only source of entropy in the whole
design — everything else is just sampling and conditioning.

The six oscillator outputs feed the inputs of an **STM32F070F6P6** microcontroller
(Cortex-M0, 48 MHz) and are latched by one shared pulse:

| pin | role |
|---|---|
| PA0, PA1, PA2, PA3, PA4, PA7 | oscillator inputs, channels **A**–**F** |
| PA5 | latch output (one pulse captures all six at once) |
| PA6 | status LED |
| PA11 / PA12 | USB (CDC-ACM) |

Channel F sits on PA7 rather than PA5 because PA5 is taken by the latch.

## 2. Processing chain

```
6 × RO (3 × NOT)  →  latch (PA5)  →  port read in the TIM14 interrupt @ 100 kHz
                                     ↓
                            channel-select LUT  (enabled bits only, packed)
                                     ↓
                          bit accumulator → 288 B buffer
                                     ↓
                 SP 800-90B health tests (RCT + APT, per channel)
                                     ↓
                              SHA-256  (9:1 compression)
                                     ↓
                     32 B per block → USB CDC (batches of 5 blocks)
```

The timer samples at **100 kHz**; with all six channels enabled that is 6 bits per sample,
i.e. **75 000 B/s of raw stream**. After SHA-256 conditioning at the default **9:1** the
output is **8 333 B/s**. A buffer that fails a health test is discarded before conditioning,
so nothing derived from a failing source reaches USB.

The device also has a **RAW mode** (command `r`) in which raw bits go straight to USB with no
SHA — that is the mode used to collect all the data assessed below.

## 3. Measurement methodology

`collect_all.sh` captures **1 MiB** of RAW data per channel in isolation (one channel
enabled, the rest disabled) plus one combined capture with all six enabled. It then unpacks
the stream into a one-sample-per-byte format and runs `ea_non_iid`:

- single channels: `ea_non_iid -v -i outputX.bin 1` → **8 388 608** 1-bit samples
- combined: `ea_non_iid -v -i outputA-F.bin 6` → **1 398 101** 6-bit samples (64 symbols)

Both clear the SP 800-90B minimum of 1 000 000 samples.

`ea_non_iid` runs ten min-entropy estimators (MCV, Collision, Markov, Compression, t-Tuple,
LRS, MultiMCW, Lag, MultiMMC, LZ78Y) and reports the **minimum over all of them** — the most
pessimistic estimate. The measurement was repeated at five sampling frequencies: 60, 70, 80,
90 and 100 kHz.

## 4. Results — individual channels

Min-entropy in bits per bit of raw stream (`H_original`):

| FS | A | B | C | D | E | F | min | mean | **sum** |
|---|---|---|---|---|---|---|---|---|---|
| 60 kHz | 0.1414 | 0.1451 | 0.1590 | 0.1478 | 0.1618 | 0.1516 | 0.1414 | 0.1511 | **0.9068** |
| 70 kHz | 0.1113 | 0.1273 | 0.1474 | 0.1234 | 0.1621 | 0.1255 | 0.1113 | 0.1328 | **0.7970** |
| 80 kHz | 0.1514 | 0.1574 | 0.1360 | 0.1220 | 0.1570 | 0.1264 | 0.1220 | 0.1417 | **0.8502** |
| 90 kHz | 0.1157 | 0.1110 | 0.1495 | 0.1546 | 0.1496 | 0.1243 | 0.1110 | 0.1341 | **0.8047** |
| **100 kHz** | 0.1169 | 0.1196 | **0.0832** | 0.1112 | 0.1515 | **0.1743** | 0.0832 | 0.1261 | **0.7566** |

Conclusions:

- **A single raw bit carries roughly 0.08–0.17 bits of entropy**, at best one sixth of its
  nominal value. The raw stream is heavily correlated and **absolutely unusable without
  conditioning**.
- The spread between channels is wide (2.1× between best and worst at 100 kHz) and **not
  stable across runs** — channel C is the best at 60 kHz (0.1590) and the worst at 100 kHz
  (0.0832), while channel F does the opposite. This suggests the per-channel differences are
  mostly measurement noise rather than a durable property of a particular ring.
- The binding estimator is **t-Tuple** (channels A–D, `t` = 148–214) or **Compression**
  (channels E and F). Both say the same thing in different words: the stream is predictable
  at large scale — t-Tuple because sequences of ~150–210 samples recur far more often than
  randomness would allow; Compression because the stream compresses far below its nominal
  length. This is the classic signature of **oversampling**: consecutive samples of one ring
  read almost the same phase, because jitter had no time to accumulate between latch pulses.

## 5. Results — combined six-channel stream

| FS | `H_original` | `H_bitstring` | **assessed** `min(H_orig, 6×H_bits)` | per bit |
|---|---|---|---|---|
| 60 kHz | 2.2661 | 0.4529 | **2.2661** | 0.3777 |
| 70 kHz | 1.6934 | 0.2487 | **1.4925** ← `H_bitstring` binds | 0.2487 |
| 80 kHz | 1.7145 | 0.4132 | **1.7145** | 0.2857 |
| 90 kHz | 2.2181 | 0.4705 | **2.2181** | 0.3697 |
| **100 kHz** | 2.2988 | 0.4434 | **2.2988** | 0.3831 |

Read this per sample: 2.2988 bits of min-entropy **per 6-bit sample**, i.e. per 6 raw bits
collected, i.e. 0.3831 bits per raw bit.

At 100 kHz the binding estimator is **MultiMMC = 2.2988 / 6 bits** (second lowest is LRS at
2.4203; the rest give 3.0–5.5 bits). At 70 kHz the limit comes not from `H_original` but from
`6 × H_bitstring`, dropping the assessment to 1.4925.

**Important: the combined estimate is far higher than the sum of the individual channel
estimates** (2.30 vs 0.76 bits per sample, a factor of 3.0). For independent sources this is
impossible — the entropy rate of the joint stream must equal the sum of its components' rates,
and any correlation only lowers it. So one of the two numbers is inflated, and the tool's own
output shows which:

| assessment | alphabet | samples | chance floor `2·log_k(N)` | measured `t` |
|---|---|---|---|---|
| single channel | 2 symbols | 8 388 608 | ~46 | **148–214** |
| combined | 64 symbols | 1 398 101 | ~6.8 | **5** |

On single channels the tool finds recurring sequences of 150–214 samples, three to five times
longer than chance permits — a measurement of real, massive structure. On the combined stream
`t = 5` sits **below** the chance floor: the estimator found nothing, not because the structure
is absent but because a 64-symbol alphabet with 6× fewer samples gives it nowhere to look.
There are 64⁶ = 69 billion possible 6-symbol combinations against 1.4 million samples, so any
repeat longer than ~4 symbols is statistically impossible no matter how predictable the data
is. The same handicap applies to the predictors: MultiMMC and LZ78Y must learn a model over a
64-symbol space from a six times smaller sample.

**Failing to detect structure is not the same as its absence.** SP 800-90B assumes one noise
source per assessment precisely because interleaving disables many of the estimators. The
combined figure is therefore optimistic and is not used as the entropy budget below.

## 6. Cross-channel coupling (crosstalk)

Since the budget rests on *summing* the six channels, their independence has to be
demonstrated rather than assumed. This needs **simultaneous** samples, which only
`channelA-F.bin` has — all six latched by one pulse. The per-channel captures were taken at
different times and cannot be cross-correlated.

Measured with `channel_crosstalk.py` at 100 kHz, N = 1 398 101, noise floor 1σ = 0.00085.

Bias (fraction of ones): A=0.5137 B=0.5087 C=0.5109 D=0.5022 E=0.5102 F=0.5149

Pearson correlation at lag 0:

|  | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| **A** | . | **0.0145** | −0.0008 | 0.0009 | 0.0020 | **0.0227** |
| **B** | **0.0145** | . | −0.0010 | −0.0009 | −0.0002 | 0.0003 |
| **C** | −0.0008 | −0.0010 | . | **0.0134** | −0.0011 | −0.0002 |
| **D** | 0.0009 | −0.0009 | **0.0134** | . | **0.0162** | −0.0004 |
| **E** | 0.0020 | −0.0002 | −0.0011 | **0.0162** | . | **0.0125** |
| **F** | **0.0227** | 0.0003 | −0.0002 | −0.0004 | **0.0125** | . |

Five pairs are significant; **the other ten sit below 3σ**, indistinguishable from zero.
Real physical coupling propagates with a delay, so lag 0 understates it — the strongest
correlation over lags ±64 is consistently larger:

| pair | lag 0 | strongest | at lag | mutual information |
|---|---|---|---|---|
| A-F | +0.0227 (27σ) | −0.0240 | +2 | 0.000372 bit |
| D-E | +0.0162 (19σ) | −0.0200 | +2 | 0.000190 bit |
| A-B | +0.0145 (17σ) | +0.0292 | −1 | 0.000151 bit |
| C-D | +0.0134 (16σ) | +0.0225 | +2 | 0.000130 bit |
| E-F | +0.0125 (15σ) | +0.0224 | +2 | 0.000112 bit |

The pattern reproduces across frequencies. Consistently significant at 60, 80 and 100 kHz:
**A-B, C-D, E-F** — three disjoint pairs. Additional pairs come and go with frequency (D-E
and A-F at 80/100 kHz, B-C at 60 kHz), which is what one expects from weaker coupling over a
shared supply rail.

**Likely cause, worth confirming in the schematic.** `Rev-1/hardware/RO-Trng.kicad_sch` uses
**74LVC04** hex inverters in three packages (U1, U2, U3). Six rings × three gates = 18 gates
= 3 × 6, i.e. **two complete rings per package**, sharing a die, supply pins and ground
bounce. The A-B / C-D / E-F pairing maps onto exactly that. Treat this as a well-supported
hypothesis, not an established fact: the gate-to-ring assignment in the schematic was not
verified. If it holds, the implication for Rev-2 is obvious — put each ring in its own
package, or at least give each one its own decoupling.

**How much does the coupling actually cost?** This is where statistical significance and
practical relevance part company. Total mutual information over all 15 pairs is
**0.000960 bits per sample**, against an entropy budget of 0.7566 — that is **0.13%**. Even
taking the strongest lag for every pair the total stays below ~0.5%.

Three-channel subsets, entropy against worst in-subset coupling:

| subset | sum H | entropy rate | worst \|r\| in subset |
|---|---|---|---|
| **ABCDEF** | 0.7566 | **75.7 kbit/s** | 0.0227 (27σ) |
| BDF | 0.4051 | 40.5 kbit/s | **0.0009 (1σ)** |
| BCF | 0.3771 | 37.7 kbit/s | 0.0010 (1σ) |
| ADE | 0.3795 | 37.9 kbit/s | 0.0162 (19σ) |
| ACE | 0.3515 | 35.2 kbit/s | 0.0020 (2σ) |

**Conclusion: keep all six channels enabled.** Dropping to three costs 46–54% of the entropy
rate to recover 0.13%. If a rigorously defensible independence claim is ever needed — for a
certification narrative, say — then **B+D+F beats A+C+E on both axes at once**: worst |r| of
0.0009 (1σ, indistinguishable from zero) versus 0.0020 (2σ), *and* a higher entropy rate
(0.4051 vs 0.3515 bits per sample). Avoid A+D+E: it contains D-E, one of the most strongly
coupled pairs.

## 7. How much entropy an output byte carries

At 100 kHz with six channels, 600 000 bit/s of raw data are collected. The conservative
budget (sum of channels, independence assumed and now checked in section 6):
**75 659 bits of entropy per second**, i.e. 9.5 kB/s.

One output block is 256 bits (SHA-256). Entropy going into each block:

| compression | collects | samples | entropy per block (conservative) | per combined estimate | output | entropy bits / output bit |
|---|---|---|---|---|---|---|
| 4:1 | 128 B | 170.7 | 129.1 bit | 392.4 bit | 18 750 B/s | 0.50 |
| 7:1 | 224 B | 298.7 | 226.0 bit | 686.7 bit | 10 714 B/s | 0.88 |
| **8:1** | 256 B | 341.3 | **258.3 bit** | 784.8 bit | 9 375 B/s | **1.01** |
| **9:1 (default, maximum)** | 288 B | 384.0 | **290.5 bit** | 882.9 bit | 8 333 B/s | **1.13** |

So, on the conservative reading of the measurements:

- **4:1 compression does not produce full-entropy output** — a 32-byte block carries ~129
  bits of entropy, not 256, about 0.50 entropy bits per output bit. This is why the default
  was moved to 9:1
- reaching ≥ 1 entropy bit per output bit needs compression of **at least 7.9:1**, in practice
  **8:1**
- the 2× margin (the usual requirement for treating the output of a vetted conditioning
  function as full entropy) needs **15.9:1**, far beyond what the firmware allows
  (maximum 9:1)
- at 9:1 a block receives 290 bits, only 1.13× the output length

If the combined estimate (2.30 bits/sample) were taken as truth, 4:1 would already deliver
392 bits per block, 1.53× the output length. Section 5 explains why that reading is not used.

## 8. Choice of sampling frequency

Entropy **per sample** does not grow with frequency — sampling faster gives jitter less time
to accumulate — but entropy **per second** does:

| FS | bit/sample (sum) | **entropy [kbit/s]** |
|---|---|---|
| 60 kHz | 0.9068 | 54.4 |
| 70 kHz | 0.7970 | 55.8 |
| 80 kHz | 0.8502 | 68.0 |
| 90 kHz | 0.8047 | 72.4 |
| 100 kHz | 0.7566 | **75.7** |

Throughput **rises monotonically across the tested range** and peaks at 100 kHz
(75.7 kbit/s). The curve is clearly flattening though — +33% from 60 to 90 kHz, but only
+4.5% from 90 to 100 kHz — so the plateau starts right here. **100 kHz is the optimum on this
data** among the frequencies tested, and it happens to give a round clock divisor
(`Period = 479`, exactly 100 kHz). Higher frequencies were not measured; the flattening
suggests little to gain, and the SHA-256 compute ceiling sits at ~130–190 kHz depending on
compression (see `../firmware/analiza.md`).

## 9. Sample sizes — what is enough, and where more does not help

- **Single channels: 8 388 608 samples (1 MiB raw) are ample** — 8× the SP 800-90B minimum.
  No reason to increase.
- **Coupling analysis: 1.4 M simultaneous samples already suffice.** With σ = 0.00085, effects
  down to ~0.003 are detectable; couplings of order 0.02 were resolved at 27σ. More data adds
  nothing.
- **Combined 6-bit assessment: more samples will not fix it.** The limitation is alphabet
  width, not sample count. To lift the chance floor for tuple repeats to the binary case's ~46
  you would need 64²³ ≈ 10⁴¹ samples; even a floor of 20 needs 64¹⁰ ≈ 1.2 × 10¹⁸. Raising the
  capture to **~10 M symbols** (7.5 MB raw, ~100 s at 100 kHz) is still worth doing — the
  current 1 398 101 is only 1.4× the standard's minimum, and the predictors train better on
  more data — but the result should be read as a sanity check, not as the budget.
- **Missing regardless of size: restart tests.** SP 800-90B wants 1000 restarts × 1000 samples,
  which is a different capture procedure entirely, with a power cycle between samples.

## 10. Caveats — read before quoting these numbers

The points below do not overturn the qualitative conclusion (raw bits are heavily correlated,
conditioning is mandatory), but they **do affect the exact values** and must be resolved
before anyone treats this as a formal assessment.

1. ~~**Bit order mismatch.**~~ **Resolved.** The firmware packs LSB-first; the first test
   series unpacked MSB-first, inflating results by 8–20% (channels) and 20–33% (combined).
   `collect_all.sh` now passes `--bit-order little`, and the figures in this document come
   from the corrected `results_*kHz_lsb.txt` series. **Still to fix: `BIT_ORDER = "big"` in
   `check_streams_corelation.py`**, which remains inconsistent with the firmware.
2. ~~**Channel independence unverified.**~~ **Measured, see section 6.** Five channel pairs
   are statistically coupled, but the total cost is 0.13% of the budget, so summing the six
   per-channel estimates stands. What remains open is confirming the *cause* — whether the
   ring-to-package assignment really is U1={A,B}, U2={C,D}, U3={E,F}.
3. **Single-source model for the combined stream.** Running the non-IID track over a stream
   interleaving six sources does not match the SP 800-90B model, which is why section 5's
   figure is not used as the budget. The per-channel assessment plus section 6 is the
   defensible path.
4. ~~**No health tests.**~~ **Implemented.** RCT and APT per SP 800-90B 4.4 now run per
   channel over every completed buffer, in the main loop rather than the ISR, so the 100 kHz
   sampling path is untouched. Cutoffs are derived for the worst measured per-channel
   min-entropy H = 0.08 and alpha = 2^-30: **RCT = 376**, **APT W = 1024, C = 1007**. A buffer
   that fails is discarded before conditioning; the failure count is reported by `?`.
   Two deviations from the standard remain worth knowing: the reaction is "discard this
   buffer and carry on" rather than "declare the source failed and stop", and a run that is
   still open at a buffer boundary is detected at that boundary rather than on the exact
   sample. Neither weakens detection of a stalled ring, which is caught within 376 samples
   (3.8 ms).
6. **No assessment over the operating envelope.** All measurements were taken at room
   temperature on USB power. Ring oscillators are sensitive to supply voltage and temperature;
   a formal assessment needs the full operating range.
7. **Single specimen.** Unit-to-unit variation was not investigated.
8. **Low multiplicities are not viable at 100 kHz.** With health tests enabled the main loop
   cannot keep up at 1:1 or 2:1 compression — SHA-256's fixed per-digest cost dominates at
   those settings. 3:1 and above fit; the default 9:1 has a 1.6× margin. An overrun is not
   silent: it shows up as `DROP col` rising in the `?` report.

## 11. What quality this project represents

**In short:** a working, measured and documented hardware generator with a known, modest
entropy throughput — **~76 kbit/s** at 100 kHz — with SHA-256 conditioning, but **without
formal SP 800-90B compliance**, and with a default configuration of **9:1**, chosen so that
the conservative reading of the measurements still yields at least one entropy bit per output
bit.

What it can be used for as it stands:

- ✅ as a **seed source** for a deterministic generator (DRBG) on the host, provided the host
  knows how much entropy it is actually getting and draws proportionally more material
- ✅ for experiments, teaching, and comparing oscillator designs
- ✅ as a source of bit-for-bit randomness at the **default 9:1** setting, which carries 290
  assessed entropy bits per 256-bit output block (1.13×)
- ⚠️ in applications requiring certification — the health tests are in place but the whole
  assessment still lacks restart tests, an operating-envelope characterisation and more than
  one specimen (caveats 5, 6, 7)

Next steps that would most improve the credibility of this assessment, in order: confirm the
ring-to-package mapping and act on it in Rev-2 (section 6), fix `BIT_ORDER` in
`check_streams_corelation.py` (caveat 1), add SP 800-90B restart tests, and characterise the
source over temperature and supply voltage.

---

*Source data: `results_60kHz_lsb.txt` … `results_100kHz_lsb.txt` in this directory
(authoritative series); `results_*kHz.txt` is the defective MSB-first series.
Collection script: `host_tools/collect_all.sh`. Coupling analysis:
`host_tools/channel_crosstalk.py`. Tool: NIST SP 800-90B EntropyAssessment `ea_non_iid`.
Firmware timing budget analysis: `../firmware/analiza.md`.*
