#!/usr/bin/env python3
"""Rev-2: test the claim that a particular ring subset (e.g. ADFH) is special.

Background. channel_crosstalk_rev2.py prints one line labelled "best 4 by
coupling": the four-ring subset with the smallest worst-case |r| out of all 70
possible subsets. It is tempting to read that as an experimental finding - "pick
A+D+F+H and the crosstalk is gone". That reading has a hole: the subset was
chosen by minimising the very number then quoted as evidence, on the same data.
Any of the 70 subsets would show *some* minimum; the question is whether this
one is reproducibly better or whether the ranking among the good ones is noise.

This script answers that, in four parts, and needs no hardware for the first
three.

  1. FULL ENUMERATION. Every subset of the requested size, with its worst |r|,
     summed mutual information and (if an assessment is supplied) summed
     min-entropy. Shows how many subsets are statistically clean, so you can see
     whether the winner stands out or shares the top with a crowd.

  2. SPLIT-HALF STABILITY. The real test of "is this subset special". The capture
     is split into interleaved blocks, forming two independent halves. The best
     subset is picked on half 1, then its rank is looked up on half 2, repeated
     over several independent splits. A genuinely better subset keeps rank 1. A
     noise-driven winner lands at a random rank among the clean subsets. This is
     the standard defence against selecting on the same data you evaluate on.

  3. COUPLING STRUCTURE. Checks the competing explanation: that coupling is
     determined by adjacency of the bit index in the 74HC174, so every subset
     avoiding the adjacent pairs is equally clean and nothing distinguishes them
     beyond noise. Reported as a confusion table against the measured pairs.

  4. COST. What the subset gives up. Dropping to four rings halves the entropy
     budget; the coupling it removes costs a fraction of a percent. Printed side
     by side so the trade is explicit.

  5. HARDWARE CONTROL (printed, not run). The experiment that would actually
     settle it: capture a clean subset and a deliberately coupled one of the same
     size, and compare the assessed joint entropy of each against the sum of its
     channels. If coupling matters, the coupled subset loses more. Commands are
     emitted ready to paste.

Usage:
    python3 verify_subset_claim_rev2.py Rev_2/100kHz/channelA-H.bin \\
        --entropy-file Rev_2/100kHz/results.txt
    python3 verify_subset_claim_rev2.py capture.bin --size 4 --claim ADFH
    python3 verify_subset_claim_rev2.py capture.bin --size 3 --splits 8
"""

import argparse
import itertools
import math
import pathlib
import re
import sys

import numpy as np

CHANNELS = "ABCDEFGH"
NUM_CHANNELS = 8

# Which 74HC174 data input each ring is wired to, and which latch it belongs to.
# Coupling can only propagate inside one latch, so pairs from different latches
# have no bit-index distance at all. See RO_hardware.kicad_sch.
LATCH_PIN = {
    "A": ("B", 0), "B": ("B", 2), "C": ("B", 3), "D": ("B", 5),
    "E": ("A", 0), "F": ("A", 1), "G": ("A", 3), "H": ("A", 4),
}


def parse_entropy_file(path):
    """Per-channel H_original from a collect_all_rev2.sh results.txt."""
    table, current = {}, None
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
    p = argparse.ArgumentParser(
        description="Test whether a 'best subset' of rings is reproducibly best.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("capture", help="channelA-H.bin (all eight rings, simultaneous)")
    p.add_argument("--size", type=int, default=4, help="subset size to enumerate")
    p.add_argument("--claim", default="ADFH",
                   help="the subset being claimed as best, for direct comparison")
    p.add_argument("--entropy-file", metavar="RESULTS.TXT",
                   help="results.txt from collect_all_rev2.sh, for the sum-H column")
    p.add_argument("--splits", type=int, default=6,
                   help="independent split-half repetitions for the stability test")
    p.add_argument("--block", type=int, default=4096,
                   help="block length in samples for the split-half partition")
    p.add_argument("--sigma", type=float, default=4.0,
                   help="significance threshold in units of the 1/sqrt(N) noise floor")
    p.add_argument("--seed", type=int, default=20260828,
                   help="seed for the split-half partitions (reproducibility)")
    p.add_argument("--top", type=int, default=10, help="how many rows to list")
    return p.parse_args(argv)


def load_channels(path):
    """(N, 8) uint8 array of simultaneous samples; firmware packs LSB-first."""
    raw = np.frombuffer(pathlib.Path(path).read_bytes(), dtype=np.uint8)
    bits = np.unpackbits(raw, bitorder="little")
    n = len(bits) // NUM_CHANNELS
    return bits[: n * NUM_CHANNELS].reshape(n, NUM_CHANNELS)


def corr_matrix(samples):
    """Pearson correlation at lag 0 for every channel pair."""
    x = samples.astype(np.float64)
    sd = x.std(0)
    sd[sd == 0] = 1.0
    z = (x - x.mean(0)) / sd
    return (z.T @ z) / len(x)


def mi_matrix(samples):
    """Pairwise mutual information in bits."""
    n = len(samples)
    out = np.zeros((NUM_CHANNELS, NUM_CHANNELS))
    for i, j in itertools.combinations(range(NUM_CHANNELS), 2):
        a, b = samples[:, i].astype(np.int64), samples[:, j].astype(np.int64)
        joint = np.bincount(a * 2 + b, minlength=4) / n
        pa = np.array([1.0 - a.mean(), a.mean()])
        pb = np.array([1.0 - b.mean(), b.mean()])
        mi = sum(joint[k] * math.log2(joint[k] / (pa[k >> 1] * pb[k & 1]))
                 for k in range(4) if joint[k] > 0)
        out[i, j] = out[j, i] = max(mi, 0.0)
    return out


def subset_stats(members, corr, mi, entropy):
    """worst |r|, summed MI and summed H for one subset."""
    idx = [CHANNELS.index(c) for c in members]
    pairs = list(itertools.combinations(idx, 2))
    worst = max(abs(float(corr[i, j])) for i, j in pairs)
    total_mi = sum(float(mi[i, j]) for i, j in pairs)
    sum_h = sum(entropy[c] for c in members) if entropy and all(
        c in entropy for c in members) else None
    return worst, total_mi, sum_h


def rank_subsets(subsets, corr, mi, entropy):
    """Subsets ordered by worst |r|, ascending."""
    rows = []
    for members in subsets:
        worst, total_mi, sum_h = subset_stats(members, corr, mi, entropy)
        rows.append((worst, total_mi, sum_h, members))
    rows.sort(key=lambda r: r[0])
    return rows


def bit_distance(a, b):
    """Distance of the two rings' bit index inside their latch, or None if they
    sit in different latches (no shared silicon, so no shared index)."""
    la, pa = LATCH_PIN[a]
    lb, pb = LATCH_PIN[b]
    return abs(pa - pb) if la == lb else None


# ── 1. enumeration ──────────────────────────────────────────────────────────
def report_enumeration(rows, noise, sigma, claim, top):
    clean_cut = sigma * noise
    clean = [r for r in rows if r[0] < clean_cut]

    print(f"1. ALL {len(rows)} SUBSETS OF SIZE {len(rows[0][3])}, "
          f"best worst-|r| first")
    print(f"   {'subset':<10}{'worst |r|':>11}{'sigma':>8}{'sum MI [bit]':>15}"
          f"{'sum H':>10}")
    for worst, total_mi, sum_h, members in rows[:top]:
        h = f"{sum_h:>10.4f}" if sum_h is not None else f"{'-':>10}"
        mark = "  <- claim" if "".join(members) == claim else ""
        print(f"   {''.join(members):<10}{worst:>11.5f}{worst / noise:>8.1f}"
              f"{total_mi:>15.6f}{h}{mark}")
    if len(rows) > top:
        print(f"   ... {len(rows) - top} more, worst of all: "
              f"{''.join(rows[-1][3])} at |r|={rows[-1][0]:.5f}")

    print(f"\n   Noise floor 1 sigma = {noise:.5f}; a subset is 'clean' when its "
          f"worst |r| < {clean_cut:.5f} ({sigma:g} sigma).")
    print(f"   Clean subsets: {len(clean)} of {len(rows)}.")
    if len(clean) > 1:
        spread = max(r[0] for r in clean) - min(r[0] for r in clean)
        print(f"   Among the clean ones worst |r| spans {spread:.5f}, i.e. "
              f"{spread / noise:.1f} sigma - that spread is what the ranking "
              f"inside this group is built on.")
        print(f"   Clean subsets: {', '.join(''.join(r[3]) for r in clean)}")
    return clean


# ── 2. split-half stability ─────────────────────────────────────────────────
def report_split_half(samples, subsets, entropy, claim, splits, block, seed,
                      sigma, n_clean):
    print(f"\n2. SPLIT-HALF STABILITY over {splits} independent partitions "
          f"(blocks of {block} samples)")
    print("   Pick the best subset on half 1, then look up its rank on half 2.")
    print("   A really better subset keeps rank 1. Noise gives a random rank.")

    n = len(samples)
    n_blocks = n // block
    if n_blocks < 4:
        print("   not enough data for a split-half test")
        return
    rng = np.random.default_rng(seed)
    total = len(subsets)

    print(f"\n   {'split':>6}{'best on half 1':>16}{'its rank on half 2':>21}"
          f"{'best on half 2':>16}{'claim rank h1/h2':>19}")
    ranks, claim_ranks = [], []
    for s in range(splits):
        order = rng.permutation(n_blocks)
        take = order[: n_blocks // 2]
        mask = np.zeros(n_blocks, dtype=bool)
        mask[take] = True
        sel = np.repeat(mask, block)
        # trailing samples beyond whole blocks are dropped from both halves
        body = samples[: n_blocks * block]
        h1, h2 = body[sel], body[~sel]

        rows1 = rank_subsets(subsets, corr_matrix(h1), mi_matrix(h1), entropy)
        rows2 = rank_subsets(subsets, corr_matrix(h2), mi_matrix(h2), entropy)
        order2 = ["".join(r[3]) for r in rows2]
        best1 = "".join(rows1[0][3])
        best2 = order2[0]
        rank2 = order2.index(best1) + 1
        ranks.append(rank2)
        c1 = ["".join(r[3]) for r in rows1].index(claim) + 1 if claim in [
            "".join(r[3]) for r in rows1] else None
        c2 = order2.index(claim) + 1 if claim in order2 else None
        claim_ranks.append((c1, c2))
        cr = f"{c1}/{c2}" if c1 and c2 else "n/a"
        print(f"   {s + 1:>6}{best1:>16}{rank2:>21}{best2:>16}{cr:>19}")

    print(f"\n   Winner of half 1 lands on half 2 at rank: "
          f"{', '.join(str(r) for r in ranks)}")
    print(f"   mean rank {np.mean(ranks):.1f} of {total} subsets "
          f"({n_clean} of them clean)")
    if claim_ranks and all(c[0] and c[1] for c in claim_ranks):
        h1r = [c[0] for c in claim_ranks]
        h2r = [c[1] for c in claim_ranks]
        print(f"   {claim} ranks: half 1 {h1r}, half 2 {h2r} "
              f"(mean {np.mean(h1r + h2r):.1f})")

    stable = sum(1 for r in ranks if r == 1)
    expected_random = (n_clean + 1) / 2 if n_clean else total / 2
    print()
    if stable == splits:
        print(f"   VERDICT: rank 1 on every split - the winner is reproducible, "
              f"the claim of a genuinely best subset holds.")
    elif np.mean(ranks) <= max(2.0, 0.25 * expected_random):
        print(f"   VERDICT: mostly near the top ({stable}/{splits} times rank 1) "
              f"- a weak but real preference.")
    else:
        print(f"   VERDICT: the winner does not survive resampling "
              f"({stable}/{splits} times rank 1; a random pick among the clean "
              f"subsets would average rank {expected_random:.1f}).")
        print(f"   The ranking inside the clean group is noise. Any clean subset "
              f"is as good as the 'best' one, and quoting its worst |r| as an "
              f"achieved value overstates what was measured.")


# ── 3. coupling structure ───────────────────────────────────────────────────
def report_structure(corr, noise, sigma):
    print("\n3. WHAT DETERMINES COUPLING - adjacency of the bit index in the latch")
    cut = sigma * noise
    by_dist = {}
    mism = []
    for a, b in itertools.combinations(CHANNELS, 2):
        i, j = CHANNELS.index(a), CHANNELS.index(b)
        r = float(corr[i, j])
        d = bit_distance(a, b)
        key = "other latch" if d is None else d
        by_dist.setdefault(key, []).append((a + "-" + b, r))
        strong = abs(r) > 10 * cut          # clearly coupled, not borderline
        if (d == 1) != strong:
            mism.append((a + "-" + b, d, r))

    print(f"   {'bit distance':<14}{'pairs':>6}{'max |r|':>11}{'mean |r|':>11}   examples")
    for key in sorted(by_dist, key=lambda k: (isinstance(k, str), k)):
        rows = by_dist[key]
        rs = [abs(r) for _, r in rows]
        ex = ", ".join(nm for nm, _ in sorted(rows, key=lambda t: -abs(t[1]))[:3])
        label = key if isinstance(key, str) else f"{key}"
        print(f"   {label:<14}{len(rows):>6}{max(rs):>11.5f}{np.mean(rs):>11.5f}   {ex}")

    print(f"\n   Prediction: exactly the bit-distance-1 pairs are coupled, all "
          f"others are not.")
    if mism:
        print(f"   MISMATCHES ({len(mism)}): " +
              ", ".join(f"{nm} (d={d}, r={r:+.4f})" for nm, d, r in mism))
        print("   The prediction does not fully hold - worth a look at the routing.")
    else:
        d1 = by_dist.get(1, [])
        print(f"   Holds without exception: all {len(d1)} adjacent pairs are "
              f"coupled and none of the other {28 - len(d1)} pairs is.")
        print("   Consequence: any subset containing no adjacent pair is clean, "
              "and there is nothing to choose between such subsets.")
        clean_by_rule = [
            "".join(s) for s in itertools.combinations(CHANNELS, 4)
            if all(bit_distance(a, b) != 1 for a, b in itertools.combinations(s, 2))
        ]
        print(f"   Subsets of size 4 with no adjacent pair: {len(clean_by_rule)} "
              f"- predicted from the wiring alone, before looking at any data:")
        print("     " + ", ".join(clean_by_rule))


# ── 4. cost ─────────────────────────────────────────────────────────────────
def report_cost(rows, corr, mi, entropy, claim):
    print("\n4. WHAT THE SUBSET COSTS")
    full = "".join(CHANNELS)
    fw, fmi, fh = subset_stats(full, corr, mi, entropy)
    cw, cmi, ch = subset_stats(claim, corr, mi, entropy) if all(
        c in CHANNELS for c in claim) else (None, None, None)
    if cw is None:
        print(f"   {claim}: not a valid channel set")
        return
    print(f"   {'set':<10}{'rings':>6}{'worst |r|':>11}{'sum MI [bit]':>15}{'sum H':>10}")
    for label, w, m, h in (("all eight", fw, fmi, fh), (claim, cw, cmi, ch)):
        hs = f"{h:>10.4f}" if h is not None else f"{'-':>10}"
        print(f"   {label:<10}{8 if label == 'all eight' else len(claim):>6}"
              f"{w:>11.5f}{m:>15.6f}{hs}")
    if fh and ch:
        print(f"\n   Removing the coupling saves {fmi - cmi:.6f} bit/sample of "
              f"mutual information,")
        print(f"   which is {100 * (fmi - cmi) / fh:.2f}% of the full budget "
              f"({fh:.4f} bit/sample).")
        print(f"   It gives up {fh - ch:.4f} bit/sample of entropy, "
              f"{100 * (fh - ch) / fh:.1f}% of the budget.")
        ratio = (fh - ch) / (fmi - cmi) if (fmi - cmi) > 0 else float("inf")
        print(f"   Trade ratio: {ratio:.0f} bits of entropy given up per bit of "
              f"mutual information removed.")
        print("   Note both figures rest on per-channel H measured with one ring "
              "enabled at a time;")
        print("   whether four rings together really deliver their sum is exactly "
              "what part 5 tests.")


# ── 5. hardware control experiment ──────────────────────────────────────────
def report_hardware(claim, entropy):
    print("\n5. THE CONTROL EXPERIMENT THAT WOULD SETTLE IT (needs the board)")
    coupled = None
    for cand in itertools.combinations(CHANNELS, len(claim)):
        pairs = [(a, b) for a, b in itertools.combinations(cand, 2)
                 if bit_distance(a, b) == 1]
        if len(pairs) >= 2:
            coupled = "".join(cand)
            break
    if coupled is None:
        coupled = "EFGH"

    print(f"   Compare a clean subset ({claim}) with a deliberately coupled one "
          f"({coupled},")
    print(f"   which contains two adjacent pairs) at the same ring count, then "
          f"check each")
    print(f"   assessed joint entropy against the sum of its channels. If "
          f"coupling costs")
    print(f"   real entropy, the coupled subset falls further below its sum.")
    print()
    for name in (claim, coupled):
        seq = "".join(("!" + c.lower()) if c in name else ("!" + c) for c in CHANNELS)
        print(f"   # subset {name}")
        print(f"   python3 usb_read.py -s '{seq}!r' -o channel{name}.bin")
        print(f"   python3 unpack_single_channel.py channel{name}.bin "
              f"output{name}.bin --bits {len(name)} --bit-order little")
        print(f"   ea_non_iid -v -i output{name}.bin {len(name)}")
        if entropy and all(c in entropy for c in name):
            print(f"   # sum of per-channel H for {name}: "
                  f"{sum(entropy[c] for c in name):.4f} bit/sample")
        print()
    print("   Read it as: H_original(subset) versus that sum. Rev-1 saw the joint")
    print("   figure come out far ABOVE the sum, which was a sign the joint")
    print("   assessment is optimistic on interleaved sources - so treat a small")
    print("   gap as inconclusive and a large deficit as real.")


def main(argv=None):
    args = parse_args(argv)
    claim = args.claim.upper()
    if not all(c in CHANNELS for c in claim) or len(set(claim)) != len(claim):
        sys.exit(f"--claim must be distinct letters from {CHANNELS}")
    if not 2 <= args.size <= 7:
        sys.exit("--size must be between 2 and 7")
    if len(claim) != args.size:
        print(f"note: --claim {claim} has {len(claim)} rings, "
              f"enumerating size {len(claim)} instead of {args.size}",
              file=sys.stderr)
        args.size = len(claim)

    entropy = parse_entropy_file(args.entropy_file) if args.entropy_file else {}

    samples = load_channels(args.capture)
    n = len(samples)
    if n < 1000:
        sys.exit("capture too short")
    noise = 1.0 / math.sqrt(n)
    corr = corr_matrix(samples)
    mi = mi_matrix(samples)
    subsets = ["".join(s) for s in itertools.combinations(CHANNELS, args.size)]

    print(f"=== {args.capture} ===")
    print(f"{n} simultaneous samples, noise floor 1 sigma = {noise:.5f}, "
          f"claim under test: {claim}")
    if entropy:
        print(f"per-channel H from {args.entropy_file}: " +
              "  ".join(f"{c}={entropy[c]:.4f}" for c in CHANNELS if c in entropy))
    else:
        print("no --entropy-file: sum-H columns are omitted")
    print()

    rows = rank_subsets(subsets, corr, mi, entropy)
    clean = report_enumeration(rows, noise, args.sigma, claim, args.top)
    report_split_half(samples, subsets, entropy, claim, args.splits,
                      args.block, args.seed, args.sigma, len(clean))
    report_structure(corr, noise, args.sigma)
    report_cost(rows, corr, mi, entropy, claim)
    report_hardware(claim, entropy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
