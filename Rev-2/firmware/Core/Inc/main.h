/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32g4xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define ring_in2_Pin GPIO_PIN_0
#define ring_in2_GPIO_Port GPIOA
#define latch_b_Pin GPIO_PIN_1
#define latch_b_GPIO_Port GPIOA
#define ring_in3_Pin GPIO_PIN_2
#define ring_in3_GPIO_Port GPIOA
#define ring_in4_Pin GPIO_PIN_3
#define ring_in4_GPIO_Port GPIOA
#define ring_in5_Pin GPIO_PIN_4
#define ring_in5_GPIO_Port GPIOA
#define ring_in6_Pin GPIO_PIN_5
#define ring_in6_GPIO_Port GPIOA
#define ring_in7_Pin GPIO_PIN_6
#define ring_in7_GPIO_Port GPIOA
#define ring_in8_Pin GPIO_PIN_7
#define ring_in8_GPIO_Port GPIOA
#define latch_a_Pin GPIO_PIN_0
#define latch_a_GPIO_Port GPIOB
#define sw3_Pin GPIO_PIN_11
#define sw3_GPIO_Port GPIOB
#define sw1_Pin GPIO_PIN_13
#define sw1_GPIO_Port GPIOB
#define ring_in1_Pin GPIO_PIN_8
#define ring_in1_GPIO_Port GPIOA
#define led1_Pin GPIO_PIN_5
#define led1_GPIO_Port GPIOB
#define led2_Pin GPIO_PIN_6
#define led2_GPIO_Port GPIOB
#define led3_Pin GPIO_PIN_7
#define led3_GPIO_Port GPIOB
#define sw2_boot_Pin GPIO_PIN_8
#define sw2_boot_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
