#!/usr/bin/env python3
"""Measure cross-channel coupling between the six ring oscillators.

Input is a channelA-F.bin capture: the only file that holds *simultaneous*
samples of all six channels, taken with one latch pulse, which is what coupling
measurement needs. The per-channel captures (channelA.bin ...) were taken at
different times and cannot be cross-correlated.

Reports, for every one of the 15 channel pairs:
  * Pearson correlation at lag 0, in units of the 1/sqrt(N) noise floor
  * the strongest correlation over a range of non-zero lags - a real physical
    coupling shows up with a propagation delay, so lag 0 alone understates it
  * mutual information, which converts "statistically significant" into
    "how many bits of the entropy budget does this actually cost"

Bit order matches the firmware: LSB-first, channel A on bit 0 of each sample.

Usage:
    python3 channel_crosstalk.py 100kHz/channelA-F.bin
    python3 channel_crosstalk.py */channelA-F.bin --max-lag 64
"""

import argparse
import pathlib
import sys

import numpy as np

CHANNELS = "ABCDEF"
NUM_CHANNELS = 6

# Per-channel min-entropy from ea_non_iid, bits per bit, at 100 kHz. Used only to
# express the coupling cost as a fraction of the entropy budget.
DEFAULT_CHANNEL_ENTROPY = {
    "A": 0.116868, "B": 0.119555, "C": 0.083195,
    "D": 0.111178, "E": 0.151450, "F": 0.174346,
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Cross-channel coupling report for a channelA-F.bin capture.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("files", nargs="+", help="channelA-F.bin capture(s)")
    parser.add_argument(
        "--max-lag", type=int, default=64,
        help="largest non-zero lag to scan for the strongest coupling",
    )
    parser.add_argument(
        "--sigma", type=float, default=4.0,
        help="significance threshold in units of the 1/sqrt(N) noise floor",
    )
    return parser.parse_args(argv)


def load_channels(path):
    """Return an (N, 6) float32 array of simultaneous channel samples."""
    raw = np.frombuffer(pathlib.Path(path).read_bytes(), dtype=np.uint8)
    bits = np.unpackbits(raw, bitorder="little")      # firmware packs LSB-first
    n = len(bits) // NUM_CHANNELS
    return bits[: n * NUM_CHANNELS].reshape(n, NUM_CHANNELS).astype(np.float32)


def mutual_information(a, b):
    """Mutual information of two binary streams, in bits."""
    n = len(a)
    joint = np.bincount((a * 2 + b).astype(np.int64), minlength=4) / n
    p_a = np.array([1.0 - a.mean(), a.mean()])
    p_b = np.array([1.0 - b.mean(), b.mean()])
    return sum(
        joint[k] * np.log2(joint[k] / (p_a[k >> 1] * p_b[k & 1]))
        for k in range(4) if joint[k] > 0
    )


def strongest_lagged(norm_a, norm_b, max_lag):
    """Largest |correlation| over non-zero lags in both directions."""
    best_r, best_lag = 0.0, 0
    for lag in range(1, max_lag + 1):
        for x, y, signed in ((norm_a[lag:], norm_b[:-lag], lag),
                             (norm_a[:-lag], norm_b[lag:], -lag)):
            r = float((x * y).mean())
            if abs(r) > abs(best_r):
                best_r, best_lag = r, signed
    return best_r, best_lag


def report(path, max_lag, sigma_threshold):
    samples = load_channels(path)
    n = len(samples)
    noise = 1.0 / np.sqrt(n)
    normed = (samples - samples.mean(0)) / samples.std(0)

    print(f"=== {path} ===")
    print(f"{n} simultaneous samples, noise floor 1 sigma = {noise:.5f}")
    print("bias (fraction of ones): " +
          "  ".join(f"{CHANNELS[j]}={samples[:, j].mean():.4f}" for j in range(NUM_CHANNELS)))

    print("\nPearson correlation at lag 0:")
    print("      " + "".join(f"{c:>9}" for c in CHANNELS))
    corr = (normed.T @ normed) / n
    for i in range(NUM_CHANNELS):
        cells = "".join(
            f"{corr[i, j]:>9.4f}" if i != j else f"{'.':>9}"
            for j in range(NUM_CHANNELS)
        )
        print(f"   {CHANNELS[i]}  " + cells)

    print(f"\nPairs above {sigma_threshold:g} sigma, with their strongest non-zero lag:")
    total_mi = 0.0
    rows = []
    for i in range(NUM_CHANNELS):
        for j in range(i + 1, NUM_CHANNELS):
            r0 = corr[i, j]
            mi = mutual_information(samples[:, i], samples[:, j])
            total_mi += mi
            if abs(r0) > sigma_threshold * noise:
                r_lag, lag = strongest_lagged(normed[:, i], normed[:, j], max_lag)
                rows.append((abs(r0), CHANNELS[i], CHANNELS[j], r0, r_lag, lag, mi))
    for _, a, b, r0, r_lag, lag, mi in sorted(rows, reverse=True):
        print(f"   {a}-{b}:  lag 0 r={r0:+.4f} ({abs(r0)/noise:>3.0f} sigma)"
              f"   best r={r_lag:+.4f} at lag {lag:+d}"
              f"   MI={mi:.6f} bit")
    if not rows:
        print("   none")

    budget = sum(DEFAULT_CHANNEL_ENTROPY.values())
    print(f"\nTotal mutual information over all 15 pairs: {total_mi:.6f} bit/sample")
    print(f"Entropy budget (sum of per-channel min-entropy): {budget:.6f} bit/sample")
    print(f"Coupling costs {100 * total_mi / budget:.2f}% of the budget")

    print("\nThree-channel subsets, entropy against worst in-subset coupling:")
    for subset in ("ABCDEF", "ACE", "BDF", "BCF", "ADE"):
        idx = [CHANNELS.index(c) for c in subset]
        worst = max(abs(float((normed[:, i] * normed[:, j]).mean()))
                    for i in idx for j in idx if i < j)
        h = sum(DEFAULT_CHANNEL_ENTROPY[c] for c in subset)
        print(f"   {subset:<7} sum H={h:.4f} bit/sample"
              f"   worst |r|={worst:.4f} ({worst/noise:>3.0f} sigma)")
    print()


def main(argv=None):
    args = parse_args(argv)
    for path in args.files:
        report(path, args.max_lag, args.sigma)
    return 0


if __name__ == "__main__":
    sys.exit(main())
