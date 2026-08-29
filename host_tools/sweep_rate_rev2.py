#!/usr/bin/env python3
"""Rev-2: sweep the sampling rate and find the recommended operating point.

Uses the firmware's 't' command (Rev-2 only) to step through the sampling rate
table, capturing and assessing at each rate, then reports which rate maximises
usable output.

WHAT IS MEASURED, AND WHY THAT METRIC

Entropy per sample falls as the rate rises - jitter gets less time to accumulate
between latch pulses - while samples per second rise. The product is what
matters, and it turns out to be exactly what the device can deliver:

    required compression for a 2x margin:  m = ceil(16 / H_sample)
    output rate:                           f_s / m  bytes/s
    which is proportional to              f_s * H_sample

(one sample is one byte with eight rings, and a 256-bit block needs 512 bits of
entropy for the 2x margin usually required to treat the output of a vetted
conditioning function as full entropy). So maximising entropy per second is the
same as maximising output at fixed quality, and the sweep can rank rates on a
single number. A rate needing m > 9 is out of reach, because that is the
firmware's maximum compression.

ONE CAPTURE PER RATE, NOT NINE

Per-channel figures come from the all-eight-rings capture by de-interleaving it,
not from separate single-ring captures. That is 8x less capturing, and it is the
more representative measurement anyway: it is how the device actually runs. It
also gives the crosstalk matrix, which needs simultaneous samples, for free.

WHAT ELSE COMES OUT

Whether the pair coupling (B-C, E-F, G-H) depends on the sampling rate. If the
coupling is crosstalk at the instant of latching, it is a property of one event
and should not move with the rate; if it comes from the rings pulling each
other's phase, it must change. That is the open question from the coupling
analysis and this sweep answers it as a by-product.

METHODOLOGY NOTES BUILT IN

  * Rates are visited in a shuffled order by default. Sweeping monotonically
    would let the board's warm-up drift correlate with the rate and be read as a
    rate effect. The order used is recorded in the summary.
  * Every capture is bracketed by the firmware's '?' report. The report resets
    the DROP counters when read, so the reading afterwards covers exactly that
    capture. A capture with dropped blocks is flagged and excluded from the
    recommendation: a gap in the stream looks like serial structure to the
    estimators. Expect drops in RAW mode above ~250 kHz, where the stream
    outruns CDC.
  * The rate is verified by reading FS= back from the report, not assumed. An
    unchanged FS means the firmware predates the 't' command.
  * One random-data baseline is measured for the sample count in use, giving the
    estimator ceiling all the H figures should be read against (it is ~0.90, not
    1.0, and that matters when judging how much room is left).

Usage:
    python3 sweep_rate_rev2.py --outdir Rev_2/sweep
    python3 sweep_rate_rev2.py --outdir Rev_2/sweep --rates 1,2,3,4,5
    python3 sweep_rate_rev2.py --outdir Rev_2/sweep --capture-only
    python3 sweep_rate_rev2.py --outdir Rev_2/sweep --analyse-only
    python3 sweep_rate_rev2.py --outdir Rev_2/sweep --joint     # slow, optional

Needs: board on --port with the 't' command, ea_non_iid in PATH, numpy,
pyserial. Run from host_tools/ so the helper scripts are found.
"""

import argparse
import itertools
import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import time

import numpy as np

CHANNELS = "ABCDEFGH"
NUM_CHANNELS = 8

# Must match sampling_arr[] in Core/Src/entropy_collector.c. Index is the digit
# the 't' command takes; the frequency is what the firmware reports as FS=.
RATE_TABLE = {
    0: 50_000, 1: 100_000, 2: 150_000, 3: 200_000, 4: 250_000,
    5: 300_000, 6: 350_000, 7: 400_000, 8: 500_000, 9: 600_000,
}

ENABLE_ALL_RAW = "!a!b!c!d!e!f!g!h!r"
SHA_BLOCK_BITS = 256
DIGEST_BYTES = 32
MAX_MULTIPLICITY = 9        # MAX_BUFFER_MULTIPLICITY in the firmware
MARGIN = 2.0                # entropy bits per output bit required


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Sweep the sampling rate and recommend an operating point.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--outdir", default="Rev_2/sweep",
                   help="root directory; one subdirectory per rate")
    p.add_argument("--rates", default="0,1,2,3,4,5,6,7,8,9",
                   help="comma separated table indices to visit")
    p.add_argument("--bytes", type=int, default=4 * 1024 * 1024,
                   help="bytes per capture; with eight rings this is also the "
                        "sample count per channel")
    p.add_argument("--port", default="/dev/ttyACM0", help="device serial port")
    p.add_argument("--order", choices=("shuffled", "asc", "desc"),
                   default="shuffled",
                   help="visit order; shuffled decorrelates warm-up drift from rate")
    p.add_argument("--seed", type=int, default=20260828, help="seed for the order")
    p.add_argument("--capture-only", action="store_true")
    p.add_argument("--analyse-only", action="store_true")
    p.add_argument("--report-only", action="store_true",
                   help="rebuild the report from the summary.json files already "
                        "written, without re-running any estimator")
    p.add_argument("--joint", action="store_true",
                   help="also assess the interleaved 8-bit stream; ~7x the cost of "
                        "all eight channels together and not used as the budget")
    p.add_argument("--skip-subset", action="store_true",
                   help="skip verify_subset_claim_rev2.py per rate")
    p.add_argument("--baseline-reps", type=int, default=3, metavar="N",
                   help="random-data baselines to average for the estimator "
                        "ceiling; independent draws differ by a few percent, so "
                        "one draw makes the %%ceil column look more precise than "
                        "it is")
    p.add_argument("--keep-unpacked", action="store_true",
                   help="keep the per-channel one-sample-per-byte files")
    return p.parse_args(argv)


# ── device ──────────────────────────────────────────────────────────────────
def open_port(port):
    import serial
    return serial.Serial(port, 115200, timeout=0.2)


def read_report(ser, timeout=4.0):
    """Read the '?' report. Resets the firmware's DROP counters as a side effect."""
    ser.reset_input_buffer()
    ser.write(b"!?")
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        buf += ser.read(4096)
        if b"CNT=" in buf and buf.rfind(b"\r\n") > buf.find(b"CNT="):
            break
    start = buf.find(b"CH=")
    if start < 0:
        return None
    end = buf.find(b"\r\n", buf.find(b"CNT=", start))
    return buf[start:end if end > 0 else None].decode("ascii", "replace")


def parse_report(text):
    if not text:
        return {}
    out = {}
    m = re.search(r"FS=(\d+)Hz", text)
    if m:
        out["fs"] = int(m.group(1))
    m = re.search(r"CH=(\S+)\s+N=(\d+)", text)
    if m:
        out["channels"], out["n_channels"] = m.group(1), int(m.group(2))
    m = re.search(r"MODE=(\w+)", text)
    if m:
        out["mode"] = m.group(1)
    m = re.search(r"BUF=(\d+)B RATIO=(\d+)", text)
    if m:
        out["buf"], out["ratio"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"DROP usb=(\d+) col=(\d+) health=(\d+)", text)
    if m:
        out["drop_usb"], out["drop_col"], out["health"] = (int(g) for g in m.groups())
    return out


def send(ser, seq, settle=0.05):
    """Send a command string byte by byte, as the firmware parser expects."""
    for ch in seq:
        ser.write(ch.encode("ascii"))
        time.sleep(settle)


def set_rate(ser, index):
    """'!t' followed by the digit, which goes out unprefixed - see CDC_Receive_FS."""
    ser.write(b"!t")
    time.sleep(0.05)
    ser.write(str(index).encode("ascii"))
    time.sleep(0.1)


def capture_rate(index, outdir, port, nbytes, tool_dir):
    """Set the rate, then capture, all on one open port.

    The port must not be reopened between setting the rate and reading: the
    firmware streams continuously, so any gap fills the driver buffer with data
    from before the rate change and inflates DROP with the gap itself.

    Synchronisation uses the firmware's own freeze. Reading the '?' report stops
    the sampling timer and the stream and zeroes the DROP counters; the input
    buffer can then be discarded while nothing is arriving; and the byte that
    lifts the freeze also schedules a collector reset and restarts the timer from
    zero (see resumeTransfer() and updateCollector()). So the first byte captured
    is the first byte produced at the new rate.
    """
    want = RATE_TABLE[index]
    print(f"\n>>> rate index {index} -> {want / 1000:g} kHz")
    path = outdir / "channelA-H.bin"

    with open_port(port) as ser:
        set_rate(ser, index)
        send(ser, ENABLE_ALL_RAW)

        before = parse_report(read_report(ser))
        if "fs" not in before:
            print("    ! no readable '?' report - is this the Rev-2 firmware?")
            return None
        got = before["fs"]
        if abs(got - want) > max(1000, want * 0.02):
            print(f"    ! FS={got} Hz but {want} Hz was requested.")
            print(f"    ! The firmware probably predates the 't' command "
                  f"(rebuild and reflash).")
            return None
        print(f"    confirmed FS={got} Hz, CH={before.get('channels')} "
              f"N={before.get('n_channels')} MODE={before.get('mode')}")
        if before.get("n_channels") != NUM_CHANNELS:
            print(f"    ! expected 8 channels enabled, got "
                  f"{before.get('n_channels')}")
            return None
        if before.get("mode") != "RAW":
            print(f"    ! expected RAW mode, got {before.get('mode')}")
            return None

        # Stream is frozen here and DROP is zeroed. Drop whatever the driver still
        # holds from before the rate change, then unfreeze and read immediately.
        ser.reset_input_buffer()
        ser.write(b"\n")               # any byte resumes; not a command unprefixed

        got_bytes = 0
        t0 = time.time()
        last_print = t0
        last_progress = t0
        with open(path, "wb") as f:
            while got_bytes < nbytes:
                chunk = ser.read(min(65536, nbytes - got_bytes))
                now = time.time()
                if chunk:
                    f.write(chunk)
                    got_bytes += len(chunk)
                    last_progress = now
                elif now - last_progress > 10.0:
                    print(f"\n    ! no data for 10 s at {got_bytes} of {nbytes} B "
                          f"- is the device streaming?")
                    return None
                if now - last_print >= 1.0:
                    rate = got_bytes / (now - t0) if now > t0 else 0
                    eta = (nbytes - got_bytes) / rate if rate > 0 else 0
                    print(f"\r    {got_bytes / 1e6:5.2f} / {nbytes / 1e6:.2f} MB  "
                          f"{rate / 1000:6.1f} kB/s  ETA {eta:4.0f} s", end="",
                          flush=True)
                    last_print = now
        elapsed = time.time() - t0
        print(f"\r    captured {nbytes} B in {elapsed:.0f} s "
              f"({nbytes / elapsed / 1000:.1f} kB/s)" + " " * 20)

        after = parse_report(read_report(ser))

    drops = after.get("drop_usb", -1), after.get("drop_col", -1)
    health = after.get("health", -1)
    sampling_only = nbytes / want
    print(f"    sampling alone needs {sampling_only:.0f} s; "
          f"DROP usb={drops[0]} col={drops[1]} health={health}")
    if drops[0] > 0 or drops[1] > 0:
        print(f"    !! dropped blocks - this capture has gaps in time and its "
              f"entropy estimates are not trustworthy")

    return {"index": index, "fs_requested": want, "fs_reported": got,
            "bytes": nbytes, "seconds": elapsed, "drop_usb": drops[0],
            "drop_col": drops[1], "health": health,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}


# ── analysis ────────────────────────────────────────────────────────────────
def run_ea(path, bits):
    res = subprocess.run(["ea_non_iid", "-v", "-i", str(path), str(bits)],
                         capture_output=True, text=True)
    out = res.stdout + res.stderr
    m = re.search(r"^H_original:\s*([\d.eE+-]+)", out, re.M)
    return (float(m.group(1)) if m else None), out


def deinterleave(path):
    """(N, 8) uint8 of simultaneous samples; firmware packs LSB-first."""
    raw = np.frombuffer(pathlib.Path(path).read_bytes(), dtype=np.uint8)
    return np.stack([(raw >> i) & 1 for i in range(NUM_CHANNELS)], axis=1)


def corr_worst(bits):
    x = bits.astype(np.float64)
    sd = x.std(0)
    sd[sd == 0] = 1.0
    z = (x - x.mean(0)) / sd
    c = (z.T @ z) / len(x)
    pairs = list(itertools.combinations(range(NUM_CHANNELS), 2))
    worst = max(abs(float(c[i, j])) for i, j in pairs)
    # the three pairs adjacent by latch bit index, the ones known to be coupled
    named = {"B-C": (1, 2), "E-F": (4, 5), "G-H": (6, 7)}
    return worst, {k: float(c[i, j]) for k, (i, j) in named.items()}, c


def analyse_rate(index, outdir, joint, skip_subset, keep, tool_dir, cap_info):
    path = outdir / "channelA-H.bin"
    if not path.is_file():
        print(f"    missing {path}")
        return None
    print(f"\n>>> analyse rate index {index} ({RATE_TABLE[index] / 1000:g} kHz)")
    bits = deinterleave(path)
    n = len(bits)
    print(f"    {n} samples per channel ({n / 1e6:.2f} M)")
    if n < 1_000_000:
        print("    ! below the SP 800-90B minimum of 1 000 000 samples")

    # results.txt in the format collect_all_rev2.sh writes, so
    # channel_crosstalk_rev2.py can read it with --entropy-file
    results = outdir / "results.txt"
    lines, per = [], {}
    t0 = time.monotonic()
    for i, ch in enumerate(CHANNELS):
        f = outdir / f"output{ch}.bin"
        f.write_bytes(np.ascontiguousarray(bits[:, i]).tobytes())
        h, out = run_ea(f, 1)
        per[ch] = h
        lines.append(f"########## channel {ch} (1 bit/sample, little) ##########")
        lines.append(out.strip())
        lines.append("")
        print(f"    {ch}: H={h:.4f}" if h is not None else f"    {ch}: failed")
        if not keep:
            f.unlink(missing_ok=True)

    h_joint = None
    if joint:
        f = outdir / "outputA-H.bin"
        f.write_bytes(np.ascontiguousarray(
            (bits * (1 << np.arange(NUM_CHANNELS, dtype=np.uint16))).sum(1)
            .astype(np.uint8)).tobytes())
        h_joint, out = run_ea(f, NUM_CHANNELS)
        lines.append("########## channels A-H (8 bit/sample, little) ##########")
        lines.append(out.strip())
        lines.append("")
        print(f"    joint: H={h_joint:.4f}" if h_joint is not None else
              "    joint: failed")
        if not keep:
            f.unlink(missing_ok=True)
    results.write_text("\n".join(lines))
    print(f"    ea_non_iid took {time.monotonic() - t0:.0f} s")

    worst, named, _ = corr_worst(bits)
    print(f"    worst |r| = {worst:.5f}   " +
          "  ".join(f"{k}={v:+.4f}" for k, v in named.items()))

    # full crosstalk report, saved for the record
    cro = subprocess.run(
        [sys.executable, str(tool_dir / "channel_crosstalk_rev2.py"), str(path),
         "--entropy-file", str(results)], capture_output=True, text=True)
    (outdir / "crosstalk.txt").write_text(cro.stdout + cro.stderr)

    if not skip_subset:
        sub = subprocess.run(
            [sys.executable, str(tool_dir / "verify_subset_claim_rev2.py"),
             str(path), "--entropy-file", str(results), "--splits", "4"],
            capture_output=True, text=True)
        (outdir / "subset_claim.txt").write_text(sub.stdout + sub.stderr)

    ok = all(v is not None for v in per.values())
    rec = {"index": index, "fs": RATE_TABLE[index], "n_samples": n,
           "per_channel": per, "sum_h": sum(v for v in per.values() if v) if ok else None,
           "h_joint": h_joint, "worst_r": worst, "named_r": named}
    if cap_info:
        rec.update({k: cap_info.get(k) for k in
                    ("drop_usb", "drop_col", "health", "seconds", "timestamp",
                     "fs_reported", "bytes")})
    (outdir / "summary.json").write_text(json.dumps(rec, indent=2))
    return rec


def baseline(root, n, seed, reps=3):
    """Estimator ceiling for this sample count - what a perfect source scores.

    Averaged over several draws: independent realisations of the same size differ
    by a few percent, so a single draw would make the %ceil column look more
    precise than it is.
    """
    path = root / "baseline_1bit.bin"
    print(f"\n>>> baseline: random data, {n} samples of 1 bit, "
          f"{max(1, reps)} draw(s)")
    vals = []
    for i in range(max(1, reps)):
        rng = np.random.default_rng(seed + i)
        path.write_bytes(rng.integers(0, 2, size=n, dtype=np.uint8).tobytes())
        h, _ = run_ea(path, 1)
        if h is not None:
            vals.append(h)
            print(f"    draw {i + 1}: H = {h:.4f}")
    path.unlink(missing_ok=True)
    if not vals:
        return None, None
    mean = float(np.mean(vals))
    spread = float(np.ptp(vals)) if len(vals) > 1 else 0.0
    print(f"    ceiling H = {mean:.4f} bit/bit"
          + (f" +/- {spread / 2:.4f} over {len(vals)} draws" if spread else "")
          + " (a perfect source cannot score more at this sample count)")
    return mean, spread


# ── report ──────────────────────────────────────────────────────────────────
# Bytes the firmware discards per dropped unit, for turning DROP counts into a
# fraction of the stream: a USB drop loses one batch, a collector drop one buffer.
BATCH_BYTES = 5 * DIGEST_BYTES     # ENTROPY_BATCH_SIZE * TC_SHA256_DIGEST_SIZE
RAW_BUFFER_BYTES = DIGEST_BYTES    # buffer_bytes_target in RAW mode

# Below this share of the stream the gaps are too rare to shift the estimators.
# Measured basis: at 0.004% the figures sit on the trend, at 0.65% they already
# leave it, and by 14% they are inflated to the level of a rate 4x lower.
DROP_TOLERANCE = 0.0005            # 0.05% of the stream


def lost_fraction(rec, nbytes):
    """Share of the stream the firmware threw away during the capture."""
    usb = rec.get("drop_usb") or 0
    col = rec.get("drop_col") or 0
    if usb < 0 or col < 0:
        return None
    lost = usb * BATCH_BYTES + col * RAW_BUFFER_BYTES
    return lost / (nbytes + lost) if (nbytes + lost) else 0.0


def required_multiplicity(sum_h):
    """Compression needed so a 256-bit block carries MARGIN x its length."""
    if not sum_h or sum_h <= 0:
        return None
    need = MARGIN * SHA_BLOCK_BITS / (DIGEST_BYTES * sum_h)
    return math.ceil(need)


def achieved_margin(sum_h, m):
    """Entropy bits per output bit actually delivered at compression m."""
    if not sum_h or not m:
        return None
    return m * DIGEST_BYTES * sum_h / SHA_BLOCK_BITS


def report(records, ceiling, order, root, ceiling_spread=None):
    rows = [r for r in records if r and r.get("sum_h")]
    if not rows:
        print("\nNo complete results.")
        return
    rows.sort(key=lambda r: r["fs"])

    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("\n" + "=" * 96)
    out("SWEEP RESULT")
    out("=" * 96)
    out(f"visit order (to decorrelate warm-up drift): "
        f"{', '.join(str(i) for i in order)}")
    if ceiling:
        pm = (f" +/- {ceiling_spread / 2:.4f}" if ceiling_spread else "")
        out(f"estimator ceiling at this sample count: {ceiling:.4f}{pm} bit/bit "
            f"per channel, {NUM_CHANNELS * ceiling:.4f} summed")
        if ceiling_spread:
            out(f"  (so the %ceil column carries about "
                f"{100 * ceiling_spread / 2 / ceiling:.1f}% uncertainty of its own)")
    out()
    out(f"{'f_s':>7} {'sum H':>8} {'%ceil':>7} {'H/s':>9} {'m':>3} "
        f"{'out':>8} {'margin':>7} {'worst|r|':>9} {'lost':>8} {'valid':>6}")
    out(f"{'[kHz]':>7} {'[b/smp]':>8} {'':>7} {'[kbit/s]':>9} {'':>3} "
        f"{'[kB/s]':>8} {'':>7} {'':>9} {'[% strm]':>8}")
    out("-" * 96)
    for r in rows:
        m = required_multiplicity(r["sum_h"])
        rate_bits = r["sum_h"] * r["fs"]
        lost = lost_fraction(r, r.get("bytes") or 0)
        reachable = m is not None and m <= MAX_MULTIPLICITY
        r["m_2x"], r["entropy_rate"], r["lost_fraction"] = m, rate_bits, lost
        r["valid"] = reachable and (lost is not None) and (lost <= DROP_TOLERANCE)
        r["output_bps"] = (r["fs"] / m) if reachable else None
        r["margin"] = achieved_margin(r["sum_h"], m)
        pct = 100 * r["sum_h"] / (NUM_CHANNELS * ceiling) if ceiling else float("nan")
        out(f"{r['fs'] / 1000:>7.0f} {r['sum_h']:>8.4f} {pct:>6.1f}% "
            f"{rate_bits / 1000:>9.1f} "
            f"{(str(m) if reachable else '>9'):>3} "
            f"{(f'{r["output_bps"] / 1000:.1f}' if reachable else '-'):>8} "
            f"{(f'{r["margin"]:.2f}x' if r['margin'] else '-'):>7} "
            f"{r['worst_r']:>9.5f} "
            f"{(f'{100 * lost:.3f}' if lost is not None else '?'):>8} "
            f"{('yes' if r['valid'] else 'NO'):>6}")
    out("-" * 96)
    out("sum H  = summed per-channel SP 800-90B min-entropy, the conservative budget")
    out("m      = compression needed for the 2x margin; >9 exceeds the firmware max")
    out("out    = output rate at that compression;  margin = what it actually delivers")
    out("lost   = share of the stream the firmware discarded (DROP usb x 160 B +")
    out(f"         DROP col x 32 B); over {100 * DROP_TOLERANCE:g}% the row is not usable")
    out()
    out("READ THE 'lost' COLUMN BEFORE ANYTHING ELSE. Gaps in the stream decorrelate")
    out("consecutive samples, so the estimators see less structure and sum H comes out")
    out("TOO HIGH - not merely unreliable, but inflated in a known direction, roughly")
    out("in proportion to what was lost. A high rate with heavy drops can therefore")
    out("look as good as a rate several times lower. Those rows are excluded below.")

    good = [r for r in rows if r["valid"]]
    if not good:
        out("\nNo rate produced a trustworthy capture within the compression limit.")
    else:
        # Throughput first, but a tie on output is broken by the achieved margin:
        # the compression step is an integer, so two rates often deliver the same
        # bytes per second while one of them sits much closer to the 2x cliff.
        best = max(good, key=lambda r: (round(r["output_bps"], 3), r["margin"]))
        near = [r for r in good
                if r["output_bps"] >= 0.98 * best["output_bps"] and r is not best]
        out(f"\nRECOMMENDED OPERATING POINT: {best['fs'] / 1000:g} kHz "
            f"(table index {best['index']}, command '!t{best['index']}')")
        out(f"    budget      {best['sum_h']:.4f} bit per 8-bit sample "
            f"({best['entropy_rate'] / 1000:.1f} kbit/s)")
        out(f"    compression {best['m_2x']}:1 for the 2x margin  "
            f"-> command '!{best['m_2x']}'")
        out(f"    output      {best['output_bps'] / 1000:.1f} kB/s")
        cur = next((r for r in rows if r["fs"] == 100_000), None)
        if cur and cur.get("output_bps") and cur is not best:
            out(f"    versus 100 kHz: {best['output_bps'] / cur['output_bps']:.2f}x "
                f"the output ({cur['output_bps'] / 1000:.1f} kB/s at "
                f"{cur['m_2x']}:1)")
        elif cur is best:
            out("    100 kHz is already the best of the rates tested")

        # How much sum H may fall before the compression step has to increase.
        # m is an integer, so a rate can sit a fraction of a percent from needing
        # one more step - which would cut its output by 1/m.
        floor_h = MARGIN * SHA_BLOCK_BITS / (DIGEST_BYTES * best["m_2x"])
        headroom = 100 * (best["sum_h"] - floor_h) / best["sum_h"]
        out(f"\n    robustness: {best['m_2x']}:1 holds the 2x margin as long as "
            f"sum H stays above {floor_h:.4f};")
        out(f"    measured {best['sum_h']:.4f}, so there is {headroom:.1f}% of "
            f"headroom before {best['m_2x'] + 1}:1 would be required")
        if headroom < 5:
            out(f"    ! That is less than the {2.3:.1f}% uncertainty the ceiling "
                f"measurement alone carries.")
            out(f"    ! Treat this point as marginal: another board, or a warmer "
                f"one, may need {best['m_2x'] + 1}:1,")
            out(f"    ! which would give {best['fs'] / (best['m_2x'] + 1) / 1000:.1f} "
                f"kB/s instead. Consider the alternative below.")
        if near:
            out("\n    within 2% of the same output, ranked by margin:")
            for r in sorted(near, key=lambda x: -x["margin"]):
                fh = MARGIN * SHA_BLOCK_BITS / (DIGEST_BYTES * r["m_2x"])
                hr = 100 * (r["sum_h"] - fh) / r["sum_h"]
                out(f"        {r['fs'] / 1000:>4.0f} kHz at {r['m_2x']}:1  -> "
                    f"{r['output_bps'] / 1000:.1f} kB/s, margin {r['margin']:.2f}x, "
                    f"headroom {hr:.1f}%")

    # entropy per sample against rate - the physics question
    out("\nJITTER ACCUMULATION")
    # Only rows without drops: a dropped-block capture reports inflated sum H, so
    # including it would understate the real decline (at 14% loss the top rate
    # scored as high as a rate 4x lower).
    clean = [r for r in rows if r["valid"]] or rows
    if len(clean) < len(rows):
        out(f"    (over the {len(clean)} rates without drops; the rest report "
            f"inflated sum H)")
    lo, hi = clean[0], clean[-1]
    if lo["fs"] != hi["fs"]:
        drop = 100 * (1 - hi["sum_h"] / lo["sum_h"])
        ratio = hi["fs"] / lo["fs"]
        out(f"    from {lo['fs'] / 1000:g} to {hi['fs'] / 1000:g} kHz "
            f"({ratio:.0f}x the rate) the budget per sample changes by "
            f"{-drop:+.1f}%")
        out(f"    ({lo['sum_h']:.4f} -> {hi['sum_h']:.4f} bit per sample)")
        if drop < 5:
            out("    Essentially flat: jitter still saturates the sample at the top")
            out("    rate tested, so the useful range has not been exhausted - the")
            out("    next sweep should go higher, and the limit is USB, not physics.")
        elif drop < 25:
            out("    A mild decline, so the product still rises with rate; the")
            out("    optimum above is a real trade rather than a plateau.")
        else:
            out("    A steep decline - jitter no longer has time to accumulate. The")
            out("    optimum is near the knee and going higher costs more quality")
            out("    than it buys throughput.")

    # does the coupling follow the rate?
    out("\nPAIR COUPLING VERSUS RATE (open question: crosstalk at the latch edge,")
    out("or the rings pulling each other's phase?)")
    out(f"    {'f_s [kHz]':>10} {'B-C':>9} {'E-F':>9} {'G-H':>9}")
    for r in rows:
        nm = r["named_r"]
        out(f"    {r['fs'] / 1000:>10.0f} {nm['B-C']:>+9.4f} {nm['E-F']:>+9.4f} "
            f"{nm['G-H']:>+9.4f}")
    vals = {k: [r["named_r"][k] for r in rows] for k in ("B-C", "E-F", "G-H")}
    spans = {k: max(v) - min(v) for k, v in vals.items()}
    means = {k: float(np.mean(np.abs(v))) for k, v in vals.items()}
    worst_span = max(spans.values())
    typical = float(np.mean(list(means.values())))
    out(f"    spread across rates: " +
        "  ".join(f"{k}={spans[k]:.4f}" for k in spans) +
        f"   (typical |r| = {typical:.4f})")
    if typical > 0 and worst_span < 0.2 * typical:
        out("    Flat within 20% of its own size: the coupling does not follow the")
        out("    sampling rate, which fits crosstalk at the instant of latching and")
        out("    argues against the rings pulling each other's phase.")
    elif typical > 0 and worst_span > 0.5 * typical:
        out("    Varies strongly with rate: that fits phase pulling between rings")
        out("    rather than a single-event crosstalk at the latch, and points the")
        out("    Rev-3 fix at the oscillators and their supply, not the latch.")
    else:
        out("    Partly rate dependent - inconclusive, both mechanisms may be")
        out("    present. More rates or longer captures would separate them.")

    out("\nCaveat: the budget is the sum of per-channel estimates, which assumes")
    out("independence. Coupling costs 0.4% of it and was measured not to reduce")
    out("joint entropy, so the sum stands - but it is an assumption, not a")
    out("measurement, and these figures are one board at room temperature.")

    path = root / "sweep_summary.txt"
    path.write_text("\n".join(lines) + "\n")
    (root / "sweep_summary.json").write_text(json.dumps(
        {"order": order, "ceiling": ceiling, "rates": rows}, indent=2))
    print(f"\nwritten: {path}")
    print(f"written: {root / 'sweep_summary.json'}")


def main(argv=None):
    args = parse_args(argv)
    tool_dir = pathlib.Path(__file__).resolve().parent
    root = pathlib.Path(args.outdir)
    root.mkdir(parents=True, exist_ok=True)

    try:
        indices = [int(x) for x in args.rates.split(",") if x.strip() != ""]
    except ValueError:
        sys.exit("--rates must be comma separated integers")
    bad = [i for i in indices if i not in RATE_TABLE]
    if bad:
        sys.exit(f"unknown rate indices {bad}; valid are {sorted(RATE_TABLE)}")

    order = list(indices)
    if args.order == "asc":
        order.sort()
    elif args.order == "desc":
        order.sort(reverse=True)
    else:
        np.random.default_rng(args.seed).shuffle(order)

    do_capture = not args.analyse_only
    do_analyse = not args.capture_only
    if do_analyse and not shutil.which("ea_non_iid"):
        sys.exit("ea_non_iid not found in PATH")

    dirs = {i: root / f"{RATE_TABLE[i] // 1000}kHz" for i in indices}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    print(f"rates      : " + ", ".join(
        f"{RATE_TABLE[i] / 1000:g}k" for i in sorted(indices)))
    print(f"visit order: " + ", ".join(str(i) for i in order) +
          (f"  (shuffled, seed {args.seed})" if args.order == "shuffled" else ""))
    print(f"bytes/rate : {args.bytes} ({args.bytes / 1e6:.1f} M samples per channel)")
    print(f"outdir     : {root}")
    if do_capture:
        total = sum(args.bytes / RATE_TABLE[i] for i in indices)
        print(f"capture    : ~{total / 60:.0f} min of sampling in total")

    caps = {}
    if do_capture:
        for i in order:
            info = capture_rate(i, dirs[i], args.port, args.bytes, tool_dir)
            if info is None:
                print("    aborting the sweep")
                return 1
            caps[i] = info
            (dirs[i] / "capture.json").write_text(json.dumps(info, indent=2))
        if not do_analyse:
            print("\ncaptures done; re-run with --analyse-only to assess them")
            return 0

    records, ceiling, ceiling_spread = [], None, None

    if args.report_only:
        for i in sorted(indices):
            f = dirs[i] / "summary.json"
            if not f.is_file():
                print(f"missing {f}")
                continue
            rec = json.loads(f.read_text())
            cap = dirs[i] / "capture.json"
            if cap.is_file():
                c = json.loads(cap.read_text())
                for k in ("drop_usb", "drop_col", "health", "seconds",
                          "timestamp", "bytes"):
                    rec.setdefault(k, c.get(k))
            records.append(rec)
        if records:
            n = records[0].get("n_samples")
            ceiling, ceiling_spread = baseline(root, n, args.seed,
                                               args.baseline_reps)
        report(records, ceiling, order, root, ceiling_spread)
        return 0

    for i in sorted(indices):
        info = caps.get(i)
        if info is None:
            f = dirs[i] / "capture.json"
            if f.is_file():
                info = json.loads(f.read_text())
        rec = analyse_rate(i, dirs[i], args.joint, args.skip_subset,
                           args.keep_unpacked, tool_dir, info)
        if rec:
            records.append(rec)
            if ceiling is None:
                ceiling, ceiling_spread = baseline(
                    root, rec["n_samples"], args.seed, args.baseline_reps)

    report(records, ceiling, order, root, ceiling_spread)
    return 0


if __name__ == "__main__":
    sys.exit(main())
