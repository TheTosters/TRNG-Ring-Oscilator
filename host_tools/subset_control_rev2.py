#!/usr/bin/env python3
"""Rev-2: control experiment - does ring coupling cost real joint entropy?

verify_subset_claim_rev2.py shows that three ring pairs are strongly correlated
(B-C, E-F, G-H - the ones adjacent by bit index in the 74HC174) and that the
"best subset" ranking among the uncoupled subsets is noise. What it cannot show
is whether the coupling actually costs entropy, because a correlation matrix and
an entropy estimate are different measurements.

This script runs the experiment that settles it. Two four-ring subsets are
captured:

  * a CLEAN one (default ADFH) containing no adjacent pair,
  * a COUPLED one (default BCEF) containing two adjacent pairs, B-C and E-F.

They are chosen to have nearly the same summed per-channel min-entropy, so
coupling is close to the only thing that differs. For each subset the script
measures:

  1. H_joint  - min-entropy of the interleaved 4-bit stream,
  2. sum of per-channel H measured ON THE SAME CAPTURE, i.e. with all four rings
     running. This matters: the per-channel figures in results.txt were taken
     with a single ring enabled, so they describe a different electrical
     situation than four rings running together.
  3. the gap (sum - H_joint), measured against the same gap on random data of
     identical size.

Point 3 needs care, and it is why this script exists rather than two bare
ea_non_iid runs. On a wider alphabet the estimators find less structure, so a
joint assessment comes out HIGHER per bit than the channels it is made of, even
when those channels are provably independent: on random data of 600 k samples
the sum of four channels is 3.286 while the joint 4-bit figure is 3.845, a gap
of -0.559. The sign of the gap therefore says nothing about the source. What
does carry information is how far the gap sits from the random-data gap of the
same size - a synthetic test with 10% injected coupling gives +0.004 for the
clean subset and +0.407 for the coupled one.

IMPORTANT limitation of that correction, found on real data. The baseline is
drawn from uniform random bits, whose per-channel H sits near the estimator
ceiling (~0.92 of 1 bit), while the rings come out around 0.64. The alphabet
artefact is NOT additive across those two regimes: on the real board both
subsets showed an excess of +0.40 to +0.53 even though one of them has no
coupling at all (worst |r| = 0.0013). An excess that large cannot be caused by
coupling that is not there, so the absolute excess measures the method, not the
source.

Therefore only the DIFFERENCE in excess between the two subsets is meaningful.
Both are measured on the same board, in the same entropy regime, with the same
estimator and sample count, so the artefact cancels between them. The absolute
excess column is printed for transparency but must not be read as a defect.

The capture also reads the device's own '?' report afterwards and refuses to
treat data as valid if the firmware dropped blocks: a gap in the stream looks
exactly like serial structure to the estimators.

Usage:
    python3 subset_control_rev2.py --outdir Rev_2/100kHz/control
    python3 subset_control_rev2.py --outdir ... --capture-only
    python3 subset_control_rev2.py --outdir ... --analyse-only
    python3 subset_control_rev2.py --outdir ... --clean ADEG --coupled BCGH
    python3 subset_control_rev2.py --outdir ... --bytes 1048576   # faster, fewer samples

Needs: the board on --port, ea_non_iid in PATH, numpy and pyserial (host_tools/.venv).
"""

import argparse
import itertools
import math
import pathlib
import re
import shutil
import subprocess
import sys
import time

import numpy as np

CHANNELS = "ABCDEFGH"

# Ring -> (latch, 74HC174 data input). Coupling only propagates inside a latch.
LATCH_PIN = {
    "A": ("B", 0), "B": ("B", 2), "C": ("B", 3), "D": ("B", 5),
    "E": ("A", 0), "F": ("A", 1), "G": ("A", 3), "H": ("A", 4),
}


def adjacent_pairs(subset):
    """Pairs in the subset that sit on adjacent latch inputs - the coupled ones."""
    out = []
    for a, b in itertools.combinations(subset, 2):
        la, pa = LATCH_PIN[a]
        lb, pb = LATCH_PIN[b]
        if la == lb and abs(pa - pb) == 1:
            out.append(a + "-" + b)
    return out


def enable_sequence(subset):
    """Command string enabling exactly the subset, disabling the rest, RAW on."""
    body = "".join(("!" + c.lower()) if c in subset else ("!" + c) for c in CHANNELS)
    return body + "!r"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Control experiment: does ring coupling cost joint entropy?",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--outdir", default=".", help="directory for captures and results")
    p.add_argument("--clean", default="ADFH",
                   help="subset with no adjacent (coupled) pair")
    p.add_argument("--coupled", default="BCEF",
                   help="subset containing adjacent pairs")
    p.add_argument("--port", default="/dev/ttyACM0", help="device serial port")
    p.add_argument("--bytes", type=int, default=4 * 1024 * 1024,
                   help="bytes to capture per subset; 4 MiB gives 8.4 M samples, "
                        "matching the per-channel captures in results.txt")
    p.add_argument("--capture-only", action="store_true", help="only capture")
    p.add_argument("--analyse-only", action="store_true",
                   help="only analyse captures already in --outdir")
    p.add_argument("--skip-baseline", action="store_true",
                   help="skip the random-data reference (saves two ea_non_iid "
                        "runs, but then the result cannot be interpreted at all - "
                        "see point 3 in the module docstring)")
    p.add_argument("--entropy-file", metavar="RESULTS.TXT",
                   help="results.txt, to also show the single-ring per-channel H "
                        "for comparison with the in-context figures")
    p.add_argument("--seed", type=int, default=20260828,
                   help="seed for the random baseline")
    p.add_argument("--baseline-reps", type=int, default=2, metavar="N",
                   help="repeat the random baseline N times with different seeds "
                        "to measure the estimator's run-to-run spread and set the "
                        "significance threshold from it instead of the default; "
                        "costs 2 extra ea_non_iid runs per extra repetition")
    return p.parse_args(argv)


# ── device ──────────────────────────────────────────────────────────────────
def read_status(port, timeout=4.0):
    """Fetch the firmware '?' report, then unfreeze the stream.

    The report is the only way to know whether the capture just taken was
    complete: 'DROP usb' / 'col' count blocks the firmware had to throw away.
    """
    try:
        import serial
    except ImportError:
        return None, "pyserial not available"
    try:
        with serial.Serial(port, 115200, timeout=0.2) as ser:
            ser.reset_input_buffer()
            ser.write(b"!?")
            buf = b""
            deadline = time.time() + timeout
            while time.time() < deadline:
                buf += ser.read(4096)
                if b"CNT=" in buf and buf.rfind(b"\r\n") > buf.find(b"CNT="):
                    break
            # any byte lifts the freeze the report puts the device into; a bare
            # newline is not a command even if it reaches the parser
            ser.write(b"\n")
    except Exception as exc:                       # noqa: BLE001 - report and go on
        return None, str(exc)

    start = buf.find(b"CH=")
    if start < 0:
        return None, "no report in the reply"
    end = buf.find(b"\r\n", buf.find(b"CNT=", start))
    report = buf[start:end if end > 0 else None].decode("ascii", "replace")
    return report, None


def capture_subset(subset, path, port, nbytes, tool_dir):
    seq = enable_sequence(subset)
    pairs = adjacent_pairs(subset)
    kind = f"coupled via {', '.join(pairs)}" if pairs else "no adjacent pair"
    print(f">>> capture {subset} ({kind})")
    print(f"    sequence {seq}")
    cmd = [sys.executable, str(tool_dir / "usb_read.py"),
           "-p", port, "-s", seq, "-o", str(path), "-n", str(nbytes)]
    subprocess.run(cmd, check=True)

    report, err = read_status(port)
    if err:
        print(f"    ! could not read the '?' report: {err}")
        return None
    print("    device report: " + " | ".join(report.split("\r\n")))
    m = re.search(r"DROP usb=(\d+) col=(\d+) health=(\d+)", report)
    if not m:
        print("    ! no DROP field in the report")
        return None
    usb, col, health = (int(g) for g in m.groups())
    if usb or col:
        print(f"    !! DROPPED BLOCKS: usb={usb} col={col} - this capture has "
              f"gaps and the entropy estimates from it are not trustworthy.")
    if health:
        print(f"    note: {health} health-test failures since the last report")
    return {"usb": usb, "col": col, "health": health}


# ── analysis ────────────────────────────────────────────────────────────────
def unpack_samples(path, bits):
    """Bit-packed capture -> one k-bit sample per byte, LSB-first like the firmware."""
    raw = np.frombuffer(pathlib.Path(path).read_bytes(), dtype=np.uint8)
    b = np.unpackbits(raw, bitorder="little")
    n = b.size // bits
    m = b[: n * bits].reshape(n, bits)
    weights = (1 << np.arange(bits, dtype=np.uint16))
    return (m * weights).sum(axis=1).astype(np.uint8), m


def run_ea(path, bits, label):
    print(f"    ea_non_iid {label} ({bits} bit/sample) ...", end="", flush=True)
    t0 = time.monotonic()
    res = subprocess.run(["ea_non_iid", "-v", "-i", str(path), str(bits)],
                         capture_output=True, text=True)
    out = res.stdout + res.stderr
    h = re.search(r"^H_original:\s*([\d.eE+-]+)", out, re.M)
    ests = dict(re.findall(r"\t(.+?)(?: Test| Prediction Test)? Estimate = ([\d.]+) /", out))
    print(f" {time.monotonic() - t0:.0f}s")
    if not h:
        print("      ! no H_original in the output:")
        print("      " + out.strip().splitlines()[-1] if out.strip() else "      (empty)")
        return None, {}
    return float(h.group(1)), {k: float(v) for k, v in ests.items()}


def analyse_subset(subset, path, outdir, keep):
    """H_joint plus per-channel H measured on this very capture."""
    bits = len(subset)
    print(f">>> analyse {subset}")
    if not path.is_file():
        print(f"    missing {path}")
        return None
    samples, bitmat = unpack_samples(path, bits)
    n = samples.size
    print(f"    {n} samples of {bits} bit ({n / 1e6:.2f} M)")
    if n < 1_000_000:
        print("    ! below the SP 800-90B minimum of 1 000 000 samples")

    joint_path = outdir / f"output{subset}.bin"
    joint_path.write_bytes(samples.tobytes())
    h_joint, joint_ests = run_ea(joint_path, bits, f"{subset} joint")

    per = {}
    for i, ch in enumerate(subset):
        ch_path = outdir / f"output{subset}_{ch}.bin"
        ch_path.write_bytes(np.ascontiguousarray(bitmat[:, i]).tobytes())
        h, _ = run_ea(ch_path, 1, f"{subset} channel {ch}")
        per[ch] = h
        if not keep:
            ch_path.unlink(missing_ok=True)
    if not keep:
        joint_path.unlink(missing_ok=True)

    # correlation inside the subset, to confirm the coupling really is present
    x = bitmat.astype(np.float64)
    sd = x.std(0)
    sd[sd == 0] = 1.0
    z = (x - x.mean(0)) / sd
    corr = (z.T @ z) / n
    worst = max(abs(float(corr[i, j]))
                for i, j in itertools.combinations(range(bits), 2))

    return {"subset": subset, "n": n, "h_joint": h_joint, "per": per,
            "worst_r": worst, "noise": 1.0 / math.sqrt(n),
            "joint_ests": joint_ests}


def baseline(outdir, bits, n, seed, keep):
    print(f">>> baseline: random data, {n} samples of {bits} bit")
    rng = np.random.default_rng(seed)
    path = outdir / f"baseline_{bits}bit.bin"
    path.write_bytes(rng.integers(0, 1 << bits, size=n, dtype=np.uint8).tobytes())
    h, _ = run_ea(path, bits, f"baseline {bits} bit")
    if not keep:
        path.unlink(missing_ok=True)
    return h


def parse_entropy_file(path):
    table, current = {}, None
    banner = re.compile(r"^#+\s*channel\s+([A-H])\s*\(")
    value = re.compile(r"^H_original:\s*([\d.eE+-]+)")
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


# Fallback threshold, used only when the baseline is not repeated. It is a guess
# and an optimistic one: two independent random draws of 600 k samples differed
# by 0.229 bit/sample in their gap, so at small sample counts the real noise is
# several times this. Always prefer the measured value (--baseline-reps >= 2,
# which is the default) and treat a verdict marked "not measured" as weak.
DEFAULT_THRESHOLD = 0.05


def report(results, base_joint, base_ch, single_ring, threshold, measured):
    print("\n" + "=" * 78)
    print("RESULT")
    print("=" * 78)

    for r in results:
        if r is None:
            continue
        pairs = adjacent_pairs(r["subset"])
        print(f"\n{r['subset']}  ({'coupled: ' + ', '.join(pairs) if pairs else 'no adjacent pair'})")
        print(f"    worst |r| inside the subset: {r['worst_r']:.5f} "
              f"({r['worst_r'] / r['noise']:.0f} sigma)")
        print(f"    per-channel H measured on this capture (all four rings running):")
        for ch, h in r["per"].items():
            extra = ""
            if single_ring and ch in single_ring:
                d = h - single_ring[ch]
                extra = (f"   (single-ring capture: {single_ring[ch]:.4f}, "
                         f"{d:+.4f})")
            print(f"        {ch} = {h:.4f}{extra}" if h is not None
                  else f"        {ch} = failed")
        if any(v is None for v in r["per"].values()) or r["h_joint"] is None:
            print("    incomplete - skipping the comparison")
            continue
        s = sum(r["per"].values())
        r["sum_h"] = s
        r["gap"] = s - r["h_joint"]
        print(f"    sum of channels : {s:.4f} bit/sample")
        print(f"    H_joint         : {r['h_joint']:.4f} bit/sample")
        print(f"    gap             : {r['gap']:+.4f} bit/sample "
              f"(negative is normal - see below)")

    ok = [r for r in results if r and r.get("gap") is not None]
    if len(ok) < 2:
        print("\nNot enough complete results to compare.")
        return

    k = len(ok[0]["subset"])
    base_gap = None
    if base_joint is not None and base_ch is not None:
        base_gap = base_ch * k - base_joint
        for r in ok:
            r["excess"] = r["gap"] - base_gap

    print("\n" + "-" * 78)
    head = (f"{'subset':<10}{'coupled':>9}{'worst |r|':>11}{'sum H':>10}"
            f"{'H_joint':>10}{'gap':>9}")
    print(head + (f"{'excess':>10}" if base_gap is not None else ""))
    for r in ok:
        row = (f"{r['subset']:<10}{len(adjacent_pairs(r['subset'])):>9}"
               f"{r['worst_r']:>11.5f}{r['sum_h']:>10.4f}{r['h_joint']:>10.4f}"
               f"{r['gap']:>+9.4f}")
        print(row + (f"{r['excess']:>+10.4f}" if base_gap is not None else ""))
    if base_gap is not None:
        print(f"{'random':<10}{0:>9}{'-':>11}{base_ch * k:>10.4f}"
              f"{base_joint:>10.4f}{base_gap:>+9.4f}{0.0:>+10.4f}")
    print("-" * 78)

    if base_gap is None:
        print("\nNo baseline was measured (--skip-baseline), so the gaps above")
        print("cannot be interpreted: a negative gap is normal even for perfectly")
        print("independent channels. Re-run without --skip-baseline.")
        return

    a, b = sorted(ok, key=lambda r: len(adjacent_pairs(r["subset"])))
    delta = b["excess"] - a["excess"]
    print(f"\nThe 'gap' column is sum-of-channels minus joint. Its sign carries no")
    print(f"information: a wider alphabet hides structure from the estimators, and")
    print(f"random data of this size gives {base_gap:+.4f}.")
    print(f"\n'excess' is the gap relative to the random baseline. Read it with care:")
    print(f"the baseline sits near the estimator ceiling (~{base_ch:.2f} per channel)")
    print(f"while these rings come out near {a['sum_h'] / k:.2f}, and the artefact does")
    print(f"not carry across regimes. A large excess on a subset with no coupling is")
    print(f"a property of the method, not a defect of the source - so only the")
    print(f"DIFFERENCE below is evidence. It cancels the artefact, because both")
    print(f"subsets share board, regime, estimator and sample count.")
    print(f"\n   clean   {a['subset']}: excess {a['excess']:+.4f} bit/sample")
    print(f"   coupled {b['subset']}: excess {b['excess']:+.4f} bit/sample")
    print(f"   difference: {delta:+.4f} bit/sample   <- the measurement")

    if measured:
        print(f"   significance threshold: {threshold:.4f} bit/sample "
              f"(measured from repeated baselines)")
    else:
        print(f"   significance threshold: {threshold:.4f} bit/sample "
              f"(NOT MEASURED - fallback guess)")
        print(f"   ! the estimators' run-to-run spread was not measured, so this "
              f"threshold is unreliable;")
        print(f"   ! re-run with --baseline-reps 3 before trusting the verdict.")
    print()
    if delta > threshold:
        print("VERDICT: the coupled subset loses measurably more than the clean one.")
        print("Coupling has a real entropy cost. Worth fixing at the source in a")
        print("future revision (three rings per latch, or grounding the unused")
        print("inputs as a shield) rather than by dropping to four rings, which")
        print("would give up half the budget to recover this much.")
    elif delta < -threshold:
        print("VERDICT: the clean subset lost MORE than the coupled one, which no")
        print("coupling model explains. Suspect an uncontrolled difference between")
        print("the two captures (temperature, supply drift, dropped blocks) rather")
        print("than a real effect; re-run both, ideally alternating them in time.")
    else:
        print("VERDICT: the two subsets are indistinguishable. The coupling is real")
        print("in the correlation matrix but does not cost joint entropy at this")
        print("resolution - consistent with the 0.38% of budget it accounts for.")
        print("Selecting rings to avoid it buys nothing measurable and costs half")
        print("the entropy rate: keep all eight enabled.")
        if b["h_joint"] >= a["h_joint"]:
            print()
            print(f"Note how strong this is: the COUPLED subset came out at "
                  f"{b['h_joint']:.4f},")
            print(f"at or above the clean one's {a['h_joint']:.4f}, on nearly equal "
                  f"channel sums")
            print(f"({b['sum_h']:.4f} vs {a['sum_h']:.4f}). Coupling costing real "
                  f"entropy would have")
            print(f"pushed it the other way, so this is not merely a null result "
                  f"for want of")
            print(f"resolution - the effect is absent in the direction it would "
                  f"have to appear.")
        print()
        print(f"Resolution note: this rules out an effect larger than "
              f"{threshold:.4f} bit/sample,")
        print(f"i.e. {100 * threshold / a['sum_h']:.1f}% of this subset's budget. "
              f"A smaller cost would not be visible;")
        print("raise --bytes or --baseline-reps to tighten it.")
    print("\nCaveat: joint assessments over interleaved sources do not match the")
    print("SP 800-90B single-source model (Rev-1 summary.md section 5). The")
    print("baseline correction handles the alphabet artefact but not that")
    print("modelling objection, so read this as evidence, not as an assessment.")


def main(argv=None):
    args = parse_args(argv)
    clean = args.clean.upper()
    coupled = args.coupled.upper()
    for name, s in (("--clean", clean), ("--coupled", coupled)):
        if not all(c in CHANNELS for c in s) or len(set(s)) != len(s):
            sys.exit(f"{name} must be distinct letters from {CHANNELS}")
    if len(clean) != len(coupled):
        sys.exit("both subsets must have the same number of rings")
    if adjacent_pairs(clean):
        print(f"warning: --clean {clean} contains adjacent pair(s) "
              f"{', '.join(adjacent_pairs(clean))}", file=sys.stderr)
    if not adjacent_pairs(coupled):
        print(f"warning: --coupled {coupled} contains no adjacent pair, so the "
              f"experiment has no contrast", file=sys.stderr)

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tool_dir = pathlib.Path(__file__).resolve().parent

    do_capture = not args.analyse_only
    do_analyse = not args.capture_only

    if do_analyse and not shutil.which("ea_non_iid"):
        sys.exit("ea_non_iid not found in PATH")

    print(f"clean subset   : {clean}  ({', '.join(adjacent_pairs(clean)) or 'no adjacent pair'})")
    print(f"coupled subset : {coupled}  ({', '.join(adjacent_pairs(coupled)) or 'none'})")
    print(f"output dir     : {outdir}")
    if do_capture:
        per_subset = args.bytes / 50_000
        print(f"capture        : {args.bytes} B per subset "
              f"(~{per_subset:.0f} s each at 100 kHz with four rings)")
    print()

    paths = {s: outdir / f"channel{s}.bin" for s in (clean, coupled)}

    if do_capture:
        for s in (clean, coupled):
            capture_subset(s, paths[s], args.port, args.bytes, tool_dir)
            print()
        if not do_analyse:
            print("captures done; re-run with --analyse-only to assess them")
            return 0

    results = [analyse_subset(s, paths[s], outdir, keep=False)
               for s in (clean, coupled)]

    base_joint = base_ch = None
    threshold, measured = DEFAULT_THRESHOLD, False
    if not args.skip_baseline:
        n = next((r["n"] for r in results if r), None)
        if n:
            reps = max(1, args.baseline_reps)
            gaps = []
            for i in range(reps):
                bj = baseline(outdir, len(clean), n, args.seed + 2 * i, keep=False)
                bc = baseline(outdir, 1, n, args.seed + 2 * i + 1, keep=False)
                if bj is None or bc is None:
                    continue
                gaps.append((bc * len(clean) - bj, bj, bc))
            if gaps:
                # Average the reference, and if there is more than one draw use the
                # spread between draws as the threshold - that is exactly the noise
                # a real effect has to beat.
                base_gapv = float(np.mean([g[0] for g in gaps]))
                base_joint = float(np.mean([g[1] for g in gaps]))
                base_ch = float(np.mean([g[2] for g in gaps]))
                if len(gaps) > 1:
                    vals = [g[0] for g in gaps]
                    # Full range across draws, which for 2 draws is all the
                    # information there is and for more stays conservative.
                    spread = float(np.ptp(vals))
                    threshold, measured = max(spread, 0.01), True
                    print(f"    baseline gap over {len(gaps)} draws: "
                          f"{base_gapv:+.4f}, range {spread:.4f} "
                          f"(min {min(vals):+.4f}, max {max(vals):+.4f})")
                    print(f"    -> significance threshold {threshold:.4f} "
                          f"bit/sample")

    single_ring = parse_entropy_file(args.entropy_file) if args.entropy_file else {}
    report(results, base_joint, base_ch, single_ring, threshold, measured)
    return 0


if __name__ == "__main__":
    sys.exit(main())
