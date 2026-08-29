/*
 * entropy_collector.c
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */

#include <string.h>
#include "stm32g4xx_hal.h"
#include "main.h"
#include "entropy_collector.h"
#include "flash_storage.h"
#include "tinycrypt/sha256.h"
#include "haptic.h"
#include "sender.h"

#define ACTION_SAVE_CONFIG (0x1)
#define ACTION_REBUILD_LUT (0x2)
#define ACTION_RESET_COLLECTOR (0x4)
#define ACTION_LOAD_CONFIG (0x8)
#define ACTION_SEND_INFO (0x10)
#define ACTION_APPLY_SAMPLING (0x20)

//buildInfoReport() writes without bounds checking, so this has to stay ahead of
//the worst case: 183 bytes with every counter saturated, every field at its
//longest (FS=600000, BUF=288, RATIO=9, UP=600000B/s, four 10 digit counters).
//The headroom is deliberate - adding a field to the report must not silently
//run off the end of the buffer.
#define INFO_TEXT_SIZE (224)

//Bumped whenever the stored record changes meaning or the recommended operating
//point moves: a saved record wins over the defaults below, so without a bump a
//board that had ever run '!s' would come up on the old point after a reflash.
//History: ...0B added the ring-numbered channel mask (Rev-1 used port bits),
//...0C added sampling_index, ...0D moved the default point to 300 kHz at 6:1.
#define MAGIC_COOKIE (0xDABAD00D)

#define MAX_BUFFER_MULTIPLICITY (9)
//Chosen together with DEFAULT_SAMPLING_INDEX, from the rate sweep in
//host_tools/Rev_2/sweep. At 300 kHz the measured budget is 3.1215 bit of
//min-entropy per 8 bit sample (sum of the eight per-channel SP 800-90B
//estimates), so 6 * 32 samples carry 599 bit into every 256 bit block - a 2.34x
//margin, above the 2x usually required to treat the output of a vetted
//conditioning function as full entropy.
//Why 6 and not 5: 5:1 would also clear 2x at 250 kHz and give the same 50 kB/s,
//but only until the budget fell by 1%, where 6:1 tolerates a 14.6% fall (down to
//2.6667 bit/sample) before 7:1 becomes necessary. The sweep's own ceiling
//measurement carries 2.3% uncertainty, so the 1% variant is not defensible.
//For reference: Rev-1 needed 9:1 to reach only 1.13x.
#define DEFAULT_MULTIPLICITY (6)

//SP 800-90B 4.4 health tests, run per channel because the noise source is eight
//independent rings. Cutoffs derived for per-channel min-entropy H = 0.08
//bit/sample and alpha = 2^-30:
//  RCT: C = 1 + ceil(-log2(alpha)/H) = 376
//  APT: W = 1024, C = CRITBINOM(W, 2^-H, 1-alpha) = 1007
//alpha = 2^-30 puts a false alarm at roughly once per 25 days of running, and
//one costs a single discarded buffer out of ~1560 produced per second at the
//default rate.
//DELIBERATELY LOOSE, and worth knowing why. H = 0.08 is the worst Rev-1 channel;
//Rev-2 has since been measured at 0.390 bit/channel at 300 kHz (3.1215 summed
//over eight), which would give RCT C = 78 and a correspondingly tighter APT.
//Tightening them would detect a degrading ring sooner, and there is an argument
//that it should: the 6:1 compression above assumes the 300 kHz budget, and a
//channel that quietly falls to 0.15 bit/sample would pass these cutoffs while
//breaking the 2x margin. Against that, 0.390 comes from one board at room
//temperature, and cutoffs set from it would raise false alarms on a colder or
//slower specimen - each costing a discarded buffer. Left conservative until the
//source is characterised over temperature and more than one unit; see caveat 6
//in Rev-1/entopy_tests/summary.md, which still applies to Rev-2.
#define HEALTH_RCT_CUTOFF (376)
#define HEALTH_APT_WINDOW (1024)
#define HEALTH_APT_CUTOFF (1007)
//"matches > 1007 out of 1024" is the same statement as "non-matches <= 16", and
//counting non-matches lets a channel be written off as healthy as soon as it has
//17 of them - after roughly 40 samples of a 1024 sample window. The remaining 96%
//of the window then costs nothing.
#define HEALTH_APT_SLACK (HEALTH_APT_WINDOW - HEALTH_APT_CUTOFF - 1)

//All eight ring outputs sit on port A, but not on consecutive bits: PA1 in the
//middle is the latch_b output. The lookup table is indexed by the low nine bits
//of GPIOA->IDR, which covers PA0..PA8 in one read; PA1 is simply never read out
//of the index, so its state only duplicates table entries.
#define RO_PORT_MASK (0x1FFu)
#define RO_LUT_SIZE  (RO_PORT_MASK + 1u)

//Every ring may be selected, unlike Rev-1 where two port bits were taken by the
//latch and the LED.
#define VALID_RO_CHANNELS (0xFF)

#define MAX_ENTROPY_BLOCK_SIZE (MAX_BUFFER_MULTIPLICITY * TC_SHA256_DIGEST_SIZE)

#define ENTROPY_BATCH_SIZE (5)
#define ENTROPY_PROCESS_BUFFER_COUNT (3)

//Front panel restart (SW2): how long the stream stays down before resuming. Long
//enough to be unmistakable on the LEDs, short enough not to feel broken.
#define RESTART_PAUSE_MS (3000)

//Self test (SW1): how long it samples, and how long the verdict stays lit.
#define SELF_TEST_MS     (1000)
#define VERDICT_MS       (3000)
//A ring that is stuck outputs a constant level, so it never changes; a healthy
//one changes on most samples. 1% of samples is far below anything a working ring
//produces and far above what a dead one can.
#define SELF_TEST_MIN_CHANGE_PERMILLE (10)
//Bias band. Deliberately wide: this checks for a broken ring, not for entropy
//quality, and the SP 800-90B tools on the host do the latter properly.
#define SELF_TEST_BIAS_MIN_PERCENT (35)
#define SELF_TEST_BIAS_MAX_PERCENT (65)

//Most Common Value monitor. RCT and APT judge each channel on its own, which
//leaves one failure mode wide open: rings that have synchronised - to each other
//or to an injected signal - still change on most samples and still sit at 50%
//ones, so both tests pass while the entropy is gone. That only shows up in the
//distribution of the whole sample, which is what the histogram measures.
//A frequency injection attack (Markettos & Moore, CHES 2009) is exactly this
//failure, and it is the only realistic attack on this design.
//Window is a power of two so log2(N) is exact and the division below is a shift.
#define MCV_WINDOW_SAMPLES (65536u)
#define MCV_WINDOW_LOG2    (16u)
//Alarm below 70% of the theoretical maximum (channel_bits). Measured on this
//board: 7.43 to 7.49 bit out of 8 across 50 kHz to 600 kHz, so the threshold of
//5.6 leaves a wide margin, while eight synchronised rings would read about 1.0.
//Expressed in 1/256 bit, the same scale log2_fixed() returns.
#define MCV_ALARM_NUM (179u)          // 0.70 * 256, per channel bit

//log2(1 + i/32) * 4096, used by log2_fixed(). Linear interpolation between
//entries keeps the error under 0.005 bit, which avoids pulling in libm.
static const uint16_t LOG2_FRAC[33] = {
	    0,   182,   358,   530,   696,   858,  1016,  1169,
	 1319,  1465,  1607,  1746,  1882,  2015,  2145,  2272,
	 2396,  2518,  2637,  2754,  2869,  2982,  3092,  3200,
	 3307,  3412,  3514,  3615,  3715,  3812,  3908,  4003,
	 4096,
};

//One flash per this many collected bytes. At the default 300 kHz that is about
//4.6 Hz, at 50 kHz slightly under 1 Hz - so the LED shows both that the stream
//is alive and roughly how fast it runs.
#define HEARTBEAT_BYTES (65536u)

//Table index for 300 kHz, the operating point the rate sweep settled on
//(host_tools/Rev_2/sweep/sweep_summary.txt). Entropy per sample falls with the
//rate while samples per second rise, and the product - which is what the device
//can actually deliver - peaks here among the rates that could be measured:
//330 kbit/s at 50 kHz, 510 at 100 kHz, 937 at 300 kHz. Above 300 kHz the RAW
//stream outruns CDC, so the source could not be assessed there; those rates
//remain usable in SHA mode but their budget is unknown.
#define DEFAULT_SAMPLING_INDEX (5)

//TIM6 auto-reload values for the 't' command. APB1 is 168 MHz = 2^9 * 3 * 7 * 5^6
//and every entry divides it exactly, so each rate is nominal with no rounding
//error - which matters because the '?' report derives FS from ARR.
//Cost of going up: the sampling ISR takes ~90 cycles, so 600 kHz spends ~32% of
//the CPU there before conditioning, and the raw stream reaches 600 kB/s, well past
//what CDC can carry. An unsustainable rate is not dangerous and does not corrupt
//entropy - it shows up as DROP counts in the '?' report. RAW mode hits the USB
//limit long before SHA mode does.
static const uint16_t sampling_arr[SAMPLING_RATE_COUNT] = {
	3359,	//'0' -  50 kHz
	1679,	//'1' - 100 kHz (default)
	1119,	//'2' - 150 kHz
	 839,	//'3' - 200 kHz
	 671,	//'4' - 250 kHz
	 559,	//'5' - 300 kHz
	 479,	//'6' - 350 kHz
	 419,	//'7' - 400 kHz
	 335,	//'8' - 500 kHz
	 279,	//'9' - 600 kHz
};


//Which processed_entropy_block block is filled now
static uint8_t processed_entropy_block_index  = 0;
//How many TC_SHA256_DIGEST_SIZE blocks are already in block pointed by processed_entropy_block_index
static uint8_t processed_entropy_block_fill_index = 0;
//Entropy blocks which are filled with conditioned entropy
static uint8_t processed_entropy_block[ENTROPY_PROCESS_BUFFER_COUNT][TC_SHA256_DIGEST_SIZE * ENTROPY_BATCH_SIZE];

static uint8_t raw_entropy_block[MAX_ENTROPY_BLOCK_SIZE] = { 0 };
static uint8_t raw_entropy_block2[MAX_ENTROPY_BLOCK_SIZE] = { 0 };
static bool primary_buffer = true;
static uint8_t* entropy_buffer_to_fill = raw_entropy_block;
static uint8_t* volatile buffer_to_process = NULL;

//Bit accumulator state
static uint32_t bit_acc  = 0;
static uint8_t  acc_bits = 0;

//Fill data window
static uint8_t* fill_ptr = raw_entropy_block;
static uint8_t* fill_end = raw_entropy_block + DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE;

static bool pending_raw_entropy = false;
static bool use_raw_entropy = false;

static uint32_t buffer_bytes_target = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE;

//next Update actions
static uint8_t scheduled_action = 0;

//Status report ('?' / '/'): while frozen nothing but the report is sent and the
//sampling timer stays stopped, so the counters below keep their meaning.
static char info_text[INFO_TEXT_SIZE];
static uint16_t info_length = 0;
static bool info_pending = false;
static volatile bool transfer_frozen = false;

//Diagnostics: batches dropped because USB was still busy, and collector blocks
//dropped because the main loop did not pick the previous one up in time.
static uint32_t dropped_usb_batches = 0;
static volatile uint32_t dropped_collector_blocks = 0;

//Diagnostics: what the host actually sent, see noteReceivedByte()
static volatile uint8_t last_rx_byte = 0;
static volatile uint32_t rx_byte_count = 0;

//SP 800-90B health test state, one set of counters per channel. Runs in the main
//loop over a completed buffer, never in the ISR, so it costs the 300 kHz
//sampling path nothing. A buffer that fails is discarded before conditioning, so
//no data derived from a failing source ever reaches USB.
//RCT is evaluated per window rather than per sample: OR every bit change into a
//mask, and at the end of a HEALTH_RCT_CUTOFF long window any channel missing from
//that mask produced no change at all, i.e. a run at least that long. Costs three
//bitwise ops per sample regardless of the data, where a per-channel run counter
//costs ~240 cycles per sample - the rings flip their output in ~78% of samples
//(Markov P_0,1 = 0.80 from ea_non_iid), so there is no sparse case to exploit.
static uint8_t  rct_prev_sample = 0;
static uint8_t  rct_changed_mask = 0;
static uint16_t rct_window_pos = 0;
static uint8_t  apt_reference = 0;
static uint8_t  apt_nonmatch[RO_CHANNEL_COUNT] = {0};
static uint8_t  apt_pending = 0;                    //channels not yet proven healthy
static uint16_t apt_position = HEALTH_APT_WINDOW;   //forces a new window on first sample
static uint32_t health_failures = 0;

//Front panel state. Both the restart pause and the self test suspend the entropy
//stream; USB commands keep working throughout.
static uint32_t restart_resume_at = 0;      //0 = not pausing
static bool self_test_active = false;
static uint32_t self_test_end = 0;
static uint32_t st_ones[RO_CHANNEL_COUNT];
static uint32_t st_changes[RO_CHANNEL_COUNT];
static uint32_t st_samples = 0;
static uint8_t  st_prev_sample = 0;
static bool     st_have_prev = false;
static uint8_t  st_saved_channels = 0;
static bool     st_saved_raw = false;

static uint32_t heartbeat_bytes = 0;

//Sample histogram, shared by the continuous monitor and the self test - they
//never run at the same time, so one kilobyte serves both.
static uint32_t sample_hist[256];
static uint32_t mcv_window_count = 0;
//Last completed window, in 1/256 bit. 0 until the first window closes.
static uint16_t mcv_last = 0;
static uint32_t mcv_failures = 0;

static AppConfig_t configuration = {
		.magic = MAGIC_COOKIE,
		.selected_entropy_buffor_size = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE,
		.selected_ro_channels = VALID_RO_CHANNELS,
		.sampling_index = DEFAULT_SAMPLING_INDEX
};

static uint8_t channels_lut[RO_LUT_SIZE];
static uint8_t channel_bits;

//Command letter order 'a'..'h' maps to ring 1..8, and each ring to the port bit
//its latch output is wired to. Rings 1..4 come from latch B (74HC174 U22, the
//four T rings), rings 5..8 from latch A (U3, the four plain rings) - see the
//RING-IN-x nets in RO_hardware.kicad_sch.
static const uint8_t ro_port_bit[RO_CHANNEL_COUNT] = {
	8,	//ring 1 - PA8 / RING-IN-1 - 'a' - latch B, T-OscilatorRing
	0,	//ring 2 - PA0 / RING-IN-2 - 'b' - latch B, T-OscilatorRing2
	2,	//ring 3 - PA2 / RING-IN-3 - 'c' - latch B, T-OscilatorRing3
	3,	//ring 4 - PA3 / RING-IN-4 - 'd' - latch B, T-OscilatorRing1
	4,	//ring 5 - PA4 / RING-IN-5 - 'e' - latch A, OscilatorRing1
	5,	//ring 6 - PA5 / RING-IN-6 - 'f' - latch A, OscilatorRing2
	6,	//ring 7 - PA6 / RING-IN-7 - 'g' - latch A, OscilatorRing4
	7,	//ring 8 - PA7 / RING-IN-8 - 'h' - latch A, OscilatorRing3
};

static void loadAndValidateConfig(void);

//Packs the enabled ring outputs into the low bits of one byte, in ring order, so
//bit N of a sample is always the (N+1)-th enabled ring. Does the port bit
//gathering as well, which is why the ISR only has to hand over a raw IDR read.
static void rebuildChannelLut(void) {
    const uint8_t mask = configuration.selected_ro_channels;
    for (unsigned v = 0; v < RO_LUT_SIZE; v++) {
        uint8_t res = 0, pos = 0;
        for (unsigned i = 0; i < RO_CHANNEL_COUNT; i++) {
            if (mask & (1u << i)) {
                if (v & (1u << ro_port_bit[i])) res |= (uint8_t)(1u << pos);
                pos++;
            }
        }
        channels_lut[v] = res;
    }
    channel_bits = 0;
    for (unsigned i = 0; i < RO_CHANNEL_COUNT; i++) {
    	if (mask & (1u << i)) {
    		channel_bits++;
    	}
    }
}

//Primary called in IRQ scope!
static void swapInputBuffer() {
	primary_buffer = !primary_buffer;
	entropy_buffer_to_fill = primary_buffer ? raw_entropy_block : raw_entropy_block2;
	fill_ptr = entropy_buffer_to_fill;
	fill_end = entropy_buffer_to_fill + buffer_bytes_target;
}

static void mcvResetWindow(void);
static uint32_t log2_fixed(uint32_t x);

//The health tests carry state across buffers on purpose (an RCT run and an APT
//window are both longer than one buffer), so a restart has to clear it - stale
//counters would otherwise judge the new stream against the old one.
static void resetHealthState(void)
{
    rct_prev_sample = 0;
    rct_changed_mask = 0;
    rct_window_pos = 0;
    apt_reference = 0;
    apt_pending = 0;
    apt_position = HEALTH_APT_WINDOW;   //forces a fresh window on the next sample
    for (uint8_t c = 0; c < RO_CHANNEL_COUNT; c++) {
        apt_nonmatch[c] = 0;
    }
    //Sample width changes with the channel mask, so a window must never span a
    //reconfiguration - the histogram would mix samples of two different widths.
    mcvResetWindow();
}

static void resetCollector(void)
{
    use_raw_entropy = pending_raw_entropy;
    buffer_bytes_target = use_raw_entropy ? TC_SHA256_DIGEST_SIZE
                                          : configuration.selected_entropy_buffor_size;
    bit_acc = 0;
    acc_bits = 0;
    buffer_to_process = NULL;
    processed_entropy_block_fill_index = 0;
    heartbeat_bytes = 0;
    swapInputBuffer();
}

inline void useRawEntropy(bool use_raw) {
	pending_raw_entropy = use_raw;
	scheduled_action |= ACTION_RESET_COLLECTOR;
	if (use_raw) {
		constantGlow();
	} else {
		ledNormal();
	}
}

void useRO(int ringIndex, bool enabled) {
	if (ringIndex >= 0 && ringIndex < RO_CHANNEL_COUNT) {
		uint8_t value = (uint8_t)(1u << ringIndex);
		if (enabled) {
			configuration.selected_ro_channels |= value;
			blinkFast(4);
		} else {
			configuration.selected_ro_channels &= (~value);
			blinkFast(2);
		}
		scheduled_action |= ACTION_RESET_COLLECTOR | ACTION_REBUILD_LUT;
	}
}

//Writes ARR directly rather than going through HAL: AutoReloadPreload is
//disabled so the value takes effect at once, and updateCollector() has the timer
//stopped while actions are applied anyway.
static void applySamplingRate(void) {
	TIM6->PSC = 0;
	TIM6->ARR = sampling_arr[configuration.sampling_index];
}

void setSamplingRate(int index) {
	if (index >= 0 && index < SAMPLING_RATE_COUNT) {
		configuration.sampling_index = (uint8_t)index;
		//Reset the collector too: a half filled buffer straddling a rate change
		//would mix samples taken at two different rates into one block.
		scheduled_action |= ACTION_APPLY_SAMPLING | ACTION_RESET_COLLECTOR;
		blinkFast(index + 1);
	}
}

bool acquisitionSuspended(void) {
	return self_test_active || restart_resume_at != 0;
}

void restartAcquisition(void) {
	if (acquisitionSuspended()) {
		return;                    //already down, leave the running one alone
	}
	restart_resume_at = HAL_GetTick() + RESTART_PAUSE_MS;
	if (restart_resume_at == 0) {
		restart_resume_at = 1;     //0 means "not pausing"
	}
	//Yellow while the stream is down. It also means RAW, but steady-on there
	//versus blinking here keeps the two apart.
	start_blink(LED_MODE, RESTART_PAUSE_MS / (2 * 300), BLINK_MODE_SLOW_OFF);
	ledClear(LED_OK);
	//updateCollector() stops the timer when it applies this and will not restart
	//it until the pause expires.
	scheduled_action |= ACTION_RESET_COLLECTOR;
}

void startSelfTest(void) {
	if (acquisitionSuspended()) {
		return;
	}
	st_saved_channels = configuration.selected_ro_channels;
	st_saved_raw = pending_raw_entropy;

	for (uint8_t c = 0; c < RO_CHANNEL_COUNT; c++) {
		st_ones[c] = 0;
		st_changes[c] = 0;
	}
	st_samples = 0;
	st_have_prev = false;
	self_test_active = true;
	self_test_end = HAL_GetTick() + SELF_TEST_MS;
	//Shared with the continuous monitor; the two never run together.
	mcvResetWindow();

	//Every ring on, so one sample is one byte and every channel gets tested even
	//if the user had some disabled. RAW off keeps buffer_bytes_target at the
	//configured size; the buffers are analysed here, never conditioned or sent.
	configuration.selected_ro_channels = VALID_RO_CHANNELS;
	pending_raw_entropy = false;

	ledClear(LED_OK);
	ledClear(LED_ERROR);
	ledSetSteady(LED_MODE, true);          //steady yellow: test in progress
	scheduled_action |= ACTION_REBUILD_LUT | ACTION_RESET_COLLECTOR;
}

//Counts, per channel, how often it is high and how often it changes. Both are
//needed: a ring stuck high has a perfect count of changes equal to zero, while
//one oscillating at exactly half the sampling rate would look unbiased.
static void analyseSelfTestBuffer(const uint8_t* buffer, uint32_t bytes) {
	uint8_t prev = st_prev_sample;
	bool have_prev = st_have_prev;

	for (uint32_t i = 0; i < bytes; i++) {
		const uint8_t v = buffer[i];
		if (have_prev) {
			const uint8_t diff = (uint8_t)(v ^ prev);
			for (uint8_t c = 0; c < RO_CHANNEL_COUNT; c++) {
				st_changes[c] += (diff >> c) & 1u;
			}
		}
		for (uint8_t c = 0; c < RO_CHANNEL_COUNT; c++) {
			st_ones[c] += (v >> c) & 1u;
		}
		//Same distribution check the continuous monitor does, so the button also
		//catches rings that synchronised rather than only ones that died.
		sample_hist[v]++;
		prev = v;
		have_prev = true;
		st_samples++;
	}
	st_prev_sample = prev;
	st_have_prev = have_prev;
}

static void finishSelfTest(void) {
	bool passed = st_samples > 0;

	//Distribution check first: it is the one that catches synchronisation, which
	//every per-channel test below would happily pass. The self test always runs
	//with all eight rings on, so the full 8 bit scale applies.
	if (passed) {
		//Scale the window count to whatever was actually collected
		const uint32_t peak_limit = st_samples;
		uint32_t peak = 0;
		for (unsigned v = 0; v < 256u; v++) {
			if (sample_hist[v] > peak) {
				peak = sample_hist[v];
			}
		}
		if (peak == 0 || peak > peak_limit) {
			passed = false;
		} else {
			//log2(N) - log2(peak), both in 1/256 bit
			const uint32_t h = log2_fixed(st_samples) - log2_fixed(peak);
			mcv_last = (uint16_t)h;
			if (h < MCV_ALARM_NUM * RO_CHANNEL_COUNT) {
				passed = false;
			}
		}
	}

	if (passed) {
		for (uint8_t c = 0; c < RO_CHANNEL_COUNT; c++) {
			const uint32_t ones_pct = (st_ones[c] * 100u) / st_samples;
			const uint32_t change_permille = (st_changes[c] * 1000u) / st_samples;
			if (change_permille < SELF_TEST_MIN_CHANGE_PERMILLE
					|| ones_pct < SELF_TEST_BIAS_MIN_PERCENT
					|| ones_pct > SELF_TEST_BIAS_MAX_PERCENT) {
				passed = false;
				break;
			}
		}
	}

	configuration.selected_ro_channels = st_saved_channels;
	pending_raw_entropy = st_saved_raw;
	self_test_active = false;

	//Yellow back to whatever RAW mode says, verdict on green or red. No blink
	//codes: one colour, held long enough to read after letting go of the button.
	ledSetSteady(LED_MODE, st_saved_raw);
	ledSolidFor(passed ? LED_OK : LED_ERROR, VERDICT_MS);

	scheduled_action |= ACTION_REBUILD_LUT | ACTION_RESET_COLLECTOR;
}

void setBufferMultiplicity(int multiplicity) {
	multiplicity = multiplicity < 1 ? 1 : multiplicity;
	multiplicity = multiplicity > MAX_BUFFER_MULTIPLICITY ? MAX_BUFFER_MULTIPLICITY : multiplicity;
	configuration.selected_entropy_buffor_size = multiplicity * TC_SHA256_DIGEST_SIZE;
	scheduled_action |= ACTION_RESET_COLLECTOR;
	blinkSlow(multiplicity);
}

void saveConfiguration(void) {
	if (self_test_active) {
		//The self test temporarily forces every channel on; writing that to flash
		//would silently replace the user's channel selection.
		return;
	}
	scheduled_action |= ACTION_SAVE_CONFIG;
	blinkFast(1);
}

void reloadConfiguration(void) {
	scheduled_action |= ACTION_LOAD_CONFIG | ACTION_REBUILD_LUT | ACTION_RESET_COLLECTOR;
	blinkFast(1);
}

void requestInfo(void) {
	transfer_frozen = true;
	scheduled_action |= ACTION_SEND_INFO;
}

void resumeTransfer(void) {
	if (transfer_frozen) {
		transfer_frozen = false;
		//Discard whatever was half collected before the freeze
		scheduled_action |= ACTION_RESET_COLLECTOR;
	}
}

void noteReceivedByte(uint8_t c) {
	rx_byte_count++;
	if (c != '?' && c != '/') {
		//Keep the byte before the report request, otherwise RX would always be '?'
		last_rx_byte = c;
	}
}

static char* appendText(char* p, const char* s) {
	while (*s) {
		*p++ = *s++;
	}
	return p;
}

static char* appendU32(char* p, uint32_t v) {
	char digits[10];
	uint8_t n = 0;
	do {
		digits[n++] = (char)('0' + (v % 10u));
		v /= 10u;
	} while (v);
	while (n) {
		*p++ = digits[--n];
	}
	return p;
}

//Prints a 1/256 bit value as "7.45". Two decimals is the resolution that
//matters: the measured spread across every rate tested is 7.43 to 7.49.
static char* appendFixed256(char* p, uint16_t v) {
	p = appendU32(p, (uint32_t)(v >> 8));
	*p++ = '.';
	const uint32_t cent = ((uint32_t)(v & 0xFFu) * 100u) >> 8;
	*p++ = (char)('0' + (cent / 10u));
	*p++ = (char)('0' + (cent % 10u));
	return p;
}

static char* appendHex8(char* p, uint8_t v) {
	static const char hex[] = "0123456789ABCDEF";
	*p++ = hex[v >> 4];
	*p++ = hex[v & 0x0F];
	return p;
}

//Enabled channels as their command letters, disabled ones as '.', in 'a'..'h'
//order. Reads directly as the commands that produced it.
static char* appendChannels(char* p, uint8_t mask) {
	for (uint8_t i = 0; i < RO_CHANNEL_COUNT; i++) {
		*p++ = (mask & (1u << i)) ? (char)('A' + i) : '.';
	}
	return p;
}

static void buildInfoReport(void) {
	//APB1 prescaler is 1 in SystemClock_Config(), so the timer clock equals PCLK1
	uint32_t sampling_hz = HAL_RCC_GetPCLK1Freq()
			/ ((TIM6->PSC + 1u) * (TIM6->ARR + 1u));
	uint32_t raw_bytes_per_s = (sampling_hz * channel_bits) / 8u;
	//SHA mode emits one digest per buffer_bytes_target of collected entropy
	uint32_t upload_bytes_per_s = use_raw_entropy
			? raw_bytes_per_s
			: (raw_bytes_per_s * TC_SHA256_DIGEST_SIZE) / buffer_bytes_target;

	char* p = info_text;
	p = appendText(p, "\r\nCH=");
	p = appendChannels(p, configuration.selected_ro_channels);
	p = appendText(p, " N=");
	p = appendU32(p, channel_bits);
	p = appendText(p, "\r\nFS=");
	p = appendU32(p, sampling_hz);
	p = appendText(p, "Hz\r\nBUF=");
	p = appendU32(p, buffer_bytes_target);
	//How many collected 32B blocks are folded into one emitted block. RAW is 1:1,
	//SHA256 equals the buffer multiplicity.
	p = appendText(p, "B RATIO=");
	p = appendU32(p, buffer_bytes_target / TC_SHA256_DIGEST_SIZE);
	//CFG is the requested size, BUF the one the collector actually runs on. They
	//differ only if a command reached the config but resetCollector did not apply it.
	p = appendText(p, ":1 CFG=");
	p = appendU32(p, configuration.selected_entropy_buffor_size);
	p = appendText(p, "\r\nMODE=");
	p = appendText(p, use_raw_entropy ? "RAW" : "SHA256");
	p = appendText(p, "\r\nUP=");
	p = appendU32(p, upload_bytes_per_s);
	p = appendText(p, "B/s\r\nDROP usb=");
	p = appendU32(p, dropped_usb_batches);
	p = appendText(p, " col=");
	p = appendU32(p, dropped_collector_blocks);
	p = appendText(p, " health=");
	p = appendU32(p, health_failures);
	p = appendText(p, " mcv=");
	p = appendU32(p, mcv_failures);
	//Min-entropy of the most common sample value over the last completed window.
	//Not a measure of entropy quality - serial correlation, which is what makes
	//the budget fall with the sampling rate, is invisible here. It is a health
	//indicator: it barely moves in normal operation (7.43 to 7.49 measured from
	//50 kHz to 600 kHz) and collapses towards 1.0 if the rings synchronise.
	p = appendText(p, "\r\nMCV=");
	if (mcv_last != 0) {
		p = appendFixed256(p, mcv_last);
		p = appendText(p, "/");
		p = appendU32(p, channel_bits);
	} else {
		p = appendText(p, "-");        //no window has completed yet
	}
	//Last non-report byte the host sent, as hex, plus how many bytes arrived in total
	p = appendText(p, "\r\nRX=");
	p = appendHex8(p, last_rx_byte);
	p = appendText(p, " CNT=");
	p = appendU32(p, rx_byte_count);
	p = appendText(p, "\r\n");

	info_length = (uint16_t)(p - info_text);
	info_pending = true;

	//Counters are cleared once they have been formatted, so every report answers
	//"what was dropped since the previous report" instead of accumulating the idle
	//time when no host was draining the endpoint. dropped_collector_blocks is
	//written by the TIM6 ISR, hence the critical section.
	dropped_usb_batches = 0;
	health_failures = 0;
	mcv_failures = 0;
	__disable_irq();
	dropped_collector_blocks = 0;
	__enable_irq();
}

//log2(x) * 256 for x > 0. Exponent from the leading zero count, mantissa scaled
//to [4096, 8192) and looked up in LOG2_FRAC with linear interpolation.
static uint32_t log2_fixed(uint32_t x) {
	if (x == 0) {
		return 0;
	}
	const uint32_t e = 31u - (uint32_t)__CLZ(x);
	const uint32_t m = (e >= 12u) ? (x >> (e - 12u)) : (x << (12u - e));
	const uint32_t f = m - 4096u;
	const uint32_t idx = f >> 7;
	const uint32_t rem = f & 127u;
	const uint32_t a = LOG2_FRAC[idx];
	const uint32_t b = LOG2_FRAC[idx + 1u];
	const uint32_t frac = a + ((b - a) * rem) / 128u;
	return e * 256u + (frac >> 4);
}

//Min-entropy of the closed window, in 1/256 bit: log2(N) - log2(most common).
static uint16_t mcvFromHistogram(void) {
	uint32_t peak = 0;
	for (unsigned v = 0; v < 256u; v++) {
		if (sample_hist[v] > peak) {
			peak = sample_hist[v];
		}
	}
	if (peak == 0) {
		return 0;
	}
	const uint32_t h = MCV_WINDOW_LOG2 * 256u - log2_fixed(peak);
	return (uint16_t)h;
}

static void mcvResetWindow(void) {
	memset(sample_hist, 0, sizeof(sample_hist));
	mcv_window_count = 0;
}

/* SP 800-90B 4.4 Repetition Count Test and Adaptive Proportion Test, applied per
 * channel to a freshly filled noise buffer. Returns false if any channel failed.
 *
 * Runs in the main loop, not in the ISR: the tests need the same samples the
 * collector already stored, so there is no reason to pay for them per sample.
 * Both tests keep state across buffers, because an RCT run and an APT window are
 * both longer than one buffer. */
static bool healthTestBuffer(const uint8_t* buffer, uint32_t bytes) {
	const uint8_t channels = channel_bits;
	if (channels == 0) {
		return true;                       //no source selected, nothing to test
	}

	const uint8_t sample_mask = (uint8_t)((1u << channels) - 1u);
	uint32_t acc = 0;
	uint8_t acc_bits = 0;
	bool passed = true;

	for (uint32_t i = 0; i < bytes; i++) {
		acc |= (uint32_t)buffer[i] << acc_bits;
		acc_bits += 8;

		while (acc_bits >= channels) {
			const uint8_t sample = (uint8_t)(acc & sample_mask);
			acc >>= channels;
			acc_bits -= channels;

			//APT window boundary: any channel still pending never reached 17
			//non-matches, so its match count exceeded the cutoff - that is a failure.
			if (apt_position >= HEALTH_APT_WINDOW) {
				if (apt_pending) {
					passed = false;
				}
				for (uint8_t c = 0; c < RO_CHANNEL_COUNT; c++) {
					apt_nonmatch[c] = 0;
				}
				apt_pending = sample_mask;
				apt_reference = sample;
				apt_position = 0;
			}
			apt_position++;

			//MCV monitor: the sample is already unpacked here, so the whole cost
			//is one increment. The window closes on its own schedule, which is
			//much longer than a buffer.
			sample_hist[sample]++;
			if (++mcv_window_count >= MCV_WINDOW_SAMPLES) {
				mcv_last = mcvFromHistogram();
				if (mcv_last < (uint16_t)(MCV_ALARM_NUM * channels)) {
					mcv_failures++;
					passed = false;     //treated like any other health failure
				}
				mcvResetWindow();
			}

			//RCT: three ops per sample, verdict once per window
			rct_changed_mask |= (uint8_t)(sample ^ rct_prev_sample);
			rct_prev_sample = sample;
			if (++rct_window_pos >= HEALTH_RCT_CUTOFF) {
				if ((rct_changed_mask & sample_mask) != sample_mask) {
					passed = false;             //a channel never changed in the window
				}
				rct_changed_mask = 0;
				rct_window_pos = 0;
			}

			//APT: count non-matches, and drop a channel from apt_pending once it has
			//enough of them to be safe. Once every channel is out, the rest of the
			//window costs one test.
			if (apt_pending) {
				uint8_t differing = (uint8_t)((sample ^ apt_reference) & apt_pending);
				while (differing) {
					const uint8_t lowest = (uint8_t)(differing & (uint8_t)(-(int8_t)differing));
					uint8_t c = 0;
					while (!(lowest & (1u << c))) {
						c++;
					}
					if (++apt_nonmatch[c] > HEALTH_APT_SLACK) {
						apt_pending = (uint8_t)(apt_pending & ~lowest);
					}
					differing = (uint8_t)(differing & (differing - 1u));
				}
			}
		}
	}

	return passed;
}

static void processEntropyBlock() {
	//Health tests run before conditioning, so a buffer from a source that just
	//failed is discarded instead of being hashed and shipped. Stronger than
	//reacting after the fact, and it keeps the failure off the USB stream.
	if (!healthTestBuffer(buffer_to_process, buffer_bytes_target)) {
		health_failures++;
		blinkError(1);
		return;
	}

	//Heartbeat: one flash per fixed amount of collected data, so the green LED
	//shows both that the stream is alive and roughly how fast it runs. Placed
	//after the health test, so a source that keeps failing goes dark rather than
	//pulsing happily.
	heartbeat_bytes += buffer_bytes_target;
	if (heartbeat_bytes >= HEARTBEAT_BYTES) {
		heartbeat_bytes -= HEARTBEAT_BYTES;
		ledPulse(LED_OK);
	}

	uint8_t* slot = &processed_entropy_block
			[processed_entropy_block_index]
			 [processed_entropy_block_fill_index * TC_SHA256_DIGEST_SIZE];

	if (use_raw_entropy) {
		//SEND RAW
		memcpy(slot, buffer_to_process, TC_SHA256_DIGEST_SIZE);
	} else {
		//Pass through SHA256
		struct tc_sha256_state_struct sha256_state;
		tc_sha256_init(&sha256_state);
		tc_sha256_update(&sha256_state, buffer_to_process, configuration.selected_entropy_buffor_size);
		tc_sha256_final(slot, &sha256_state);
	}

	if (++processed_entropy_block_fill_index < ENTROPY_BATCH_SIZE) {
		return;
	}
	processed_entropy_block_fill_index = 0;

	if (sendEntropyToHost(
			processed_entropy_block[processed_entropy_block_index], ENTROPY_BATCH_SIZE * TC_SHA256_DIGEST_SIZE)) {
		processed_entropy_block_index++;
		if (processed_entropy_block_index >= ENTROPY_PROCESS_BUFFER_COUNT) {
			processed_entropy_block_index = 0;
		}
	} else {
		//Overflow error
		dropped_usb_batches++;
		blinkError(2);
	}
}

static void copyEntropyBitsToBuffer(uint8_t input_data, size_t data_bits_count) {

	bit_acc  |= (uint32_t)input_data << acc_bits;
	acc_bits += data_bits_count;

	if (acc_bits >= 8) {
		*fill_ptr++ = (uint8_t)bit_acc;
		bit_acc >>= 8;
		acc_bits -= 8;

		if (fill_ptr == fill_end) {
			if (buffer_to_process != NULL) {
				dropped_collector_blocks++;
				blinkError(2);	//Error
			}
			buffer_to_process = entropy_buffer_to_fill;
			swapInputBuffer();
		}
	}
}

void updateCollector() {
	const uint32_t now = HAL_GetTick();

	if (self_test_active) {
		//Buffers are analysed here instead of being conditioned and sent, so no
		//test data ever reaches the host.
		if (buffer_to_process != NULL) {
			analyseSelfTestBuffer(buffer_to_process, buffer_bytes_target);
			buffer_to_process = NULL;
		}
		if ((int32_t)(now - self_test_end) >= 0) {
			finishSelfTest();
		}
	} else if (restart_resume_at != 0) {
		//Paused: the timer is already stopped, just drop anything still in flight
		//so the buffer that was half full when the button was pressed is not
		//mixed into the first buffer after the pause.
		buffer_to_process = NULL;
		if ((int32_t)(now - restart_resume_at) >= 0) {
			restart_resume_at = 0;
			resetHealthState();
			ledClear(LED_MODE);
			ledSetSteady(LED_MODE, use_raw_entropy);
			//Falls through to the action block below, which restarts the timer
			//now that acquisitionSuspended() is false again.
			scheduled_action |= ACTION_RESET_COLLECTOR;
		}
	} else if (!transfer_frozen && buffer_to_process != NULL) {
		processEntropyBlock();
		buffer_to_process = NULL;
	}

	uint8_t actions;
	__disable_irq();
	actions = scheduled_action;
	scheduled_action = 0;
	__enable_irq();

	if (actions) {
		TIM6->CR1 &= ~TIM_CR1_CEN;         /* stop timer for reconfiguration time */
		if (actions & ACTION_LOAD_CONFIG)     loadAndValidateConfig();
		if (actions & ACTION_SAVE_CONFIG)     save_config(&configuration);
		if (actions & ACTION_REBUILD_LUT)     rebuildChannelLut();
		if (actions & ACTION_APPLY_SAMPLING)  applySamplingRate();
		if (actions & ACTION_RESET_COLLECTOR) resetCollector();
		if (actions & ACTION_SEND_INFO)       buildInfoReport();
		/* stays stopped while the report is up, during the restart pause, and
		   while a self test is between buffers - each of those restarts it
		   itself once its own condition clears */
		if (!transfer_frozen && restart_resume_at == 0) {
			TIM6->CNT = 0;                 /* full first period after resume */
			TIM6->SR = 0;                  /* reset UIF increased in pause time */
			TIM6->CR1 |= TIM_CR1_CEN;
		}
	}

	//Retried until the sender is free, e.g. when an entropy batch was in flight
	if (info_pending && sendEntropyToHost((const uint8_t*)info_text, info_length)) {
		info_pending = false;
	}
}

inline void collectEntropyBits(uint32_t port_a_idr) {
	const uint8_t sample = channels_lut[port_a_idr & RO_PORT_MASK];
	copyEntropyBitsToBuffer(sample, channel_bits);
}

static void loadAndValidateConfig(void) {
	load_config(&configuration);
	if (configuration.magic != MAGIC_COOKIE) {
		configuration.magic = MAGIC_COOKIE;
		configuration.selected_entropy_buffor_size = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE;
		configuration.selected_ro_channels = VALID_RO_CHANNELS;
	}

	//An erased or half written page can hold a valid looking magic with a 0xFFFF
	//payload. buffer_bytes_target/fill_end are derived from the size, so a bogus
	//value would push the fill window past the end of the buffers.
	if (configuration.selected_entropy_buffor_size < TC_SHA256_DIGEST_SIZE
			|| configuration.selected_entropy_buffor_size > MAX_ENTROPY_BLOCK_SIZE
			|| (configuration.selected_entropy_buffor_size % TC_SHA256_DIGEST_SIZE) != 0) {
		configuration.selected_entropy_buffor_size = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE;
	}
	configuration.selected_ro_channels &= VALID_RO_CHANNELS;

	//An erased byte reads as 0xFF, which is not a valid table index
	if (configuration.sampling_index >= SAMPLING_RATE_COUNT) {
		configuration.sampling_index = DEFAULT_SAMPLING_INDEX;
	}
}

void loadConfiguration() {
	loadAndValidateConfig();
	rebuildChannelLut();
	//Overrides the ARR that MX_TIM6_Init() set, before the timer is started
	applySamplingRate();
	//Derives buffer_bytes_target and the fill window from the loaded size.
	//Runs before HAL_TIM_Base_Start_IT, so no need for the deferred action path.
	resetCollector();
}
