/*
 * heptic.c
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */
#include <haptic.h>
#include "stm32f0xx_hal.h"
#include <stdbool.h>

#define HEPTIC_LED_PIN                  (GPIO_PIN_6)
#define HEPTIC_LED_PORT                 (GPIOA)

#define HEPTIC_BLINK_FAST_INTERVAL_MS   (100)
#define HEPTIC_BLINK_SLOW_INTERVAL_MS   (300)

static BlinkMode_t blink_mode = BLINK_MODE_OFF;

static uint32_t blink_interval = 0;
static uint8_t blink_count = 0;
static uint32_t last_toggle_time = 0;
static bool led_state = false;

static void set_led(bool state) {
    if (state) {
        HEPTIC_LED_PORT->BSRR = HEPTIC_LED_PIN;
    } else {
        HEPTIC_LED_PORT->BRR = HEPTIC_LED_PIN;
    }
}

void start_blink(uint8_t count, BlinkMode_t mode) {
    if (count == 0) {
        return;
    }
	switch(blink_mode) {
		case BLINK_MODE_OFF:
		case BLINK_MODE_FAST_OFF:
		case BLINK_MODE_SLOW_OFF:
			blink_mode = (mode == BLINK_MODE_FAST_OFF ? BLINK_MODE_FAST_OFF : BLINK_MODE_SLOW_OFF);
			break;

		case BLINK_MODE_ON:
		case BLINK_MODE_FAST_ON:
		case BLINK_MODE_SLOW_ON:
			blink_mode = (mode == BLINK_MODE_FAST_OFF ? BLINK_MODE_FAST_ON : BLINK_MODE_SLOW_ON);
			break;

		default:
			blink_mode = mode;
			return;
	}

    blink_interval = ((blink_mode == BLINK_MODE_FAST_ON || blink_mode == BLINK_MODE_FAST_OFF) ? HEPTIC_BLINK_FAST_INTERVAL_MS : HEPTIC_BLINK_SLOW_INTERVAL_MS);
    blink_count = 2*count;
    last_toggle_time = HAL_GetTick();
}

void constantGlow(void) {
    blink_mode = BLINK_MODE_ON;
}

void ledNormal(void) {
    blink_mode = BLINK_MODE_OFF;
}

void ledUpdate(void) {
	uint32_t current_time;

	switch(blink_mode) {
		case BLINK_MODE_OFF:
			set_led(false);
			return;

		case BLINK_MODE_FAST_ON:
		case BLINK_MODE_SLOW_ON:
		case BLINK_MODE_FAST_OFF:
		case BLINK_MODE_SLOW_OFF:
		    current_time = HAL_GetTick();
		    if ((current_time - last_toggle_time) >= blink_interval) {
		    	last_toggle_time = current_time;
		    	led_state = !led_state;
		    	set_led(led_state);
		    	if (blink_count) {
		    		blink_count--;
		    	} else {
		    		blink_mode = ((blink_mode == BLINK_MODE_FAST_ON || blink_mode == BLINK_MODE_SLOW_ON) ? BLINK_MODE_ON : BLINK_MODE_OFF);
		    	}
		    }
			break;

		case BLINK_MODE_ON:
			set_led(true);
			return;
	}
}
