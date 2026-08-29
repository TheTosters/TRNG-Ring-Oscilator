/*
 * heptic.h
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */

#ifndef INC_HAPTIC_H_
#define INC_HAPTIC_H_

#include <stdint.h>
#include <stdbool.h>

typedef enum {
    BLINK_MODE_OFF,
    BLINK_MODE_FAST_OFF,
    BLINK_MODE_FAST_ON,
    BLINK_MODE_SLOW_OFF,
    BLINK_MODE_SLOW_ON,
	BLINK_MODE_ON,
} BlinkMode_t;

//Channels are named after what they mean, not after the LED number, because the
//three LEDs differ in colour and the meaning has to follow the colour:
//  led1 / PB5 / D2 - green  -> healthy operation
//  led2 / PB6 / D3 - yellow -> a mode that is not the normal one
//  led3 / PB7 / D4 - red    -> something went wrong
//Getting this backwards (red for a mode, yellow for errors) is how the first
//version was wired up in software, before the BOM was checked.
typedef enum {
    LED_OK    = 0,   //green  - flow heartbeat, self test passed
    LED_MODE  = 1,   //yellow - RAW output selected, or acquisition paused
    LED_ERROR = 2,   //red    - dropped batches, health failures, self test failed
    LED_COUNT
} LedChannel_t;

void start_blink(LedChannel_t channel, uint8_t count, BlinkMode_t mode);

static inline void blinkFast(uint8_t count) {
    start_blink(LED_OK, count, BLINK_MODE_FAST_OFF);
}

static inline void blinkSlow(uint8_t count) {
    start_blink(LED_OK, count, BLINK_MODE_SLOW_OFF);
}

//Errors get their own LED, so a burst of dropped batches cannot hide a command
//acknowledgement and the other way round.
static inline void blinkError(uint8_t count) {
    start_blink(LED_ERROR, count, BLINK_MODE_FAST_OFF);
}

//One short flash, used as the flow heartbeat. Unlike start_blink() this is meant
//to be called at a steady rate from the data path, so it deliberately does
//nothing while a longer sequence (a command acknowledgement) is still running -
//otherwise the heartbeat would cut every acknowledgement short.
void ledPulse(LedChannel_t channel);

//Light the LED for a fixed time, then return to off. Used for the self test
//verdict, which has to stay readable after the button is released.
void ledSolidFor(LedChannel_t channel, uint32_t ms);

//Steady on/off, for a state that lasts until something changes it.
void ledSetSteady(LedChannel_t channel, bool on);

//Cancels whatever the channel was doing and leaves it dark.
void ledClear(LedChannel_t channel);

void constantGlow(void);
void ledNormal(void);
void ledUpdate(void);

#endif /* INC_HAPTIC_H_ */
