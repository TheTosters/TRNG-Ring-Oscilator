#!/usr/bin/env python3
"""Rev-2: measure cross-channel coupling between the eight ring oscillators.

For the Rev-1 board (6 rings, one latch) use channel_crosstalk_rev1.py.

Input is a channelA-H.bin capture: the only file that holds *simultaneous*
samples of all eight channels, taken with one latch pulse, which is what
coupling measurement needs. The per-channel captures (channelA.bin ...) were
taken at different times and cannot be cross-correlated.

Reports, for every one of the 28 channel pairs:
  * Pearson correlation at lag 0, in units of the 1/sqrt(N) noise floor
  * the strongest correlation over a range of non-zero lags - a real physical
    coupling shows up with a propagation delay, so lag 0 alone understates it
  * mutual information, which converts "statistically significant" into
    "how many bits of the entropy budget does this actually cost"

Rev-2 splits the rings into two groups that differ in both topology and latch,
so the report also breaks the coupling down as within-T / within-plain /
across-groups. That is the direct test of the Rev-1 finding that rings sharing
a package were the coupled ones (summary.md section 6):

  letter  ring  pin   latch          topology
  a       1     PA8   B (PA1, U22)   T     - SN74LVC3G04, 3 gates in one package
  b       2     PA0   B              T
  c       3     PA2   B              T
  d       4     PA3   B              T
  e       5     PA4   A (PB0, U3)    plain - 3x SN74LVC1G04, one gate per package
  f       6     PA5   A              plain
  g       7     PA6   A              plain
  h       8     PA7   A              plain

Bit order matches the firmware: LSB-first, channel A (ring 1) on bit 0 of each
sample. With all eight rings enabled a sample is exactly one byte.

Unlike the Rev-1 tool this one has no built-in per-channel min-entropy table.
Those figures are a property of the measured board, and hard-coding Rev-1's
numbers here would silently express the coupling cost as a fraction of another
board's entropy budget. Supply them with --entropy-file (the results.txt that
collect_all_rev2.sh writes) or --entropy; without them the budget section is
skipped and the coupling is reported in absolute bits only.

Usage:
    python3 channel_crosstalk_rev2.py 100kHz/channelA-H.bin
    python3 channel_crosstalk_rev2.py 100kHz/channelA-H.bin \\
        --entropy-file 100kHz/results.txt
    python3 channel_crosstalk_rev2.py */channelA-H.bin --max-lag 64
    python3 channel_crosstalk_rev2.py cap.bin --entropy A=0.21,B=0.19,C=0.24
"""

import argparse
import itertools
import pathlib
import re
import sys

import numpy as np

CHANNELS = "ABCDEFGH"
NUM_CHANNELS = 8

# Ring groups, see the map above. Used for the within/across coupling breakdown.
GROUPS = {
    "T     (rings 1-4, latch B, 3G04)": "ABCD",
    "plain (rings 5-8, latch A, 1G04)": "EFGH",
}


def parse_entropy_arg(text):
    """Parse 'A=0.21,B=0.19' into {'A': 0.21, 'B': 0.19}."""
    table = {}
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        key, _, value = item.partition("=")
        key = key.strip().upper()
        if key not in CHANNELS:
            raise argparse.ArgumentTypeError(f"unknown channel {key!r}")
        try:
            table[key] = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if not table:
        raise argparse.ArgumentTypeError("no channel=value pairs found")
    return table


def parse_entropy_file(path):
    """Pull per-channel H_original out of a collect_all_rev2.sh results.txt.

    The file is a concatenation of ea_non_iid runs, each preceded by a
    '########## channel X (...)' banner and closed by 'H_original: <value>'.
    Only the single-channel sections are picked up; the interleaved group
    sections carry a wider alphabet and are not per-channel entropy.
    """
    table = {}
    current = None
    banner = re.compile(r"^#+\s*channel\s+([A-H])\s*\(")
    value = re.compile(r"^H_original:\s*([0-9.eE+-]+)")
    for line in pathlib.Path(path).read_text().splitlines():
        m = banner.match(line)
        if m:
            current = m.group(1)
            continue
        m = value.match(line)
        if m and current is not None:
            table[current] = float(m.group(1))
            current = None
    return table


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Cross-channel coupling report for a Rev-2 channelA-H.bin capture.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("files", nargs="+", help="channelA-H.bin capture(s)")
    parser.add_argument(
        "--max-lag", type=int, default=64,
        help="largest non-zero lag to scan for the strongest coupling",
    )
    parser.add_argument(
        "--sigma", type=float, default=4.0,
        help="significance threshold in units of the 1/sqrt(N) noise floor",
    )
    parser.add_argument(
        "--entropy-file", metavar="RESULTS.TXT",
        help="results.txt from collect_all_rev2.sh; per-channel H_original is "
             "read from it to express the coupling cost as a share of the budget",
    )
    parser.add_argument(
        "--entropy", type=parse_entropy_arg, metavar="A=0.21,B=0.19,...",
        help="per-channel min-entropy given directly; overrides --entropy-file",
    )
    return parser.parse_args(argv)


def load_channels(path):
    """Return an (N, 8) float32 array of simultaneous channel samples."""
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


def group_of(letter):
    for name, members in GROUPS.items():
        if letter in members:
            return name
    return "?"


def print_group_breakdown(corr, mi_pairs, noise):
    """Within-group vs across-group coupling.

    In Rev-1 the coupled pairs were exactly the ones sharing an inverter
    package. Rev-2 gives every ring its own package, but the T rings still
    share a die between their three gates and each group shares a latch, so
    this is where a residual pattern would show up.
    """
    names = list(GROUPS)
    buckets = {name: [] for name in names}
    buckets["across groups"] = []
    for a, b in itertools.combinations(CHANNELS, 2):
        i, j = CHANNELS.index(a), CHANNELS.index(b)
        ga, gb = group_of(a), group_of(b)
        key = ga if ga == gb else "across groups"
        buckets[key].append((abs(corr[i, j]), mi_pairs[(a, b)]))

    print("\nCoupling by ring group (Rev-1 found packages, not distance, to be "
          "what mattered):")
    print(f"   {'group':<36}{'pairs':>6}{'max |r|':>11}{'mean |r|':>11}{'sum MI [bit]':>15}")
    for key in names + ["across groups"]:
        rows = buckets[key]
        if not rows:
            continue
        rs = [r for r, _ in rows]
        mis = sum(m for _, m in rows)
        print(f"   {key:<36}{len(rows):>6}{max(rs):>11.4f}{np.mean(rs):>11.4f}"
              f"{mis:>15.6f}")
    print(f"   (noise floor 1 sigma = {noise:.5f}, so |r| below "
          f"{4 * noise:.4f} is not resolvable at 4 sigma)")


def print_budget(entropy, total_mi, corr, noise):
    """Express the coupling cost against a measured per-channel entropy budget."""
    missing = [c for c in CHANNELS if c not in entropy]
    if missing:
        print(f"\nEntropy budget: incomplete, missing channels "
              f"{''.join(missing)} - budget section skipped.")
        return

    budget = sum(entropy[c] for c in CHANNELS)
    print("\nPer-channel min-entropy used for the budget "
          "(bit per bit, from the supplied assessment):")
    print("   " + "  ".join(f"{c}={entropy[c]:.4f}" for c in CHANNELS))
    print(f"Entropy budget (sum over channels): {budget:.6f} bit/sample")
    if budget > 0:
        print(f"Coupling costs {100 * total_mi / budget:.2f}% of the budget")

    # Group subsets first - they are the physically meaningful split - then the
    # best four-channel subset by worst in-subset coupling, so the report says
    # what a defensible independence claim would have to give up.
    print("\nSubsets: entropy against the worst coupling inside the subset")
    subsets = [("".join(CHANNELS), "all eight")]
    for name, members in GROUPS.items():
        subsets.append((members, name.split()[0]))

    def worst_r(members):
        idx = [CHANNELS.index(c) for c in members]
        return max(abs(float(corr[i, j])) for i, j in itertools.combinations(idx, 2))

    best4 = min(itertools.combinations(CHANNELS, 4), key=lambda s: worst_r(s))
    subsets.append(("".join(best4), "best 4 by coupling"))

    print(f"   {'subset':<10}{'note':<22}{'sum H':>10}{'worst |r|':>12}{'sigma':>8}")
    for members, note in subsets:
        h = sum(entropy[c] for c in members)
        w = worst_r(members)
        print(f"   {members:<10}{note:<22}{h:>10.4f}{w:>12.4f}{w / noise:>8.0f}")


def report(path, max_lag, sigma_threshold, entropy):
    samples = load_channels(path)
    n = len(samples)
    if n == 0:
        print(f"=== {path} ===\n   empty or too short for one full 8-bit sample")
        return
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
    mi_pairs = {}
    rows = []
    for i in range(NUM_CHANNELS):
        for j in range(i + 1, NUM_CHANNELS):
            r0 = corr[i, j]
            mi = mutual_information(samples[:, i], samples[:, j])
            mi_pairs[(CHANNELS[i], CHANNELS[j])] = mi
            total_mi += mi
            if abs(r0) > sigma_threshold * noise:
                r_lag, lag = strongest_lagged(normed[:, i], normed[:, j], max_lag)
                rows.append((abs(r0), CHANNELS[i], CHANNELS[j], r0, r_lag, lag, mi))
    for _, a, b, r0, r_lag, lag, mi in sorted(rows, reverse=True):
        same = "same group" if group_of(a) == group_of(b) else "across   "
        print(f"   {a}-{b}:  lag 0 r={r0:+.4f} ({abs(r0)/noise:>3.0f} sigma)"
              f"   best r={r_lag:+.4f} at lag {lag:+d}"
              f"   MI={mi:.6f} bit   {same}")
    if not rows:
        print("   none")

    n_pairs = NUM_CHANNELS * (NUM_CHANNELS - 1) // 2
    print(f"\nTotal mutual information over all {n_pairs} pairs: "
          f"{total_mi:.6f} bit/sample")

    print_group_breakdown(corr, mi_pairs, noise)

    if entropy:
        print_budget(entropy, total_mi, corr, noise)
    else:
        print("\nEntropy budget: not computed - pass --entropy-file or --entropy "
              "with the per-channel min-entropy measured on this board.")
    print()


def main(argv=None):
    args = parse_args(argv)

    entropy = {}
    if args.entropy_file:
        entropy = parse_entropy_file(args.entropy_file)
        if not entropy:
            print(f"warning: no per-channel H_original found in "
                  f"{args.entropy_file}", file=sys.stderr)
    if args.entropy:
        entropy.update(args.entropy)

    for path in args.files:
        report(path, args.max_lag, args.sigma, entropy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
