"""Extract a single channel (bit position) from a bit-packed stream.

The raw stream interleaves up to 8 sources bit by bit, packed 8 bits per byte.
This helper picks one channel (1-8) and writes an output file where every byte
is the 0/1 value of that channel's bit, one byte per input bit position.

Bit order within each byte selects which physical bit is "channel 1":

  * ``big`` (MSB-first, default): channel 1 = bit 7, channel 8 = bit 0.
    This matches ``check_streams_corelation.py`` (BIT_ORDER = 'big').
  * ``little`` (LSB-first): channel 1 = bit 0, channel 8 = bit 7.

Example: ``--channel 3`` writes the 3rd bit of every byte (in the chosen bit
order) as a sequence of 0x00/0x01 bytes.
"""

import argparse
import sys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Extract one channel (bit) from a bit-packed stream into 0/1 bytes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_file", help="path to the bit-packed input file")
    parser.add_argument("output_file", help="path to the 0/1-per-byte output file")
    parser.add_argument(
        "-c", "--channel", type=int, required=True,
        help="channel to extract, 1-8",
    )
    parser.add_argument(
        "--bit-order", choices=("big", "little"), default="big",
        help="'big' = MSB-first (channel 1 = bit 7), 'little' = LSB-first (channel 1 = bit 0)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.channel <= 8:
        parser.error("--channel must be between 1 and 8")
    return args


def channel_to_shift(channel, bit_order):
    """Map a 1-based channel to the right-shift needed to isolate its bit."""
    if bit_order == "big":  # channel 1 -> bit 7 (MSB), channel 8 -> bit 0
        return 8 - channel
    return channel - 1      # little: channel 1 -> bit 0 (LSB)


def main(argv=None):
    args = parse_args(argv)
    shift = channel_to_shift(args.channel, args.bit_order)

    with open(args.input_file, "rb") as f:
        data = f.read()

    with open(args.output_file, "wb") as f:
        f.write(bytes((byte >> shift) & 1 for byte in data))

    print(
        f"Extracted channel {args.channel} ({args.bit_order}-endian) from "
        f"{args.input_file}: {len(data)} bits -> {args.output_file}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()