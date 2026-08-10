/*
 * entropy_collector.c
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */

#include <string.h>
#include "stm32f0xx_hal.h"
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

//Worst case status report is 149 bytes, see buildInfoReport()
#define INFO_TEXT_SIZE (160)

#define MAGIC_COOKIE (0xDABAD00A)

#define MAX_BUFFER_MULTIPLICITY (9)
//9:1 gives >=1 bit of assessed entropy per output bit, see Rev-1/entopy_tests/summary.md
#define DEFAULT_MULTIPLICITY (9)

//SP 800-90B 4.4 health tests, run per channel because the noise source is six
//independent rings. Cutoffs derived for the worst measured per-channel
//min-entropy H = 0.08 bit/sample and alpha = 2^-30:
//  RCT: C = 1 + ceil(-log2(alpha)/H) = 376
//  APT: W = 1024, C = CRITBINOM(W, 2^-H, 1-alpha) = 1007
//alpha = 2^-30 puts a false alarm at roughly once per 25 days of running, and
//one costs a single discarded buffer out of ~260 produced per second.
#define HEALTH_RCT_CUTOFF (376)
#define HEALTH_APT_WINDOW (1024)
#define HEALTH_APT_CUTOFF (1007)
//"matches > 1007 out of 1024" is the same statement as "non-matches <= 16", and
//counting non-matches lets a channel be written off as healthy as soon as it has
//17 of them - after roughly 40 samples of a 1024 sample window. The remaining 96%
//of the window then costs nothing.
#define HEALTH_APT_SLACK (HEALTH_APT_WINDOW - HEALTH_APT_CUTOFF - 1)

//PA0..PA4 + PA7; PA5 is the latch output and PA6 drives the LED
#define VALID_RO_CHANNELS (0b10011111)

#define MAX_ENTROPY_BLOCK_SIZE (MAX_BUFFER_MULTIPLICITY * TC_SHA256_DIGEST_SIZE)

#define ENTROPY_BATCH_SIZE (5)
#define ENTROPY_PROCESS_BUFFER_COUNT (3)


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
//loop over a completed buffer, never in the ISR, so it costs the 100 kHz
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
static uint8_t  apt_nonmatch[6] = {0};
static uint8_t  apt_pending = 0;                    //channels not yet proven healthy
static uint16_t apt_position = HEALTH_APT_WINDOW;   //forces a new window on first sample
static uint32_t health_failures = 0;

static AppConfig_t configuration = {
		.magic = MAGIC_COOKIE,
		.selected_entropy_buffor_size = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE,
		.selected_ro_channels = VALID_RO_CHANNELS
};

static uint8_t channels_lut[256];
static uint8_t channel_bits;

//Command letter order 'a'..'f' mapped to port bits. PA5 drives the latch and PA6
//the LED, which is why channel 'f' lives on PA7 instead of PA5.
static const uint8_t ro_channel_bit[6] = {
	0b00000001,	//PA0 - 'a'
	0b00000010,	//PA1 - 'b'
	0b00000100,	//PA2 - 'c'
	0b00001000,	//PA3 - 'd'
	0b00010000,	//PA4 - 'e'
	0b10000000,	//PA7 - 'f'
};

static void loadAndValidateConfig(void);

static void rebuildChannelLut(void) {
    const uint8_t mask = configuration.selected_ro_channels;
    for (unsigned v = 0; v < 256; v++) {
        uint8_t res = 0, pos = 0;
        for (unsigned i = 0; i < 8; i++) {
            if (mask & (1u << i)) {
                if (v & (1u << i)) res |= (uint8_t)(1u << pos);
                pos++;
            }
        }
        channels_lut[v] = res;
    }
    channel_bits = 0;
    for (unsigned i = 0; i < 8; i++) {
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

static void resetCollector(void)
{
    use_raw_entropy = pending_raw_entropy;
    buffer_bytes_target = use_raw_entropy ? TC_SHA256_DIGEST_SIZE
                                          : configuration.selected_entropy_buffor_size;
    bit_acc = 0;
    acc_bits = 0;
    buffer_to_process = NULL;
    processed_entropy_block_fill_index = 0;
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
	if (ringIndex >=0 && ringIndex <= 5) {
		uint8_t value = ro_channel_bit[ringIndex];
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

void setBufferMultiplicity(int multiplicity) {
	multiplicity = multiplicity < 1 ? 1 : multiplicity;
	multiplicity = multiplicity > MAX_BUFFER_MULTIPLICITY ? MAX_BUFFER_MULTIPLICITY : multiplicity;
	configuration.selected_entropy_buffor_size = multiplicity * TC_SHA256_DIGEST_SIZE;
	scheduled_action |= ACTION_RESET_COLLECTOR;
	blinkSlow(multiplicity);
}

void saveConfiguration(void) {
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

static char* appendHex8(char* p, uint8_t v) {
	static const char hex[] = "0123456789ABCDEF";
	*p++ = hex[v >> 4];
	*p++ = hex[v & 0x0F];
	return p;
}

//Enabled channels as their command letters, disabled ones as '.', in 'a'..'f'
//order. Reads directly as the commands that produced it, and hides the two port
//bits (latch, LED) that can never be channels.
static char* appendChannels(char* p, uint8_t mask) {
	for (uint8_t i = 0; i < 6; i++) {
		*p++ = (mask & ro_channel_bit[i]) ? (char)('A' + i) : '.';
	}
	return p;
}

static void buildInfoReport(void) {
	//APB1 prescaler is 1 in SystemClock_Config(), so the timer clock equals PCLK1
	uint32_t sampling_hz = HAL_RCC_GetPCLK1Freq()
			/ ((TIM14->PSC + 1u) * (TIM14->ARR + 1u));
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
	//written by the TIM14 ISR, hence the critical section.
	dropped_usb_batches = 0;
	health_failures = 0;
	__disable_irq();
	dropped_collector_blocks = 0;
	__enable_irq();
}

/* SP 800-90B 4.4 Repetition Count Test and Adaptive Proportion Test, applied per
 * channel to a freshly filled noise buffer. Returns false if any channel failed.
 *
 * Runs in the main loop, not in the ISR: the tests need the same samples the
 * collector already stored, so there is no reason to pay for them at 100 kHz.
 * Both tests keep state across buffers, because an RCT run and an APT window are
 * both longer than one buffer. */
static bool healthTestBuffer(const uint8_t* buffer, uint32_t bytes) {
	const uint8_t channels = channel_bits;
	if (channels == 0) {
		return true;                       //no source selected, nothing to test
	}

	const uint8_t sample_mask = (uint8_t)((1u << channels) - 1);
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
				for (uint8_t c = 0; c < 6; c++) {
					apt_nonmatch[c] = 0;
				}
				apt_pending = sample_mask;
				apt_reference = sample;
				apt_position = 0;
			}
			apt_position++;

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
		blinkFast(1);
		return;
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
		blinkFast(2);
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
				blinkFast(2);	//Error
			}
			buffer_to_process = entropy_buffer_to_fill;
			swapInputBuffer();
		}
	}
}

void updateCollector() {
	if (!transfer_frozen && buffer_to_process != NULL) {
		processEntropyBlock();
		buffer_to_process = NULL;
	}

	uint8_t actions;
	__disable_irq();
	actions = scheduled_action;
	scheduled_action = 0;
	__enable_irq();

	if (actions) {
		TIM14->CR1 &= ~TIM_CR1_CEN;        /* stop timer for reconfiguration time */
		if (actions & ACTION_LOAD_CONFIG)     loadAndValidateConfig();
		if (actions & ACTION_SAVE_CONFIG)     save_config(&configuration);
		if (actions & ACTION_REBUILD_LUT)     rebuildChannelLut();
		if (actions & ACTION_RESET_COLLECTOR) resetCollector();
		if (actions & ACTION_SEND_INFO)       buildInfoReport();
		if (!transfer_frozen) {            /* stays stopped while the report is up */
			TIM14->CNT = 0;                /* full first period after resume */
			TIM14->SR = 0;                 /* reset UIF increased in pause time */
			TIM14->CR1 |= TIM_CR1_CEN;
		}
	}

	//Retried until the sender is free, e.g. when an entropy batch was in flight
	if (info_pending && sendEntropyToHost((const uint8_t*)info_text, info_length)) {
		info_pending = false;
	}
}

inline void collectEntropyBits(uint8_t data) {
	copyEntropyBitsToBuffer(channels_lut[data], channel_bits);
}

static void loadAndValidateConfig(void) {
	load_config(&configuration);
	if (configuration.magic != MAGIC_COOKIE) {
		configuration.magic = MAGIC_COOKIE;
		configuration.selected_entropy_buffor_size = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE;
		configuration.selected_ro_channels = VALID_RO_CHANNELS;
	}

	//Flash can hold a half-written record: save_config programs magic first and
	//the payload second, so a power loss in between leaves a valid magic with
	//0xFFFF payload. buffer_bytes_target/fill_end are derived from the size, so
	//a bogus value would push the fill window past the end of the buffers.
	if (configuration.selected_entropy_buffor_size < TC_SHA256_DIGEST_SIZE
			|| configuration.selected_entropy_buffor_size > MAX_ENTROPY_BLOCK_SIZE
			|| (configuration.selected_entropy_buffor_size % TC_SHA256_DIGEST_SIZE) != 0) {
		configuration.selected_entropy_buffor_size = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE;
	}
	configuration.selected_ro_channels &= VALID_RO_CHANNELS;
}

void loadConfiguration() {
	loadAndValidateConfig();
	rebuildChannelLut();
	//Derives buffer_bytes_target and the fill window from the loaded size.
	//Runs before HAL_TIM_Base_Start_IT, so no need for the deferred action path.
	resetCollector();
}
