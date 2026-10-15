#pragma once
#include <cstdio>
#define ESPHOME_LOG_LEVEL_VERBOSE 6
#define ESPHOME_LOG_LEVEL 3
#define ESP_LOGW(tag, ...) (printf("W: " __VA_ARGS__), printf("\n"))
#define ESP_LOGV(tag, ...) ((void)0)
#define ESP_LOGD(tag, ...) ((void)0)
#define ESP_LOGCONFIG(tag, ...) ((void)0)
