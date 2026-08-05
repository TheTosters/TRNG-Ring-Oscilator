#ifndef SENDER_H
#define SENDER_H
#include <stdint.h>

typedef enum {
    IDLE,
	PREPARE_TO_SEND,
    SENDING
} SenderState_t;

SenderState_t updateSender();
void sendEntropyToHost(const uint8_t* buffer, uint16_t len);
void onTransmitDone();

#endif // SENDER_H
