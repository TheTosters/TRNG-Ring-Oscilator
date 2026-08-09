"""Unpack a bit-packed stream into one sample per byte.

The input file holds a continuous bit stream of fixed-width samples packed back
to back, 8 bits per byte. SP800-90B tools (``ea_non_iid``) expect one sample per
byte, so this helper expands the stream: every ``--bits`` input bytes become 8
output bytes, each holding one sample value in 0..2**bits-1.

  * ``--bits 1`` (default): one channel's raw bits, output is 0x00/0x01,
    8x the input size.
  * ``--bits 6``: six interleaved channels sampled together (A-F), output is
    0x00..0x3f, 8/6 of the input size.

Bit order within each byte:

  * ``big`` (MSB-first, default): bit 7 is consumed first, bit 0 last, and the
    first bit of a sample is its MSB. This matches
    ``check_streams_corelation.py`` / ``unpack_stream.py``.
  * ``little`` (LSB-first): bit 0 is consumed first, and the first bit of a
    sample is its LSB.

Samples may straddle byte boundaries when ``--bits`` is not a power of two; any
trailing bits that do not form a whole sample are dropped (and reported).

Example::

    python unpack_single_channel.py channelB.bin outputB.bin
    ea_non_iid -i outputB.bin -v 1

    python unpack_single_channel.py channelA-F.bin outputA-F.bin --bits 6
    ea_non_iid -i outputA-F.bin -v 6
"""

import argparse
import sys

CHUNK_SYMBOLS = 1 << 17  # samples processed per iteration (8 per group)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Expand a bit-packed stream into one sample per byte.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_file", help="path to the bit-packed input file")
    parser.add_argument("output_file", help="path to the one-sample-per-byte output file")
    parser.add_argument(
        "-b", "--bits", type=int, default=1,
        help="sample width in bits, 1-8 (1 = single channel, 6 = channels A-F)",
    )
    parser.add_argument(
        "--bit-order", choices=("big", "little"), default="big",
        help="'big' = MSB-first (bit 7 consumed first), 'little' = LSB-first (bit 0 first)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.bits <= 8:
        parser.error("--bits must be between 1 and 8")
    return args


def unpack_group(group, bits, bit_order):
    """Expand ``bits`` bytes (exactly 8 samples) into 8 one-sample bytes."""
    mask = (1 << bits) - 1
    value = int.from_bytes(group, bit_order)
    if bit_order == "big":
        return bytes((value >> (bits * (7 - i))) & mask for i in range(8))
    return bytes((value >> (bits * i)) & mask for i in range(8))


def unpack_tail(tail, bits, bit_order):
    """Expand a short trailing block, dropping bits that do not fill a sample."""
    mask = (1 << bits) - 1
    total_bits = len(tail) * 8
    count = total_bits // bits
    value = int.from_bytes(tail, bit_order)
    if bit_order == "big":
        return bytes((value >> (total_bits - bits * (i + 1))) & mask for i in range(count))
    return bytes((value >> (bits * i)) & mask for i in range(count))


def main(argv=None):
    args = parse_args(argv)
    bits, bit_order = args.bits, args.bit_order
    chunk_size = bits * (CHUNK_SYMBOLS // 8)  # whole groups only, no straddling

    read_bytes = written = 0
    with open(args.input_file, "rb") as src, open(args.output_file, "wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            read_bytes += len(chunk)
            groups = [chunk[i:i + bits] for i in range(0, len(chunk) - len(chunk) % bits, bits)]
            out = b"".join(unpack_group(g, bits, bit_order) for g in groups)
            tail = chunk[len(groups) * bits:]  # only non-empty on the final chunk
            if tail:
                out += unpack_tail(tail, bits, bit_order)
            dst.write(out)
            written += len(out)

    dropped = read_bytes * 8 - written * bits
    print(
        f"Unpacked {args.input_file} ({bit_order}-endian, {bits} bit/sample): "
        f"{read_bytes} bytes -> {written} samples in {args.output_file}"
        + (f" ({dropped} trailing bits dropped)" if dropped else ""),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
