#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t n = strtoull(argv[1], 0, 10);
    if (!n) return 2;
    alarm(30);
    __asm__ volatile(
        "movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n"
        "movi v4.2d, #0\n movi v5.2d, #0\n movi v6.2d, #0\n movi v7.2d, #0\n"
        "movi v16.2d, #0\n" ::: "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v16");
    for (uint64_t i = 0; i < n; ++i) {
        __asm__ volatile(
            "fadd v0.4s, v0.4s, v16.4s\n fadd v1.4s, v1.4s, v16.4s\n"
            "fadd v2.4s, v2.4s, v16.4s\n fadd v3.4s, v3.4s, v16.4s\n"
            "fadd v4.4s, v4.4s, v16.4s\n fadd v5.4s, v5.4s, v16.4s\n"
            "fadd v6.4s, v6.4s, v16.4s\n fadd v7.4s, v7.4s, v16.4s\n"
            ::: "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v16");
    }
    printf("%llu\n", (unsigned long long)n);
    return 0;
}
