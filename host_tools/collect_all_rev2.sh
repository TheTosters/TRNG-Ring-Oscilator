#!/usr/bin/env bash
#
# Rev-2: capture and/or assess the raw TRNG output with the NIST SP 800-90B tool.
# For the Rev-1 board (6 rings, one latch) use collect_all_rev1.sh.
#
#   ./collect_all_rev2.sh capture  <dir>   only capture from the device into <dir>
#   ./collect_all_rev2.sh analyse  <dir>   only re-run the assessment on <dir>
#   ./collect_all_rev2.sh both     <dir>   capture, then analyse (default)
#
# The two phases are separate on purpose: capture needs the device connected,
# the assessment does not, so it can be repeated offline on data already taken.
#
# IMPORTANT - bit order. The firmware packs bits LSB-first: bit 0 of byte 0 is
# the first sample collected, and within a sample ring 1 sits on bit 0.
# unpack_single_channel.py defaults to MSB-first, so every call below must pass
# --bit-order little. Getting this wrong silently reorders the samples in time,
# which is exactly what the serial-dependence estimators (t-Tuple, LRS, Lag)
# measure - so the resulting entropy figures would be meaningless. With all
# eight rings enabled a sample is exactly one byte, so bit order no longer
# affects the combined capture, but it still matters for the 4-ring group
# captures and for the per-channel ones.
#
# Rev-2 hardware map (from RO_hardware.kicad_sch; note that the latch outputs
# cross over, so the letter order is not the schematic sheet order):
#
#   letter  ring  pin   latch          ring sheet          topology
#   a       1     PA8   B (PA1, U22)   T-OscilatorRing     SN74LVC3G04, 3 gates in one package
#   b       2     PA0   B              T-OscilatorRing2    ''
#   c       3     PA2   B              T-OscilatorRing3    ''
#   d       4     PA3   B              T-OscilatorRing1    ''
#   e       5     PA4   A (PB0, U3)    OscilatorRing1      3x SN74LVC1G04, one gate per package
#   f       6     PA5   A              OscilatorRing2      ''
#   g       7     PA6   A              OscilatorRing4      ''
#   h       8     PA7   A              OscilatorRing3      ''
#
# The two groups are different ring topologies behind different latches, so
# besides the per-channel and all-eight captures this script also takes one
# capture per group. Those are the only ones that can answer "is the coupling
# inside a group worse than across groups", because coupling needs simultaneous
# samples and the per-channel captures are taken at different times.

set -euo pipefail

MODE="${1:-both}"
OUTDIR="${2:-.}"
BIT_ORDER="little"
CHANNELS="A B C D E F G H"

# Ring groups by latch / topology, see the map above.
GROUP_T="A-D"          # rings 1-4, latch B, SN74LVC3G04
GROUP_PLAIN="E-H"      # rings 5-8, latch A, SN74LVC1G04

mkdir -p "$OUTDIR"

# Command sequences: enable exactly one channel, disable the rest, RAW mode on.
# Every command byte needs the '!' prefix, see COMMAND_PREFIX in entropy_collector.h
declare -A ENABLE=(
	[A]="!a!B!C!D!E!F!G!H!r" [B]="!A!b!C!D!E!F!G!H!r"
	[C]="!A!B!c!D!E!F!G!H!r" [D]="!A!B!C!d!E!F!G!H!r"
	[E]="!A!B!C!D!e!F!G!H!r" [F]="!A!B!C!D!E!f!G!H!r"
	[G]="!A!B!C!D!E!F!g!H!r" [H]="!A!B!C!D!E!F!G!h!r"
)

capture() {
	for ch in $CHANNELS; do
		echo ">>> capture channel $ch"
		python3 usb_read.py -s "${ENABLE[$ch]}" -o "$OUTDIR/channel$ch.bin"
	done
	echo ">>> capture all eight channels"
	python3 usb_read.py -s '!a!b!c!d!e!f!g!h!r' -o "$OUTDIR/channelA-H.bin"
	echo ">>> capture group $GROUP_T (rings 1-4, latch B)"
	python3 usb_read.py -s '!a!b!c!d!E!F!G!H!r' -o "$OUTDIR/channel$GROUP_T.bin"
	echo ">>> capture group $GROUP_PLAIN (rings 5-8, latch A)"
	python3 usb_read.py -s '!A!B!C!D!e!f!g!h!r' -o "$OUTDIR/channel$GROUP_PLAIN.bin"
}

# assess <file-stem> <bits> <human label>
assess() {
	local stem="$1" bits="$2" label="$3"
	python3 unpack_single_channel.py \
		"$OUTDIR/channel$stem.bin" "$OUTDIR/output$stem.bin" \
		--bits "$bits" --bit-order "$BIT_ORDER"
	{
		echo "########## $label ($bits bit/sample, $BIT_ORDER) ##########"
		ea_non_iid -v -i "$OUTDIR/output$stem.bin" "$bits"
		echo
	} >> "$OUTDIR/results.txt"
}

analyse() {
	local results="$OUTDIR/results.txt"
	: > "$results"

	for ch in $CHANNELS; do
		echo ">>> analyse channel $ch"
		assess "$ch" 1 "channel $ch"
	done

	# Group assessments. Read these as a sanity check, not as the entropy budget:
	# interleaving several sources into one stream does not match the SP 800-90B
	# single-source model and widens the alphabet, which disables several
	# estimators - see section 5 of Rev-1/entopy_tests/summary.md.
	echo ">>> analyse group $GROUP_T interleaved (rings 1-4, latch B)"
	assess "$GROUP_T" 4 "channels $GROUP_T"
	echo ">>> analyse group $GROUP_PLAIN interleaved (rings 5-8, latch A)"
	assess "$GROUP_PLAIN" 4 "channels $GROUP_PLAIN"
	echo ">>> analyse channels A-H interleaved"
	assess "A-H" 8 "channels A-H"

	echo
	echo "min-entropy estimates in $results:"
	grep -E '^(##########|H_original|H_bitstring|min\()' "$results"
	echo
	echo "next: python3 channel_crosstalk_rev2.py $OUTDIR/channelA-H.bin --entropy-file $results"
}

case "$MODE" in
	capture) capture ;;
	analyse) analyse ;;
	both)    capture; analyse ;;
	*) echo "usage: $0 {capture|analyse|both} [dir]" >&2; exit 2 ;;
esac
