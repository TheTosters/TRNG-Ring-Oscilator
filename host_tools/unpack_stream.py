"""Expand a bit-packed file into one byte per bit (0x00 or 0x01).

Some tools (e.g. randomness test suites or plotters) expect one bit per byte
rather than 8 bits packed into each byte. This helper reads a packed input file
and writes an output file where every input bit becomes its own byte valued 0
or 1.

Note: bits are unpacked LSB-first (bit 0 of each byte is emitted first). This is
the opposite order from ``check_streams_corelation.py`` (which defaults to
MSB-first); keep that in mind if you compare their outputs.
"""

input_file = "output_mode_RAW_6x4_substream_B_small.bin"
output_file = "substream_B_bits.bin"

with open(input_file, "rb") as f:
    data = f.read()

with open(output_file, "wb") as f:
    for byte in data:
        # Emit each of the 8 bits (LSB-first) as its own 0x00/0x01 byte.
        for i in range(8):
            bit = (byte >> i) & 1
            f.write(bytes([bit]))