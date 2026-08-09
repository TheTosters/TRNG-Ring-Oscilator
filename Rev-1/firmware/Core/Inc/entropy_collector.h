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

//Bitmask of user Rings buffers (0-5). Kept in RAM only, use saveConfiguration()
//to make it survive a power cycle.
void useRO(int ringIndex, bool enabled);

//sets value between 1 to 9, this is entropy buffer before SHA.
//Size of buffer is 32xMultiplication given as argument. RAM only, see saveConfiguration().
void setBufferMultiplicity(int multiplicity);

//If use_raw = true, then raw data is passed to usb
void useRawEntropy(bool use_raw);

//Writes the current settings to flash ('s' command)
void saveConfiguration(void);

//Discards the current settings and reloads them from flash ('l' command)
void reloadConfiguration(void);

//Queues the human readable status report and freezes the entropy stream
//('?' / '/' command). Any further byte from the host resumes it.
void requestInfo(void);

//Lifts the freeze set by requestInfo(). Harmless when not frozen.
void resumeTransfer(void);

void collectEntropyBits(uint8_t data);

void updateCollector();

//Boot time load, must be called before the sampling timer is started
void loadConfiguration();

#endif /* INC_ENTROPY_COLLECTOR_H_ */
