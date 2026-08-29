################################################################################
# Automatically-generated file. Do not edit!
# Toolchain: GNU Tools for STM32 (14.3.rel1)
################################################################################

# Add inputs and outputs from these tool invocations to the build variables 
C_SRCS += \
../Core/Src/tinycrypt/sha256.c \
../Core/Src/tinycrypt/utils.c 

OBJS += \
./Core/Src/tinycrypt/sha256.o \
./Core/Src/tinycrypt/utils.o 

C_DEPS += \
./Core/Src/tinycrypt/sha256.d \
./Core/Src/tinycrypt/utils.d 


# Each subdirectory must supply rules for building sources it contributes
Core/Src/tinycrypt/%.o Core/Src/tinycrypt/%.su Core/Src/tinycrypt/%.cyclo: ../Core/Src/tinycrypt/%.c Core/Src/tinycrypt/subdir.mk
	arm-none-eabi-gcc "$<" -mcpu=cortex-m4 -std=gnu11 -DUSE_HAL_DRIVER -DSTM32G431xx -c -I../Core/Inc -I../Drivers/STM32G4xx_HAL_Driver/Inc -I../Drivers/STM32G4xx_HAL_Driver/Inc/Legacy -I../Drivers/CMSIS/Device/ST/STM32G4xx/Include -I../Drivers/CMSIS/Include -I../USB_Device/App -I../USB_Device/Target -I../Middlewares/ST/STM32_USB_Device_Library/Core/Inc -I../Middlewares/ST/STM32_USB_Device_Library/Class/CDC/Inc -Os -ffunction-sections -fdata-sections -Wall -fstack-usage -fcyclomatic-complexity -MMD -MP -MF"$(@:%.o=%.d)" -MT"$@" --specs=nano.specs -mfpu=fpv4-sp-d16 -mfloat-abi=hard -mthumb -o "$@"

clean: clean-Core-2f-Src-2f-tinycrypt

clean-Core-2f-Src-2f-tinycrypt:
	-$(RM) ./Core/Src/tinycrypt/sha256.cyclo ./Core/Src/tinycrypt/sha256.d ./Core/Src/tinycrypt/sha256.o ./Core/Src/tinycrypt/sha256.su ./Core/Src/tinycrypt/utils.cyclo ./Core/Src/tinycrypt/utils.d ./Core/Src/tinycrypt/utils.o ./Core/Src/tinycrypt/utils.su

.PHONY: clean-Core-2f-Src-2f-tinycrypt

