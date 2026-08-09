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

#define MAGIC_COOKIE (0xDABAD00A)

#define MAX_BUFFER_MULTIPLICITY (9)
#define DEFAULT_MULTIPLICITY (4)

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

static AppConfig_t configuration = {
		.magic = MAGIC_COOKIE,
		.selected_entropy_buffor_size = DEFAULT_MULTIPLICITY * TC_SHA256_DIGEST_SIZE,
		.selected_ro_channels = VALID_RO_CHANNELS
};

static uint8_t channels_lut[256];
static uint8_t channel_bits;

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
		uint8_t bitMasks[] = {
			  0b00000001,	//PA0
			  0b00000010,	//PA1
			  0b00000100,	//PA2
			  0b00001000,	//PA3
			  0b00010000,	//PA4
			  0b10000000,	//PA7
			};
		uint8_t value= bitMasks[ringIndex];
		if (enabled) {
			configuration.selected_ro_channels |= value;
			blinkFast(4);
		} else {
			configuration.selected_ro_channels &= (~value);
			blinkFast(2);
		}
		scheduled_action |= ACTION_RESET_COLLECTOR | ACTION_SAVE_CONFIG | ACTION_REBUILD_LUT;
	}
}

void setBufferMultiplicity(int multiplicity) {
	multiplicity = multiplicity < 1 ? 1 : multiplicity;
	multiplicity = multiplicity > MAX_BUFFER_MULTIPLICITY ? MAX_BUFFER_MULTIPLICITY : multiplicity;
	configuration.selected_entropy_buffor_size = multiplicity * TC_SHA256_DIGEST_SIZE;
	scheduled_action |= ACTION_RESET_COLLECTOR | ACTION_SAVE_CONFIG;
	blinkSlow(multiplicity);
}

static void processEntropyBlock() {
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
				blinkFast(2);	//Error
			}
			buffer_to_process = entropy_buffer_to_fill;
			swapInputBuffer();
		}
	}
}

void updateCollector() {
	if (buffer_to_process != NULL) {
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
		if (actions & ACTION_SAVE_CONFIG)     save_config(&configuration);
		if (actions & ACTION_REBUILD_LUT)     rebuildChannelLut();
		if (actions & ACTION_RESET_COLLECTOR) resetCollector();
		TIM14->CNT = 0;                    /* full first period after resume */
		TIM14->SR = 0;                     /* reset UIF increased in pause time */
		TIM14->CR1 |= TIM_CR1_CEN;
	}
}

inline void collectEntropyBits(uint8_t data) {
	copyEntropyBitsToBuffer(channels_lut[data], channel_bits);
}

void loadConfiguration() {
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

	rebuildChannelLut();
	//Derives buffer_bytes_target and the fill window from the loaded size.
	//Runs before HAL_TIM_Base_Start_IT, so no need for the deferred action path.
	resetCollector();
}
