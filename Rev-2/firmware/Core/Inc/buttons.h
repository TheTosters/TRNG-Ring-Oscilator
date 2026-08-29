/*
 * buttons.h
 *
 *  Front panel buttons, polled from the main loop.
 */

#ifndef INC_BUTTONS_H_
#define INC_BUTTONS_H_

#include <stdbool.h>

/* Wiring, which does not line up with the silkscreen - check the schematic
 * before changing anything here (RO_hardware.kicad_sch, sheet MCU):
 *
 *   net    MCU pin   .ioc name    button on the board   used for
 *   SW-1   PB13      sw1_Pin      SW1                   self test
 *   SW-3   PB11      sw3_Pin      SW2                   acquisition restart
 *   SW-2   PB12      -            SW3                   NOT WIRED IN FIRMWARE
 *
 * The third button sits on PB12, which the .ioc does not configure at all,
 * while the .ioc does configure PB8 as "sw2_boot" - a pin the schematic leaves
 * unconnected. Adding the third button means adding PB12 in CubeMX; PB8 can go.
 * A side effect worth knowing: nothing is wired to BOOT0, so the board cannot be
 * put into the DFU bootloader by holding a button.
 *
 * All three switches pull their pin up to VCC when pressed, and the internal
 * pull-down holds it low otherwise, so pressed reads as high.
 */

typedef enum {
	BUTTON_SELF_TEST = 0,      //SW1 on the silkscreen, PB13
	BUTTON_RESTART   = 1,      //SW2 on the silkscreen, PB11
	BUTTON_COUNT
} Button_t;

//Polls the pins and debounces them. Call from the main loop; it is cheap and
//needs to run often enough to sample the bounce, which the loop does easily.
void buttonsUpdate(void);

//True exactly once per press, on the debounced press edge.
bool buttonPressed(Button_t button);

#endif /* INC_BUTTONS_H_ */
