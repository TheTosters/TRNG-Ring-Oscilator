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

//Every command byte must be immediately preceded by this one. The entropy stream
//shares the endpoint, so an unprefixed parser is reprogrammed by any byte that
//gets echoed back to the device - see the comment in CDC_Receive_FS.
#define COMMAND_PREFIX ('!')

//Number of ring oscillators, split into two groups of four - one per latch.
//Group A (latch_a / PB0) holds rings 5..8, group B (latch_b / PA1) rings 1..4.
#define RO_CHANNEL_COUNT (8)

//Bitmask of user Rings buffers (0-7). Kept in RAM only, use saveConfiguration()
//to make it survive a power cycle.
void useRO(int ringIndex, bool enabled);

//Number of entries in the sampling rate table, selected with the 't' command
#define SAMPLING_RATE_COUNT (10)

//Selects the sampling frequency by index into the fixed table in
//entropy_collector.c (0 = 50 kHz, 1 = 100 kHz, 5 = 300 kHz default, ...
//9 = 600 kHz).
//Out of range indices are ignored. RAM only, see saveConfiguration().
void setSamplingRate(int index);

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

//Diagnostics: records every byte the host sent so the '?' report can show what
//actually arrived. '?' and '/' are counted but do not overwrite the last byte,
//so the report shows the command that preceded it.
void noteReceivedByte(uint8_t c);

//Takes a raw GPIOA->IDR read, so the ISR does not have to know how the ring
//outputs are spread over the port.
void collectEntropyBits(uint32_t port_a_idr);

void updateCollector();

//Front panel SW2: stop sampling, throw away every partially collected buffer and
//the health test history, wait a few seconds, then start again from scratch.
//Configuration is untouched - this restarts acquisition, it does not reset settings.
void restartAcquisition(void);

//Front panel SW1: check that all eight rings are alive, for about a second, then
//show a pass/fail verdict on the LEDs. Enables every channel for the duration
//and restores the previous selection afterwards. Ignored while already running.
void startSelfTest(void);

//True while a self test or the restart pause owns the sampling path. The USB
//command handler keeps working; only the entropy stream is suspended.
bool acquisitionSuspended(void);

//Boot time load, must be called before the sampling timer is started
void loadConfiguration();

#endif /* INC_ENTROPY_COLLECTOR_H_ */
