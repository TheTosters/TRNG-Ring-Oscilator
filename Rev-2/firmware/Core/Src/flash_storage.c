/*
 * flash_storage.c
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */
#include "flash_storage.h"
#include "stm32g4xx_hal.h"
#include <string.h>

//STM32G431CB has 128 KB of flash in a single bank, organised in 2 KB pages, so
//the last page is number 63 at 0x0801F800. The linker script hands the whole
//128 KB to the image, so this page is only free as long as the firmware stays
//below 126 KB - it currently uses well under 32 KB.
#define STORAGE_FLASH_PAGE  (63u)
#define STORAGE_FLASH_ADDR  (FLASH_BASE + STORAGE_FLASH_PAGE * FLASH_PAGE_SIZE)

void save_config(AppConfig_t *config) {
    //HAL_FLASH_Program reads 8 bytes from the address it is given, so the source
    //has to be double word aligned - AppConfig_t on the caller's stack is not.
    uint64_t words[sizeof(AppConfig_t) / 8u];
    memcpy(words, config, sizeof(AppConfig_t));

    uint32_t address = STORAGE_FLASH_ADDR;

    HAL_FLASH_Unlock();

    FLASH_EraseInitTypeDef EraseInitStruct;
    uint32_t PageError = 0;

    EraseInitStruct.TypeErase = FLASH_TYPEERASE_PAGES;
    EraseInitStruct.Banks = FLASH_BANK_1;
    EraseInitStruct.Page = STORAGE_FLASH_PAGE;
    EraseInitStruct.NbPages = 1;

    if (HAL_FLASHEx_Erase(&EraseInitStruct, &PageError) != HAL_OK) {
        HAL_FLASH_Lock();
    }

    for (uint16_t i = 0; i < (sizeof(words) / sizeof(words[0])); i++) {
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD, address, words[i]) != HAL_OK) {
            HAL_FLASH_Lock();
        }
        address += 8;
    }

    HAL_FLASH_Lock();
}

void load_config(AppConfig_t *config) {
    memcpy(config, (const void*)STORAGE_FLASH_ADDR, sizeof(AppConfig_t));
}
