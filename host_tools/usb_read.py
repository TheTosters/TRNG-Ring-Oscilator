"""Capture a raw byte stream from the RNG device over USB serial (CDC-ACM).

This is the data-acquisition tool: it opens the device's virtual serial port,
reads bytes until a fixed target size is collected, and writes them verbatim to
a binary file for later offline analysis. While running it prints a live
progress line with the amount captured, the instantaneous throughput and an ETA.

The captured file (``bin_filename``) is the raw, bit-interleaved stream that
``check_streams_corelation.py`` later splits into the individual sub-streams.

Adjust ``port_name``, ``baud_rate``, ``bin_filename`` and ``TARGET_BYTES`` below
to match your setup. Stop early at any time with Ctrl+C.
"""

import time
import serial

port_name = "/dev/ttyACM0"      # device serial port (Linux CDC-ACM node)
baud_rate = 115200
bin_filename = "output_mode_RAW_6x4.bin"   # output file for the raw stream

# How many bytes to capture in total (100 MB here).
TARGET_BYTES = 100 * 1024 * 1024
BLOCK_SIZE = 64 * 1024  # read in large chunks (64 KB) for throughput


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


total_bytes = 0

# Variables used to measure throughput.
start_time = 0
last_update_time = 0
bytes_since_last_update = 0
current_speed_str = "0.00 B/s"

try:
    with serial.Serial(port_name, baud_rate, timeout=1) as ser, open(bin_filename, "wb") as bin_file:
        print(f"Opened port: {port_name}")
        print(f"Writing to file: {bin_filename}")
        print(f"Target to collect: {format_size(TARGET_BYTES)}")

        # Flush any stale bytes before starting (optionally send a start command).
        ser.reset_input_buffer()
        #ser.write(b"A")

        start_time = time.time()
        last_update_time = start_time

        while total_bytes < TARGET_BYTES:
            chunk_to_read = min(BLOCK_SIZE, TARGET_BYTES - total_bytes)
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
                bytes_left = TARGET_BYTES - total_bytes
                if avg_speed_bytes_per_sec > 0:
                    eta_seconds = bytes_left / avg_speed_bytes_per_sec
                else:
                    eta_seconds = float("inf")

                # Print current progress together with speed and ETA.
                print(
                    f"\rPobrano: {format_size(total_bytes)} / {format_size(TARGET_BYTES)} "
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
