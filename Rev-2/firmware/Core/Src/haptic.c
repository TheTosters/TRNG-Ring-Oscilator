/*
 * heptic.c
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */
#include <haptic.h>
#include "main.h"
#include "stm32g4xx_hal.h"
#include <stdbool.h>

#define HEPTIC_BLINK_FAST_INTERVAL_MS   (100)
#define HEPTIC_BLINK_SLOW_INTERVAL_MS   (300)

//Length of the heartbeat flash. Long enough to be seen, short enough that the
//LED still reads as pulsing at the highest sampling rate rather than as lit.
#define HEPTIC_PULSE_MS                 (25)

typedef struct {
	GPIO_TypeDef* port;
	uint16_t      pin;
	BlinkMode_t   blink_mode;
	uint32_t      blink_interval;
	uint8_t       blink_count;
	uint32_t      last_toggle_time;
	bool          led_state;
	//Non-zero while a timed state (pulse or verdict) owns the channel. Takes
	//precedence over blink_mode, which is restored to OFF when it expires.
	uint32_t      hold_until;
} LedState_t;

//Indexed by LedChannel_t
static LedState_t leds[LED_COUNT] = {
	[LED_OK]    = { .port = led1_GPIO_Port, .pin = led1_Pin, .blink_mode = BLINK_MODE_OFF },
	[LED_MODE]  = { .port = led2_GPIO_Port, .pin = led2_Pin, .blink_mode = BLINK_MODE_OFF },
	[LED_ERROR] = { .port = led3_GPIO_Port, .pin = led3_Pin, .blink_mode = BLINK_MODE_OFF },
};

static bool holdActive(const LedState_t* led, uint32_t now) {
	//Tick wraps every 49 days; the signed difference keeps the comparison valid
	//across the wrap for any hold shorter than half that.
	return led->hold_until != 0 && (int32_t)(led->hold_until - now) > 0;
}

static void set_led(LedState_t* led, bool state) {
    if (state) {
        led->port->BSRR = led->pin;
    } else {
        led->port->BRR = led->pin;
    }
}

void start_blink(LedChannel_t channel, uint8_t count, BlinkMode_t mode) {
    if (count == 0 || channel >= LED_COUNT) {
        return;
    }
    LedState_t* led = &leds[channel];
    if (holdActive(led, HAL_GetTick())) {
        return;                  //a verdict or pulse owns the channel right now
    }
    //Never restart a sequence that is still running. A high rate caller (an error
    //fired on every dropped batch) would keep pushing last_toggle_time forward,
    //ledUpdate would never see the interval elapse, and the LED would stay dark
    //instead of blinking - the exact opposite of what an error signal should do.
    if (led->blink_count != 0) {
        return;
    }
	switch(led->blink_mode) {
		case BLINK_MODE_OFF:
		case BLINK_MODE_FAST_OFF:
		case BLINK_MODE_SLOW_OFF:
			led->blink_mode = (mode == BLINK_MODE_FAST_OFF ? BLINK_MODE_FAST_OFF : BLINK_MODE_SLOW_OFF);
			break;

		case BLINK_MODE_ON:
		case BLINK_MODE_FAST_ON:
		case BLINK_MODE_SLOW_ON:
			led->blink_mode = (mode == BLINK_MODE_FAST_OFF ? BLINK_MODE_FAST_ON : BLINK_MODE_SLOW_ON);
			break;

		default:
			led->blink_mode = mode;
			return;
	}

    led->blink_interval = ((led->blink_mode == BLINK_MODE_FAST_ON || led->blink_mode == BLINK_MODE_FAST_OFF)
    		? HEPTIC_BLINK_FAST_INTERVAL_MS : HEPTIC_BLINK_SLOW_INTERVAL_MS);
    led->blink_count = 2*count;
    led->last_toggle_time = HAL_GetTick();
}

void ledPulse(LedChannel_t channel) {
    if (channel >= LED_COUNT) {
        return;
    }
    LedState_t* led = &leds[channel];
    const uint32_t now = HAL_GetTick();
    //Yield to anything already showing: a running acknowledgement, a verdict, or
    //the previous pulse. Skipping a heartbeat beat is free, cutting a message
    //short is not.
    if (led->blink_count != 0 || holdActive(led, now)
            || led->blink_mode == BLINK_MODE_ON) {
        return;
    }
    led->hold_until = now + HEPTIC_PULSE_MS;
    if (led->hold_until == 0) {
        led->hold_until = 1;     //0 is the "no hold" marker
    }
    led->led_state = true;
}

void ledSolidFor(LedChannel_t channel, uint32_t ms) {
    if (channel >= LED_COUNT || ms == 0) {
        return;
    }
    LedState_t* led = &leds[channel];
    led->blink_count = 0;
    led->blink_mode = BLINK_MODE_OFF;
    led->hold_until = HAL_GetTick() + ms;
    if (led->hold_until == 0) {
        led->hold_until = 1;
    }
    led->led_state = true;
}

void ledSetSteady(LedChannel_t channel, bool on) {
    if (channel >= LED_COUNT) {
        return;
    }
    leds[channel].hold_until = 0;
    leds[channel].blink_count = 0;
    leds[channel].blink_mode = on ? BLINK_MODE_ON : BLINK_MODE_OFF;
}

void ledClear(LedChannel_t channel) {
    ledSetSteady(channel, false);
}

void constantGlow(void) {
    ledSetSteady(LED_MODE, true);
}

void ledNormal(void) {
    ledSetSteady(LED_MODE, false);
}

static void ledUpdateChannel(LedState_t* led) {
	uint32_t current_time;

	if (led->hold_until != 0) {
		if (holdActive(led, HAL_GetTick())) {
			set_led(led, led->led_state);
			return;
		}
		led->hold_until = 0;
		led->led_state = false;
		//falls through to blink_mode, which is OFF unless something steady is set
	}

	switch(led->blink_mode) {
		case BLINK_MODE_OFF:
			set_led(led, false);
			return;

		case BLINK_MODE_FAST_ON:
		case BLINK_MODE_SLOW_ON:
		case BLINK_MODE_FAST_OFF:
		case BLINK_MODE_SLOW_OFF:
		    current_time = HAL_GetTick();
		    if ((current_time - led->last_toggle_time) >= led->blink_interval) {
		    	led->last_toggle_time = current_time;
		    	led->led_state = !led->led_state;
		    	set_led(led, led->led_state);
		    	if (led->blink_count) {
		    		led->blink_count--;
		    	} else {
		    		led->blink_mode = ((led->blink_mode == BLINK_MODE_FAST_ON || led->blink_mode == BLINK_MODE_SLOW_ON)
		    				? BLINK_MODE_ON : BLINK_MODE_OFF);
		    	}
		    }
			break;

		case BLINK_MODE_ON:
			set_led(led, true);
			return;
	}
}

void ledUpdate(void) {
	for (uint8_t i = 0; i < LED_COUNT; i++) {
		ledUpdateChannel(&leds[i]);
	}
}
