/* Freestanding shim: Dhrystone includes <stdio.h> for strcpy/strcmp/printf. */
#ifndef ASTER_DHRY_STDIO_H
#define ASTER_DHRY_STDIO_H
#include <stddef.h>
char *strcpy(char *destination, const char *source);
int   strcmp(const char *left, const char *right);
int   printf(const char *format, ...);
char *malloc(unsigned int size);
#endif
