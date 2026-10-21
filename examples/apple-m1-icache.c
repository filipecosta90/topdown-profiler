#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifdef TOPDOWN_SMALL_ICACHE
#define NOP_BLOCK 256
#define NOP_GROUPS 256
#else
#define NOP_BLOCK 65536
#define NOP_GROUPS 1
#endif

#define STRINGIFY_INNER(value) #value
#define STRINGIFY(value) STRINGIFY_INNER(value)

__attribute__((noinline)) static uint64_t icache_stream(uint64_t repetitions) {
    for (uint64_t i = 0; i < repetitions; ++i) {
        for (unsigned chunk = 0; chunk < NOP_GROUPS; ++chunk) {
            __asm__ volatile(
                ".rept " STRINGIFY(NOP_BLOCK) "\n"
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
