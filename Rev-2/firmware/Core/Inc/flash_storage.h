/*
 * flash_storage.h
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */

#ifndef FLASH_STORAGE_H
#define FLASH_STORAGE_H

#include <stdint.h>
#include <stddef.h>

typedef struct {
	uint32_t magic;
    uint8_t  selected_ro_channels;
    //Index into sampling_arr[] in entropy_collector.c, not a frequency. Sits in
    //what used to be padding, so the record stays 8 bytes and the other fields
    //keep their offsets.
    uint8_t  sampling_index;
    uint16_t selected_entropy_buffor_size;
} AppConfig_t;

//STM32G4 programs flash in 64 bit units, so the record has to be a whole number
//of double words. The struct is 8 bytes with the natural padding after
//selected_entropy_buffor_size; the assert catches a future field that breaks it.
_Static_assert((sizeof(AppConfig_t) % 8u) == 0u,
		"AppConfig_t must be a multiple of 8 bytes for double word flash writes");

void save_config(AppConfig_t *config);
void load_config(AppConfig_t *config);

#endif
