"""Capture a raw byte stream from the RNG device over USB serial (CDC-ACM).

This is the data-acquisition tool: it opens the device's virtual serial port,
optionally sends an init sequence, then reads bytes until a fixed target size is
collected and writes them verbatim to a binary file for later offline analysis.
While running it prints a live progress line with the amount captured, the
instantaneous throughput and an ETA.

The captured file is the raw, bit-interleaved stream that
``check_streams_corelation.py`` later splits into the individual sub-streams.

If ``--sequence`` is given, each character is written on its own (one write per
character) with a delay in between; only once the whole sequence has been sent
does the capture loop start. Stop early at any time with Ctrl+C.
"""

import argparse
import time

import serial

# Default device serial port (Linux CDC-ACM node); override with --port.
DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_OUTPUT = "output_mode_RAW_6x4.bin"
DEFAULT_BAUD = 115200
DEFAULT_TARGET_BYTES = 1 * 1024 * 1024  # how many bytes to capture in total

BLOCK_SIZE = 64 * 1024  # read in large chunks (64 KB) for throughput


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Capture a raw byte stream from the RNG device over USB serial.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--port", default=DEFAULT_PORT,
        help="device serial port",
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT,
        help="output file for the raw stream",
    )
    parser.add_argument(
        "-s", "--sequence", default="",
        help="characters to send one-by-one before capturing (e.g. 'abcDEFr')",
    )
    parser.add_argument(
        "--seq-delay", type=float, default=0.2,
        help="delay in seconds between characters of the init sequence",
    )
    parser.add_argument(
        "-b", "--baud", type=int, default=DEFAULT_BAUD,
        help="baud rate",
    )
    parser.add_argument(
        "-n", "--target-bytes", type=int, default=DEFAULT_TARGET_BYTES,
        help="number of bytes to capture in total",
    )
    return parser.parse_args(argv)


def format_size(num_bytes):
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1000.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1000.0
    return f"{num_bytes:.2f} PB"


def format_time(seconds):
    if seconds is None or seconds != seconds or seconds == float("inf"):
        return "--:--:--"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def send_sequence(ser, sequence, delay):
    """Send each character on its own write, waiting `delay` seconds between them."""
    print(f"Sending init sequence: {sequence!r}")
    for ch in sequence:
        ser.write(ch.encode("ascii"))
        ser.flush()
        print(f"  sent {ch!r}")
        time.sleep(delay)


def main(argv=None):
    args = parse_args(argv)

    total_bytes = 0
    bytes_since_last_update = 0
    current_speed_str = "0.00 B/s"

    try:
        with serial.Serial(args.port, args.baud, timeout=1) as ser, \
                open(args.output, "wb") as bin_file:
            print(f"Opened port: {args.port}")
            print(f"Writing to file: {args.output}")
            print(f"Target to collect: {format_size(args.target_bytes)}")

            # Flush any stale bytes before starting.
            ser.reset_input_buffer()

            # Send the optional init sequence, then flush whatever it triggered
            # so the capture starts on a clean input buffer.
            if args.sequence:
                send_sequence(ser, args.sequence, args.seq_delay)
                ser.reset_input_buffer()

            start_time = time.time()
            last_update_time = start_time

            while total_bytes < args.target_bytes:
                chunk_to_read = min(BLOCK_SIZE, args.target_bytes - total_bytes)
                data = ser.read(chunk_to_read)

                if data:
                    bin_file.write(data)
                    data_len = len(data)
                    total_bytes += data_len
                    bytes_since_last_update += data_len

                current_time = time.time()
                time_diff = current_time - last_update_time

                # Refresh the speed figures and the on-screen line about every 0.2 s.
                if time_diff >= 0.2:
                    # Instantaneous speed (bytes/s) over the last time window.
                    speed_bytes_per_sec = bytes_since_last_update / time_diff
                    current_speed_str = f"{format_size(speed_bytes_per_sec)}/s"

                    # Reset the per-window counters.
                    bytes_since_last_update = 0
                    last_update_time = current_time

                    # ETA from the average speed since the start (more stable).
                    elapsed = current_time - start_time
                    avg_speed_bytes_per_sec = total_bytes / elapsed if elapsed > 0 else 0
                    bytes_left = args.target_bytes - total_bytes
                    if avg_speed_bytes_per_sec > 0:
                        eta_seconds = bytes_left / avg_speed_bytes_per_sec
                    else:
                        eta_seconds = float("inf")

                    # Print current progress together with speed and ETA.
                    print(
                        f"\rCollected: {format_size(total_bytes)} / {format_size(args.target_bytes)} "
                        f"[{current_speed_str}] "
                        f"ETA: {format_time(eta_seconds)}",
                        end="",
                        flush=True
                    )

            # Summary once the capture is finished.
            total_time = time.time() - start_time
            avg_speed = total_bytes / total_time if total_time > 0 else 0

            print(f"\n\nDone!")
            print(f"Total collected:  {format_size(total_bytes)}")
            print(f"Duration:         {total_time:.2f} s")
            print(f"Average speed:    {format_size(avg_speed)}/s")

    except serial.SerialException as e:
        print(f"\nError: {e}.")
    except KeyboardInterrupt:
        print(f"\nStopped. Total captured: {format_size(total_bytes)}")


if __name__ == "__main__":
    main()