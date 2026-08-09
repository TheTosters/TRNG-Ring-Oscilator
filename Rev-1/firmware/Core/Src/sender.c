#include "sender.h"
#include "usbd_def.h"
#include "usbd_cdc_if.h"
#include "entropy_collector.h"
#include "haptic.h"

static volatile SenderState_t state = IDLE;

static const uint8_t* usb_tx_buffer;
static uint16_t usb_tx_length;
extern USBD_HandleTypeDef hUsbDeviceFS;

static void startTransmition() {
	state = PREPARE_TO_SEND;
	uint8_t result = CDC_Transmit_FS((uint8_t*)usb_tx_buffer, usb_tx_length);

	if (result == USBD_OK) {
		state = SENDING;
	}
}


inline SenderState_t updateSender() {
	if (state == PREPARE_TO_SEND) {
		startTransmition();
	} else if (state == SENDING) {
		USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef*)hUsbDeviceFS.pClassData;
		if (hcdc->TxState == 0)
		{
		    state = IDLE;
		}
	}
	return state;
}

bool sendEntropyToHost(const uint8_t* buffer, uint16_t len) {
	if (state == IDLE) {
		usb_tx_buffer = buffer;
		usb_tx_length = len;
		startTransmition();
		return true;
	} else {
		//GENERAL ERROR
		blinkFast(2);
		return false;
	}
}
