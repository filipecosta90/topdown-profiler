#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
static uint64_t background_result;
static void *background(void *arg) {
    pthread_set_qos_class_self_np(QOS_CLASS_BACKGROUND, 0);
    uint64_t n = *(uint64_t *)arg, x = 1234567, acc = 0;
    for (uint64_t i = 0; i < n; ++i) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
        __asm__ volatile(
            "tbz %w[x], #0, 1f\n"
            "add %[acc], %[acc], #3\n b 2f\n"
            "1: sub %[acc], %[acc], #5\n 2:\n"
            : [acc] "+r"(acc) : [x] "r"(x) : "cc");
    }
    background_result = acc ^ x;
    return NULL;
}
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    uint64_t foreground_n = strtoull(argv[1], NULL, 10);
    uint64_t background_n = strtoull(argv[2], NULL, 10);
    if (!foreground_n || !background_n) return 2;
    alarm(30);
    pthread_t thread;
    if (pthread_create(&thread, NULL, background, &background_n)) return 3;
    uint64_t a=1,b=2,c=3,d=4,e=5,f=6,g=7,h=8;
    for (uint64_t i = 0; i < foreground_n; ++i) {
        __asm__ volatile(
            "add %[a], %[a], #1\n add %[b], %[b], #2\n"
            "add %[c], %[c], #3\n add %[d], %[d], #4\n"
            "add %[e], %[e], #5\n add %[f], %[f], #6\n"
            "add %[g], %[g], #7\n add %[h], %[h], #8\n"
            : [a] "+r"(a), [b] "+r"(b), [c] "+r"(c), [d] "+r"(d),
              [e] "+r"(e), [f] "+r"(f), [g] "+r"(g), [h] "+r"(h));
    }
    if (pthread_join(thread, NULL)) return 4;
    printf("%llu\n", (unsigned long long)(a+b+c+d+e+f+g+h+background_result));
    return 0;
}
