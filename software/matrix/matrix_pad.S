// 20.5's code pad (matrix_layout.h, docs/tuning.md §3): MATRIX_PAD_CODE bytes of .text, linked between the
// program's objects and the runtime's, so the runtime's code moves relative to the program's.
#ifndef MATRIX_PAD_CODE
#define MATRIX_PAD_CODE 0
#endif
    .section .text.matrix_pad, "ax"
    .globl __matrix_pad_code
__matrix_pad_code:
#if MATRIX_PAD_CODE > 0
    .skip MATRIX_PAD_CODE
#endif
