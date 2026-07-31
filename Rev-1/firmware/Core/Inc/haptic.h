/*
 * heptic.h
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */

#ifndef INC_HAPTIC_H_
#define INC_HAPTIC_H_

#include <stdint.h>

typedef enum {
    BLINK_MODE_OFF,
    BLINK_MODE_FAST_OFF,
    BLINK_MODE_FAST_ON,
    BLINK_MODE_SLOW_OFF,
    BLINK_MODE_SLOW_ON,
	BLINK_MODE_ON,
} BlinkMode_t;

void start_blink(uint8_t count, BlinkMode_t mode);

static inline void blinkFast(uint8_t count) {
    start_blink(count, BLINK_MODE_FAST_OFF);
}

static inline void blinkSlow(uint8_t count) {
    start_blink(count, BLINK_MODE_SLOW_OFF);
}

void constantGlow(void);
void ledNormal(void);
void ledUpdate(void);

#endif /* INC_HAPTIC_H_ */
