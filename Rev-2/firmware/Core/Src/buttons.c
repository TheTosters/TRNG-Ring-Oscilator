/*
 * buttons.c
 *
 *  Front panel buttons, polled from the main loop.
 */

#include "buttons.h"
#include "main.h"
#include "stm32g4xx_hal.h"

//A tactile switch settles well inside this; long enough to swallow the bounce,
//short enough that a deliberate press is never missed.
#define BUTTON_DEBOUNCE_MS  (25)

typedef struct {
	GPIO_TypeDef* port;
	uint16_t      pin;
	bool          stable;        //debounced level
	bool          last_raw;      //level at the previous poll
	uint32_t      changed_at;    //when last_raw last differed from stable
	bool          press_pending; //press edge waiting to be consumed
} ButtonState_t;

static ButtonState_t buttons[BUTTON_COUNT] = {
	[BUTTON_SELF_TEST] = { .port = sw1_GPIO_Port, .pin = sw1_Pin },
	[BUTTON_RESTART]   = { .port = sw3_GPIO_Port, .pin = sw3_Pin },
};

void buttonsUpdate(void) {
	const uint32_t now = HAL_GetTick();

	for (unsigned i = 0; i < BUTTON_COUNT; i++) {
		ButtonState_t* b = &buttons[i];
		//Switch closes to VCC, internal pull-down holds it low: pressed = high
		const bool raw = (b->port->IDR & b->pin) != 0;

		if (raw != b->last_raw) {
			b->last_raw = raw;
			b->changed_at = now;
			continue;                       //still bouncing, wait it out
		}
		if (raw == b->stable) {
			continue;                       //nothing new
		}
		if ((now - b->changed_at) < BUTTON_DEBOUNCE_MS) {
			continue;                       //held the new level, but not long enough
		}
		b->stable = raw;
		if (raw) {
			b->press_pending = true;        //rising edge = press
		}
	}
}

bool buttonPressed(Button_t button) {
	if (button >= BUTTON_COUNT) {
		return false;
	}
	if (!buttons[button].press_pending) {
		return false;
	}
	buttons[button].press_pending = false;
	return true;
}
