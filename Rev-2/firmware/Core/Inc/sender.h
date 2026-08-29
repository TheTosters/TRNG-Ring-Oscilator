#ifndef SENDER_H
#define SENDER_H
#include <stdint.h>
#include <stdbool.h>

typedef enum {
    IDLE,
	PREPARE_TO_SEND,
    SENDING
} SenderState_t;

SenderState_t updateSender();
bool sendEntropyToHost(const uint8_t* buffer, uint16_t len);

#endif // SENDER_H
