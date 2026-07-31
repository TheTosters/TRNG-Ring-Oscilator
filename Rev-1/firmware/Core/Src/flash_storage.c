/*
 * flash_storage.c
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */
#include "flash_storage.h"
#include "stm32f0xx_hal.h"

#define STORAGE_FLASH_ADDR  0x08007000

void save_config(AppConfig_t *config) {
    uint32_t *p_data = (uint32_t*)config;
    uint32_t address = STORAGE_FLASH_ADDR;
    uint16_t word_count = sizeof(AppConfig_t) / 4;

    HAL_FLASH_Unlock();

    FLASH_EraseInitTypeDef EraseInitStruct;
    uint32_t PageError = 0;

    EraseInitStruct.TypeErase = FLASH_TYPEERASE_PAGES;
    EraseInitStruct.PageAddress = STORAGE_FLASH_ADDR;
    EraseInitStruct.NbPages = 1;

    if (HAL_FLASHEx_Erase(&EraseInitStruct, &PageError) != HAL_OK) {
        HAL_FLASH_Lock();
    }

    for (uint16_t i = 0; i < word_count; i++) {
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, address, p_data[i]) != HAL_OK) {
            HAL_FLASH_Lock();
        }
        address += 4;
    }

    HAL_FLASH_Lock();
}

void load_config(AppConfig_t *config) {
    uint32_t *p_dest = (uint32_t*)config;
    uint32_t address = STORAGE_FLASH_ADDR;
    uint16_t word_count = sizeof(AppConfig_t) / 4;

    for (uint16_t i = 0; i < word_count; i++) {
        p_dest[i] = *(__IO uint32_t*)address;
        address += 4;
    }
}
