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
    uint16_t selected_entropy_buffor_size;
} AppConfig_t;

void save_config(AppConfig_t *config);
void load_config(AppConfig_t *config);

#endif
