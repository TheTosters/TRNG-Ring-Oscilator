#!/usr/bin/env python3
"""Interactive raw-byte console for the TRNG device.

Every key press is sent as exactly one byte (the device protocol has no
terminator and no framing). Incoming data is discarded except for the '?' status
report, which is extracted and printed - otherwise the entropy stream would bury
it in binary noise.

The point of this tool over a modem terminal like minicom: it sends nothing on
its own. No modem init string, no reset string on exit, no echo, no CR/LF
translation. That matters because the firmware parses *every* received byte as a
command, so a stray 's' writes flash, a stray digit changes the compression and
a stray 'A'-'F' (Rev-1) or 'A'-'H' (Rev-2) silently drops a ring oscillator
channel.

Quit with Ctrl-D.
"""

import argparse
import select
import sys
import termios
import tty

import serial

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 115200          # ignored by CDC-ACM, kept for pyserial's sake
COMMAND_PREFIX = b"!"          # firmware ignores any command byte without it

REPORT_START = b"CH="          # first field of the '?' report
REPORT_LAST_FIELD = b"CNT="    # last field, so we know the report is complete
REPORT_END = b"\r\n"
TAIL_KEEP = 256                # enough to hold a report split across two reads


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Raw byte console for the TRNG device.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-p", "--port", default=DEFAULT_PORT, help="serial port")
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="do not show the running received-byte counter",
    )
    return parser.parse_args(argv)


def extract_report(buf):
    """Pull one complete status report out of buf.

    Returns (report_or_None, remaining_buffer).
    """
    start = buf.find(REPORT_START)
    if start < 0:
        return None, buf[-TAIL_KEEP:]
    last = buf.find(REPORT_LAST_FIELD, start)
    if last < 0:
        return None, buf[start:]
    end = buf.find(REPORT_END, last)
    if end < 0:
        return None, buf[start:]
    return buf[start:end], buf[end + len(REPORT_END):]


def write_raw(text):
    """Write to a tty that is in raw mode, so newlines need an explicit CR."""
    sys.stdout.write(text.replace("\n", "\r\n"))
    sys.stdout.flush()


def main(argv=None):
    args = parse_args(argv)

    # pyserial puts the line into raw mode with echo off and sends nothing.
    port = serial.Serial(args.port, DEFAULT_BAUD, timeout=0)

    stdin_fd = sys.stdin.fileno()
    saved_termios = termios.tcgetattr(stdin_fd)

    rx_total = 0
    buf = b""
    rate_pending = False        # last key was 't', so the next digit is its parameter

    print(f"{args.port} open. Keys go out as single raw bytes. Ctrl-D quits.")
    print("Try: '?' for the status report, 'r'/'R' for RAW on/off, '1'-'9' for compression.")
    print(f"Each key is sent as {COMMAND_PREFIX.decode()}<key>; unprefixed bytes are ignored by the device.")
    print("Rev-2 sampling rate: 't' then a digit, e.g. 't' '2' for 150 kHz. The digit")
    print("goes out unprefixed, which is what the firmware expects - so type t, then 2.")
    print("  0=50k  1=100k  2=150k  3=200k  4=250k  5=300k*  6=350k  7=400k  8=500k  9=600k")
    print("  (* firmware default, paired with 6:1 compression - see the rate sweep)")

    try:
        tty.setraw(stdin_fd)
        while True:
            ready, _, _ = select.select([stdin_fd, port.fileno()], [], [], 0.1)

            if stdin_fd in ready:
                key = sys.stdin.buffer.raw.read(1)
                if not key or key == b"\x04":          # EOF or Ctrl-D
                    break
                # The firmware requires the prefix so that echoed entropy cannot
                # act as commands. Added here so a key press stays one key press.
                # Exception: the digit after 't' is a parameter, not a command, and
                # the firmware reads it unprefixed - see CDC_Receive_FS.
                if rate_pending and key.isdigit():
                    port.write(key)
                    rate_pending = False
                else:
                    rate_pending = (key == b"t")
                    port.write(COMMAND_PREFIX + key)

            if port.fileno() in ready:
                chunk = port.read(4096)
                if chunk:
                    rx_total += len(chunk)
                    buf += chunk
                    while True:
                        report, buf = extract_report(buf)
                        if report is None:
                            break
                        write_raw("\n--- report ---\n")
                        write_raw(report.decode("ascii", "replace"))
                        write_raw("\n")

            if not args.quiet:
                write_raw(f"\rrx {rx_total} B   ")
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved_termios)
        port.close()
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
