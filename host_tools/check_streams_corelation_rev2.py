#!/usr/bin/env python3
"""Rev-2: split 8 interleaved bit streams apart and analyse how dependent they are.

For the Rev-1 board (6 rings, one latch) use check_streams_corelation_rev1.py.

This is the main analysis tool. The input file (`input`) holds a raw bit stream
in which 8 sources (A..H) are interleaved bit by bit, round-robin:

    bit0->A  bit1->B  ...  bit7->H
    bit8->A  bit9->B  ...

With all eight rings enabled a sample is exactly one byte, so unlike Rev-1 no
sample straddles a byte boundary.

Bit order within each byte is LSB-first, matching how the firmware packs bits
(entropy_collector.c fills the accumulator from bit 0 up) and matching
channel_crosstalk_rev2.py.

Rev-2 hardware map (RO_hardware.kicad_sch; the latch outputs cross over, so the
letter order is not the schematic sheet order):

    letter  ring  pin   latch          topology
    A       1     PA8   B (PA1, U22)   T     - SN74LVC3G04, 3 gates in one package
    B       2     PA0   B              T
    C       3     PA2   B              T
    D       4     PA3   B              T
    E       5     PA4   A (PB0, U3)    plain - 3x SN74LVC1G04, one gate per package
    F       6     PA5   A              plain
    G       7     PA6   A              plain
    H       8     PA7   A              plain

Because the two groups differ in both topology and latch, the summary breaks
the dependence down as within-group versus across-group - the direct test of the
Rev-1 finding that rings sharing an inverter package were the coupled ones.

Usage:
    python3 check_streams_corelation_rev2.py [input.bin]

The program:
  1. de-interleaves the sources into 6 separate bit-packed files,
  2. computes the 6x6 Pearson correlation matrix between the streams,
  3. computes the mutual-information matrix (in bits), which also detects
     non-linear dependencies that correlation misses.

Optionally it runs a per-stream analysis (autocorrelation, a Markov transition
matrix, and a Welch power spectrum) and saves CSV/NPY data plus PNG plots.

Everything is processed in a streaming fashion, so memory use stays constant
regardless of the input file size. The point of the tool is to verify that the
6 hardware RNG sources are statistically independent of one another.
"""

import math
import os
import sys
import time

import numpy as np

# ── Configuration ───────────────────────────────────────────────────────────
# Path to the input file with the raw bit stream.
input = "channelA-H.bin"

NUM_STREAMS = 8
LABELS = ["substream_A", "substream_B", "substream_C", "substream_D",
          "substream_E", "substream_F", "substream_G", "substream_H"]
# Matrix headers only: eight 'substream_X' labels do not fit in a row and run
# into each other, so the matrices are printed with the bare channel letters.
SHORT_LABELS = [lbl.rsplit("_", 1)[-1] for lbl in LABELS]
BIT_ORDER = "little"                  # firmware packs LSB-first; 'big' = MSB-first

# Ring groups by latch / topology, see the map in the module docstring. Keys are
# labels, values are the stream indices that belong to the group.
GROUPS = {
    "T     (rings 1-4, latch B, 3G04)": (0, 1, 2, 3),
    "plain (rings 5-8, latch A, 1G04)": (4, 5, 6, 7),
}
CHUNK_SIZE = 4 * 1024 * 1024          # 4 MiB of raw data per chunk
WRITE_STREAM_FILES = True             # whether to write the de-interleaved streams

# ── Per-stream analysis (autocorrelation / Markov / FFT) ─────────────────────
RUN_PER_STREAM_ANALYSIS = True        # requires WRITE_STREAM_FILES = True
MAX_LAG = 64                          # max autocorrelation lag
MARKOV_ORDER = 1                      # Markov chain order (1 => 2x2 matrix)
NFFT = 65536                          # FFT segment length (Welch, power of 2)
WELCH_OVERLAP = 0.5                   # Welch segment overlap (0..0.9)
ANALYSIS_DIR = "stream_analysis"      # output directory for PNG/CSV/NPY
# ──────────────────────────────────────────────────────────────────────────


def human(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def stream_path(path, label):
    root, ext = os.path.splitext(path)
    return f"{root}_{label}{ext}"


def draw_progress(done, total, start):
    elapsed = time.monotonic() - start
    speed = done / elapsed if elapsed > 0 else 0
    bar_len = 30
    frac = (done / total) if total else 0.0
    filled = int(bar_len * frac)
    bar = "#" * filled + "-" * (bar_len - filled)
    eta = (total - done) / speed if speed > 0 else 0
    sys.stderr.write(
        f"\r[{bar}] {frac*100:5.1f}%  {human(done)}/{human(total)}  "
        f"{human(speed)}/s  ETA {eta:5.0f}s")
    sys.stderr.flush()


def mutual_information_bits(n, s_i, s_j, n11):
    """Mutual information (in bits) for a pair of binary streams.

    n    - number of sample pairs
    s_i  - number of ones in stream i
    s_j  - number of ones in stream j
    n11  - number of positions where both streams are 1
    """
    if n == 0:
        return 0.0
    n10 = s_i - n11
    n01 = s_j - n11
    n00 = n - n11 - n10 - n01
    # marginal probabilities
    pi1, pi0 = s_i / n, 1 - s_i / n
    pj1, pj0 = s_j / n, 1 - s_j / n
    mi = 0.0
    for cnt, pi, pj in (
        (n00, pi0, pj0),
        (n01, pi0, pj1),
        (n10, pi1, pj0),
        (n11, pi1, pj1),
    ):
        if cnt > 0 and pi > 0 and pj > 0:
            pij = cnt / n
            mi += pij * math.log2(pij / (pi * pj))
    return max(mi, 0.0)  # clamp tiny negative numerical errors to 0


def iter_stream_bits(path, n_samples, chunk_bytes=CHUNK_SIZE):
    """Stream the bits out of a de-interleaved file, trimming the padding.

    The de-interleaved files are bit-packed, so the last byte may hold up to
    7 bits of zero padding — we trim to exactly n_samples bits.
    """
    remaining = n_samples
    with open(path, "rb") as f:
        while remaining > 0:
            raw = f.read(chunk_bytes)
            if not raw:
                break
            bits = np.unpackbits(
                np.frombuffer(raw, dtype=np.uint8), bitorder=BIT_ORDER)
            if bits.size > remaining:
                bits = bits[:remaining]
            remaining -= bits.size
            yield bits


def analyze_stream_file(path, n_samples, n_ones, label):
    """Compute autocorrelation, a Markov matrix and a Welch spectrum for one stream.

    Everything is streamed (constant memory). Returns a dict of results.
    """
    # Autocorrelation accumulators: sp[k] = sum of x[t]*x[t+k].
    # float64: np.dot uses BLAS (much faster than int64), and integer sums
    # up to 2^53 are represented exactly.
    sp = np.zeros(MAX_LAG + 1, dtype=np.float64)
    ac_prev = np.empty(0, dtype=np.uint8)          # tail kept to bridge lags across chunks

    # Markov chain of order MARKOV_ORDER -> 2**order states, next bit is 0/1 (2 cols).
    n_states = 1 << MARKOV_ORDER
    markov = np.zeros((n_states, 2), dtype=np.int64)
    mk_carry = np.empty(0, dtype=np.uint8)
    weights = (1 << np.arange(MARKOV_ORDER - 1, -1, -1)).astype(np.int64)

    # Welch spectrum
    window = np.hanning(NFFT)
    win_power = np.sum(window ** 2)
    step = max(1, int(NFFT * (1.0 - WELCH_OVERLAP)))
    psd_sum = np.zeros(NFFT // 2 + 1)
    n_seg = 0
    wbuf = np.empty(0, dtype=np.float64)

    total_bits = n_samples
    done = 0
    start = time.monotonic()
    last_draw = 0.0

    for bits in iter_stream_bits(path, n_samples):
        m = bits.size
        if m == 0:
            break

        # ── autocorrelation ──
        e = (np.concatenate((ac_prev, bits)) if ac_prev.size else bits).astype(np.float64)
        base = ac_prev.size
        right = e[base:base + m]
        for k in range(1, MAX_LAG + 1):
            ls = base - k
            if ls >= 0:
                sp[k] += np.dot(e[ls:ls + m], right)
            else:
                skip = k - base
                if skip < m:
                    sp[k] += np.dot(e[0:m - skip], right[skip:])
        ac_prev = (e[-MAX_LAG:] if e.size >= MAX_LAG else e).astype(np.uint8)

        # ── Markov ──
        ext = np.concatenate((mk_carry, bits)) if mk_carry.size else bits
        if ext.size > MARKOV_ORDER:
            win = np.lib.stride_tricks.sliding_window_view(ext, MARKOV_ORDER)
            states = win[:ext.size - MARKOV_ORDER].astype(np.int64)
            state_int = states @ weights
            nxt = ext[MARKOV_ORDER:].astype(np.int64)
            idx = state_int * 2 + nxt
            markov += np.bincount(
                idx, minlength=n_states * 2).reshape(n_states, 2)
        mk_carry = ext[-MARKOV_ORDER:] if ext.size >= MARKOV_ORDER else ext

        # ── Welch (overlapping segments) ──
        wbuf = np.concatenate((wbuf, bits.astype(np.float64)))
        pos = 0
        while pos + NFFT <= wbuf.size:
            seg = wbuf[pos:pos + NFFT]
            seg = seg - seg.mean()                 # detrend (remove DC component)
            spec = np.fft.rfft(seg * window)
            psd_sum += spec.real ** 2 + spec.imag ** 2
            n_seg += 1
            pos += step
        if pos:
            wbuf = wbuf[pos:].copy()

        done += m
        now = time.monotonic()
        if now - last_draw >= 0.1 or done >= total_bits:
            draw_progress(done, total_bits, start)
            last_draw = now
    sys.stderr.write("\n")

    # ── results: autocorrelation (normalised) ──
    p = n_ones / n_samples if n_samples else 0.0
    var = p * (1.0 - p)
    lags = np.arange(1, MAX_LAG + 1)
    autocorr = np.zeros(MAX_LAG)
    if var > 0:
        for k in lags:
            pairs = n_samples - k
            if pairs > 0:
                mean_prod = sp[k] / pairs
                autocorr[k - 1] = (mean_prod - p * p) / var

    # ── Markov matrix (conditional probabilities) ──
    row_tot = markov.sum(axis=1, keepdims=True)
    markov_prob = np.divide(markov, row_tot, out=np.zeros_like(markov, dtype=float),
                            where=row_tot > 0)

    # ── Welch spectrum (one-sided PSD) ──
    freqs = np.fft.rfftfreq(NFFT, d=1.0)           # cycles / sample
    if n_seg > 0 and win_power > 0:
        psd = psd_sum / (n_seg * win_power)
        psd[1:-1] *= 2.0                            # one-sided correction
    else:
        psd = psd_sum

    return {
        "label": label,
        "p_one": p,
        "lags": lags,
        "autocorr": autocorr,
        "markov_counts": markov,
        "markov_prob": markov_prob,
        "freqs": freqs,
        "psd": psd,
        "n_seg": n_seg,
    }


def save_and_plot_stream(res, out_dir):
    """Save CSV/NPY data and a PNG plot (autocorrelation + spectrum) for a stream."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lbl = res["label"]
    base = os.path.join(out_dir, lbl)

    # numeric data
    np.save(f"{base}_autocorr.npy", res["autocorr"])
    np.savetxt(f"{base}_autocorr.csv",
               np.column_stack([res["lags"], res["autocorr"]]),
               delimiter=",", header="lag,autocorr", comments="", fmt=["%d", "%.8e"])
    np.save(f"{base}_psd.npy", np.column_stack([res["freqs"], res["psd"]]))
    np.savetxt(f"{base}_psd.csv",
               np.column_stack([res["freqs"], res["psd"]]),
               delimiter=",", header="freq_cycles_per_sample,psd", comments="",
               fmt=["%.8e", "%.8e"])
    np.savetxt(f"{base}_markov.csv", res["markov_prob"],
               delimiter=",", fmt="%.8f")

    # plot
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
    ax1.stem(res["lags"], res["autocorr"])
    ax1.axhline(0, color="k", lw=0.6)
    ax1.set_title(f"{lbl} — autocorrelation")
    ax1.set_xlabel("lag [samples]")
    ax1.set_ylabel("coefficient")

    f = res["freqs"][1:]      # skip DC for readability
    psd = res["psd"][1:]
    ax2.semilogy(f, np.maximum(psd, 1e-20))
    ax2.set_title(f"{lbl} — power spectrum (Welch, {res['n_seg']} segments)")
    ax2.set_xlabel("frequency [cycles/sample]")
    ax2.set_ylabel("PSD")

    fig.tight_layout()
    fig.savefig(f"{base}.png", dpi=110)
    plt.close(fig)
    return f"{base}.png"


def run_per_stream_analysis(out_paths, n_samples, ones):
    print("\n" + "=" * 60)
    print("PER-STREAM ANALYSIS (autocorrelation / Markov / FFT-Welch)")
    print("=" * 60)
    os.makedirs(ANALYSIS_DIR, exist_ok=True)

    for i, (lbl, path) in enumerate(zip(LABELS, out_paths)):
        if not os.path.isfile(path):
            print(f"\n[{lbl}] skipped — missing file {path}")
            continue
        print(f"\n[{lbl}] analysing {path} ...")
        res = analyze_stream_file(path, int(n_samples), int(ones[i]), lbl)
        png = save_and_plot_stream(res, ANALYSIS_DIR)

        # autocorrelation: strongest lag
        ac = res["autocorr"]
        kmax = int(np.argmax(np.abs(ac))) + 1
        print(f"    autocorrelation: lag1={ac[0]:+.5f}  "
              f"max|r|={abs(ac[kmax-1]):.5f} @ lag {kmax}")

        # Order-1 Markov: show conditional P(1|state)
        mp = res["markov_prob"]
        if MARKOV_ORDER == 1:
            print(f"    Markov 2x2  P(1|0)={mp[0,1]:.5f}  P(1|1)={mp[1,1]:.5f}  "
                  f"(|difference|={abs(mp[1,1]-mp[0,1]):.5f} => order-1 memory)")
        else:
            print(f"    Markov {mp.shape[0]}x2 saved to "
                  f"{os.path.join(ANALYSIS_DIR, lbl)}_markov.csv")

        # FFT: dominant peak (excluding DC)
        psd = res["psd"].copy()
        freqs = res["freqs"]
        if psd.size > 2:
            peak = int(np.argmax(psd[1:])) + 1
            rel = psd[peak] / np.median(psd[1:])
            print(f"    FFT: peak @ f={freqs[peak]:.6f} cycles/sample "
                  f"({rel:.2f}x spectrum median)")
        print(f"    plot: {png}")

    print(f"\nOutput files (CSV/NPY/PNG) in directory: {ANALYSIS_DIR}/")


def print_group_breakdown(pear, mi, n_samples):
    """Within-group versus across-group dependence.

    In Rev-1 the coupled pairs were exactly those sharing an inverter package.
    Rev-2 gives every ring its own package, but the three gates of a T ring
    share a die and each group of four shares a latch, so a residual pattern
    would show up here rather than in the full matrix.
    """
    noise = 1.0 / math.sqrt(n_samples) if n_samples else 0.0
    names = list(GROUPS)
    buckets = {name: [] for name in names}
    buckets["across groups"] = []
    for i in range(NUM_STREAMS):
        for j in range(i + 1, NUM_STREAMS):
            gi = next((nm for nm, idx in GROUPS.items() if i in idx), None)
            gj = next((nm for nm, idx in GROUPS.items() if j in idx), None)
            key = gi if gi is not None and gi == gj else "across groups"
            buckets[key].append((abs(pear[i, j]), mi[i, j]))

    print("\nDependence by ring group:")
    print(f"    {'group':<36}{'pairs':>6}{'max |r|':>11}{'mean |r|':>11}{'sum MI [bit]':>15}")
    for key in names + ["across groups"]:
        rows = buckets[key]
        if not rows:
            continue
        rs = [r for r, _ in rows]
        print(f"    {key:<36}{len(rows):>6}{max(rs):>11.5f}"
              f"{sum(rs) / len(rs):>11.5f}{sum(m for _, m in rows):>15.6f}")
    print(f"    (noise floor 1 sigma = {noise:.5f}; |r| below {4 * noise:.5f} "
          f"is not resolvable at 4 sigma)")


def main():
    global input
    if len(sys.argv) > 1:
        if sys.argv[1] in ("-h", "--help"):
            print(__doc__)
            return
        input = sys.argv[1]
    if not os.path.isfile(input):
        sys.exit(f"Error: input file does not exist: {input!r}")

    total = os.path.getsize(input)
    if total == 0:
        sys.exit("Error: input file is empty.")

    out_paths = [stream_path(input, lbl) for lbl in LABELS]
    if WRITE_STREAM_FILES:
        for p in out_paths:
            if os.path.abspath(p) == os.path.abspath(input):
                sys.exit(f"Error: output path collides with the input path: {p}")

    print(f"Input         : {input} ({human(total)})")
    print(f"Streams       : {NUM_STREAMS} (round-robin bit interleave, "
          f"bit-order={BIT_ORDER})")
    if WRITE_STREAM_FILES:
        print("Output files:")
        for lbl, p in zip(LABELS, out_paths):
            print(f"    {lbl} -> {p}")
    print("De-interleaving streams and accumulating statistics...")

    # Statistics accumulators (constant memory).
    n_samples = 0                       # number of full samples per stream
    ones = np.zeros(NUM_STREAMS, dtype=np.int64)          # per-stream sum of ones
    joint = np.zeros((NUM_STREAMS, NUM_STREAMS), dtype=np.int64)  # per-pair n11

    carry_bits = np.empty(0, dtype=np.uint8)              # leftover < 8 bits between chunks
    # bit buffers used to pack each stream into whole bytes
    pack_buf = [np.empty(0, dtype=np.uint8) for _ in range(NUM_STREAMS)]

    fouts = [open(p, "wb") for p in out_paths] if WRITE_STREAM_FILES else None

    done = 0
    start = time.monotonic()
    last_draw = 0.0
    try:
        with open(input, "rb") as fin:
            while True:
                raw = fin.read(CHUNK_SIZE)
                if not raw:
                    break
                bits = np.unpackbits(
                    np.frombuffer(raw, dtype=np.uint8), bitorder=BIT_ORDER)
                if carry_bits.size:
                    bits = np.concatenate((carry_bits, bits))

                n_groups = bits.size // NUM_STREAMS
                usable = n_groups * NUM_STREAMS
                carry_bits = bits[usable:].copy()

                if n_groups:
                    # row = one sample, column = one stream
                    mat = bits[:usable].reshape(n_groups, NUM_STREAMS)
                    n_samples += n_groups
                    col_sums = mat.sum(axis=0, dtype=np.int64)
                    ones += col_sums
                    # n11 for every pair: product of columns (bits are 0/1)
                    joint += mat.T.astype(np.int64) @ mat.astype(np.int64)

                    if WRITE_STREAM_FILES:
                        for i in range(NUM_STREAMS):
                            col = mat[:, i]
                            buf = (np.concatenate((pack_buf[i], col))
                                   if pack_buf[i].size else col)
                            nbytes = buf.size // 8
                            if nbytes:
                                fouts[i].write(
                                    np.packbits(buf[:nbytes * 8],
                                                bitorder=BIT_ORDER).tobytes())
                            pack_buf[i] = buf[nbytes * 8:].copy()

                done += len(raw)
                now = time.monotonic()
                if now - last_draw >= 0.1 or done == total:
                    draw_progress(done, total, start)
                    last_draw = now

        # flush the tails (zero-pad up to a full byte)
        if WRITE_STREAM_FILES:
            for i in range(NUM_STREAMS):
                if pack_buf[i].size:
                    fouts[i].write(
                        np.packbits(pack_buf[i], bitorder=BIT_ORDER).tobytes())
    finally:
        if fouts:
            for f in fouts:
                f.close()

    sys.stderr.write("\n")

    if n_samples == 0:
        sys.exit("Error: not enough data for even a single full 8-bit sample.")

    # ── Per-stream statistics ─────────────────────────────────────────────────
    print(f"\nSamples per stream: {n_samples:,}")
    print("Share of ones (bias) per stream:")
    for lbl, s in zip(LABELS, ones):
        print(f"    {lbl}: {s / n_samples:.6f}")

    # ── Pearson correlation matrix ────────────────────────────────────────────
    n = int(n_samples)
    pear = np.eye(NUM_STREAMS)
    for i in range(NUM_STREAMS):
        for j in range(i + 1, NUM_STREAMS):
            # native Python ints -> no overflow for large files
            sx, sy, sxy = int(ones[i]), int(ones[j]), int(joint[i, j])
            num = n * sxy - sx * sy
            den = math.sqrt((n * sx - sx * sx) * (n * sy - sy * sy))
            r = (num / den) if den > 0 else 0.0
            pear[i, j] = pear[j, i] = r

    # ── Mutual-information matrix ──────────────────────────────────────────────
    mi = np.zeros((NUM_STREAMS, NUM_STREAMS))
    for i in range(NUM_STREAMS):
        for j in range(NUM_STREAMS):
            mi[i, j] = mutual_information_bits(n, ones[i], ones[j], joint[i, j])

    def print_matrix(title, M, fmt):
        print(f"\n{title}")
        print("        " + "".join(f"{lbl:>10}" for lbl in SHORT_LABELS))
        for i, lbl in enumerate(SHORT_LABELS):
            row = "".join(fmt(M[i, j]) for j in range(NUM_STREAMS))
            print(f"    {lbl:>3} {row}")

    print_matrix("Pearson correlation (off-diagonal ~0 => no linear dependence):",
                 pear, lambda v: f"{v:>10.5f}")
    print_matrix("Mutual information [bits] (0 => independent):",
                 mi, lambda v: f"{v:>10.6f}")

    # ── Short summary ─────────────────────────────────────────────────────────
    print_group_breakdown(pear, mi, n)

    iu = np.triu_indices(NUM_STREAMS, k=1)
    max_abs_r = np.max(np.abs(pear[iu])) if iu[0].size else 0.0
    max_mi = np.max(mi[iu]) if iu[0].size else 0.0
    print("\nSummary:")
    print(f"    max off-diagonal |Pearson| : {max_abs_r:.5f}")
    print(f"    max mutual information      : {max_mi:.6f} bit")
    if max_abs_r < 0.01 and max_mi < 1e-4:
        print("    => no significant dependence — the streams look independent.")
    else:
        print("    => dependence detected — the streams are NOT fully independent.")

    # ── Per-stream analysis ───────────────────────────────────────────────────
    if RUN_PER_STREAM_ANALYSIS:
        if not WRITE_STREAM_FILES:
            print("\nSkipped per-stream analysis "
                  "(requires WRITE_STREAM_FILES = True).")
        else:
            run_per_stream_analysis(out_paths, n, ones)


if __name__ == "__main__":
    main()
