/*
 * entropy_collector.h
 *
 *  Created on: Jul 29, 2026
 *      Author: toster
 */

#ifndef INC_ENTROPY_COLLECTOR_H_
#define INC_ENTROPY_COLLECTOR_H_

#include <stdint.h>
#include <stdbool.h>

//Bitmask of user Rings buffers (0-5)
void useRO(int ringIndex, bool enabled);

//sets value between 1 to 9, this is entropy buffer before SHA.
//Size of buffer is 32xMultiplication given as argument.
void setBufferMultiplicity(int multiplicity);

//If use_raw = true, then raw data is passed to usb
void useRawEntropy(bool use_raw);

void collectEntropyBits(uint8_t data);

void updateCollector();

void loadConfiguration();

#endif /* INC_ENTROPY_COLLECTOR_H_ */
