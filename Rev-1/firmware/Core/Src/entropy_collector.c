/*
 * entropy_collector.c
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */

#include <string.h>
#include "entropy_collector.h"
#include "flash_storage.h"
#include "tinycrypt/sha256.h"
#include "haptic.h"
#include "sender.h"

#define MAGIC_COOKIE (0xDABAD00A)

#define MAX_BUFFER_MULTIPLICITY (9)

#define MAX_ENTROPY_BLOCK_SIZE (MAX_BUFFER_MULTIPLICITY * TC_SHA256_DIGEST_SIZE)

static uint8_t processed_entropy_block[TC_SHA256_DIGEST_SIZE];

static uint8_t raw_entropy_block[MAX_ENTROPY_BLOCK_SIZE] = { 0 };
static uint8_t raw_entropy_block2[MAX_ENTROPY_BLOCK_SIZE] = { 0 };
static bool primary_buffer = true;
static uint8_t* entropy_buffer_to_fill = raw_entropy_block;
static uint8_t* buffer_to_process = NULL;

static uint32_t raw_entropy_bit_index = 0;
static bool use_raw_entropy = false;

static AppConfig_t configuration = {
		.magic = MAGIC_COOKIE,
		.selected_entropy_buffor_size = 32,
		.selected_ro_channels = 0b10011111
};

static void swapInputBuffer() {
	primary_buffer = !primary_buffer;
	raw_entropy_bit_index = 0;
	entropy_buffer_to_fill = primary_buffer ? raw_entropy_block : raw_entropy_block2;
	memset(entropy_buffer_to_fill, 0, configuration.selected_entropy_buffor_size);
}

inline void useRawEntropy(bool use_raw) {
	use_raw_entropy = use_raw;
	swapInputBuffer();
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
		save_config(&configuration);
		swapInputBuffer();
	}
}

void setBufferMultiplicity(int multiplicity) {
	multiplicity = multiplicity < 1 ? 1 : multiplicity;
	multiplicity = multiplicity > MAX_BUFFER_MULTIPLICITY ? MAX_BUFFER_MULTIPLICITY : multiplicity;
	configuration.selected_entropy_buffor_size = multiplicity * TC_SHA256_DIGEST_SIZE;
	save_config(&configuration);
	blinkSlow(multiplicity);
}

static void select_data(uint8_t data, uint8_t selected, uint8_t* out_data, size_t* out_bits) {
    uint8_t result = 0;
    uint8_t bit_pos = 0;

    for (uint8_t i = 0; i < 6; i++) {
        if (selected & (1 << i)) {
            if (data & (1 << i)) {
                result |= (1 << bit_pos);
            }
            bit_pos++;
        }
    }

    *out_data = result;
    *out_bits = bit_pos;
}

static void processEntropyBlock() {
	if (use_raw_entropy) {
		//SEND RAW
		sendEntropyToHost(buffer_to_process, TC_SHA256_DIGEST_SIZE);
	} else {
		//Pass through SHA256
		struct tc_sha256_state_struct sha256_state;
		tc_sha256_init(&sha256_state);
		tc_sha256_update(&sha256_state, buffer_to_process, configuration.selected_entropy_buffor_size);
		tc_sha256_final(processed_entropy_block, &sha256_state);
		sendEntropyToHost(processed_entropy_block, TC_SHA256_DIGEST_SIZE);
	}
}

static void copyEntropyBitsToBuffer(uint8_t input_data, size_t data_bits_count) {

	uint32_t buffor_size_to_use = use_raw_entropy ? TC_SHA256_DIGEST_SIZE : configuration.selected_entropy_buffor_size;

	uint32_t remaining_space = (buffor_size_to_use * 8) - raw_entropy_bit_index;
	uint32_t bits_to_write_count =
			(remaining_space < data_bits_count) ? remaining_space : data_bits_count;

	if (bits_to_write_count > 0) {
		uint8_t data = input_data & ((1 << bits_to_write_count) - 1);

		uint32_t byte = raw_entropy_bit_index >> 3;
		uint32_t bit_offset = raw_entropy_bit_index & 7;

		entropy_buffer_to_fill[byte] |= (data << bit_offset);

		if (bit_offset + bits_to_write_count > 8) {
			if (byte + 1 < buffor_size_to_use ) {
				entropy_buffer_to_fill[byte + 1] |= (data >> (8 - bit_offset));
			}
		}

		raw_entropy_bit_index += bits_to_write_count;
	}

	if (raw_entropy_bit_index >= buffor_size_to_use * 8) {
		//This buffer should be processed out of IRQ call
		if (buffer_to_process != NULL) {
			//GENERAL ERROR
			blinkFast(99);
		}
		buffer_to_process = entropy_buffer_to_fill;
		swapInputBuffer();
	}
}

void updateCollector() {
	if (buffer_to_process != NULL) {
		processEntropyBlock();
		buffer_to_process = NULL;
	}
}

void collectEntropyBits(uint8_t data) {
	uint8_t extracted_data;
	size_t extracted_bits_count;
	select_data(data, configuration.selected_ro_channels, &extracted_data, &extracted_bits_count);
	copyEntropyBitsToBuffer(extracted_data, extracted_bits_count);
}

void loadConfiguration() {
	load_config(&configuration);
	if (configuration.magic != MAGIC_COOKIE) {
		configuration.magic = MAGIC_COOKIE;
		configuration.selected_entropy_buffor_size = 32;
		configuration.selected_ro_channels = 0b10011111;
	}
}
