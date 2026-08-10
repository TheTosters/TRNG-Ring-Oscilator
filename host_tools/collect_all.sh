#!/usr/bin/env bash
#
# Capture and/or assess the raw TRNG output with the NIST SP 800-90B tool.
#
#   ./collect_all.sh capture  <dir>   only capture from the device into <dir>
#   ./collect_all.sh analyse  <dir>   only re-run the assessment on <dir>
#   ./collect_all.sh both     <dir>   capture, then analyse (default)
#
# The two phases are separate on purpose: capture needs the device connected,
# the assessment does not, so it can be repeated offline on data already taken.
#
# IMPORTANT - bit order. The firmware packs bits LSB-first: bit 0 of byte 0 is
# the first sample collected, and within a 6-channel sample channel A sits on
# bit 0. unpack_single_channel.py defaults to MSB-first, so every call below
# must pass --bit-order little. Getting this wrong silently reorders the samples
# in time, which is exactly what the serial-dependence estimators (t-Tuple, LRS,
# Lag) measure - so the resulting entropy figures would be meaningless.

set -euo pipefail

MODE="${1:-both}"
OUTDIR="${2:-.}"
BIT_ORDER="little"
CHANNELS="A B C D E F"

mkdir -p "$OUTDIR"

# Command sequences: enable exactly one channel, disable the rest, RAW mode on.
# Every command byte needs the '!' prefix, see COMMAND_PREFIX in entropy_collector.h
declare -A ENABLE=(
	[A]="!a!B!C!D!E!F!r" [B]="!A!b!C!D!E!F!r" [C]="!A!B!c!D!E!F!r"
	[D]="!A!B!C!d!E!F!r" [E]="!A!B!C!D!e!F!r" [F]="!A!B!C!D!E!f!r"
)

capture() {
	for ch in $CHANNELS; do
		echo ">>> capture channel $ch"
		python3 usb_read.py -s "${ENABLE[$ch]}" -o "$OUTDIR/channel$ch.bin"
	done
	echo ">>> capture all six channels"
	python3 usb_read.py -s '!a!b!c!d!e!f!r' -o "$OUTDIR/channelA-F.bin"
}

analyse() {
	local results="$OUTDIR/results.txt"
	: > "$results"

	for ch in $CHANNELS; do
		echo ">>> analyse channel $ch"
		python3 unpack_single_channel.py \
			"$OUTDIR/channel$ch.bin" "$OUTDIR/output$ch.bin" \
			--bits 1 --bit-order "$BIT_ORDER"
		{
			echo "########## channel $ch (1 bit/sample, $BIT_ORDER) ##########"
			ea_non_iid -v -i "$OUTDIR/output$ch.bin" 1
			echo
		} >> "$results"
	done

	echo ">>> analyse channels A-F interleaved"
	python3 unpack_single_channel.py \
		"$OUTDIR/channelA-F.bin" "$OUTDIR/outputA-F.bin" \
		--bits 6 --bit-order "$BIT_ORDER"
	{
		echo "########## channels A-F (6 bit/sample, $BIT_ORDER) ##########"
		ea_non_iid -v -i "$OUTDIR/outputA-F.bin" 6
		echo
	} >> "$results"

	echo
	echo "min-entropy estimates in $results:"
	grep -E '^(##########|H_original|H_bitstring|min\()' "$results"
}

case "$MODE" in
	capture) capture ;;
	analyse) analyse ;;
	both)    capture; analyse ;;
	*) echo "usage: $0 {capture|analyse|both} [dir]" >&2; exit 2 ;;
esac
