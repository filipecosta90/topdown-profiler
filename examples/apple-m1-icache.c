#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#if defined(TOPDOWN_SMALL_ICACHE) && !defined(TOPDOWN_NOP_BLOCK)
#define TOPDOWN_NOP_BLOCK 256
#endif
#ifndef TOPDOWN_NOP_BLOCK
#define TOPDOWN_NOP_BLOCK 65536
#endif
#if TOPDOWN_NOP_BLOCK <= 0
#error TOPDOWN_NOP_BLOCK must be positive
#endif

#ifndef TOPDOWN_NOP_GROUPS
#if 65536 % TOPDOWN_NOP_BLOCK != 0
#error TOPDOWN_NOP_BLOCK must divide 65536 unless TOPDOWN_NOP_GROUPS is set
#endif
#define TOPDOWN_NOP_GROUPS (65536 / TOPDOWN_NOP_BLOCK)
#endif
#if TOPDOWN_NOP_GROUPS <= 0
#error TOPDOWN_NOP_GROUPS must be positive
#endif

#define STRINGIFY_INNER(value) #value
#define STRINGIFY(value) STRINGIFY_INNER(value)

__attribute__((noinline)) static uint64_t icache_stream(uint64_t repetitions) {
    unsigned groups = TOPDOWN_NOP_GROUPS;
    // Prevent clang from duplicating the NOP block when the group count is small.
    __asm__ volatile("" : "+r"(groups));
    for (uint64_t i = 0; i < repetitions; ++i) {
        for (unsigned chunk = 0; chunk < groups; ++chunk) {
            __asm__ volatile(
                ".rept " STRINGIFY(TOPDOWN_NOP_BLOCK) "\n"
                "nop\n"
                ".endr\n");
        }
    }
    return repetitions ^ 0x1cacedULL;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    alarm(30);
    errno = 0;
    char *end;
    uint64_t repetitions = strtoull(argv[1], &end, 10);
    if (argv[1][0] == '-' || errno == ERANGE || !repetitions || *end) return 2;
    printf("%llu\n", (unsigned long long)icache_stream(repetitions));
    return 0;
}
