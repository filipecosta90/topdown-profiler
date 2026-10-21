#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifndef NOP_COUNT
#define NOP_COUNT 64
#endif
#define STRINGIFY_INNER(x) #x
#define STRINGIFY(x) STRINGIFY_INNER(x)

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10), x = 1234567, acc = 0;
    if (!n) return 2;
    alarm(30);
    for (uint64_t i = 0; i < n; ++i) {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        __asm__ volatile(
            "tbz %w[x], #0, 1f\n"
            "add %[acc], %[acc], #3\n add %[acc], %[acc], #5\n"
            "b 2f\n"
            "1: sub %[acc], %[acc], #7\n sub %[acc], %[acc], #11\n"
            "2: .rept " STRINGIFY(NOP_COUNT) "\n nop\n .endr\n"
            : [acc] "+r"(acc) : [x] "r"(x) : "cc");
    }
    printf("%llu\n", (unsigned long long)(acc ^ x));
    return 0;
}
