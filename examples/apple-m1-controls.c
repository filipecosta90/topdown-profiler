#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static uint64_t rng(uint64_t x) {
    x ^= x << 13;
    x ^= x >> 7;
    return x ^ (x << 17);
}

__attribute__((noinline)) static uint64_t serial(uint64_t n) {
    uint64_t x = 1;
    for (uint64_t i = 0; i < n; ++i) x = x * 1664525 + 1013904223;
    return x;
}

__attribute__((noinline)) static uint64_t parallel(uint64_t n) {
    uint64_t a=1,b=2,c=3,d=4,e=5,f=6,g=7,h=8;
    for (uint64_t i = 0; i < n; ++i) {
        a=a*1664525+1013904223; b=b*1664525+1013904223;
        c=c*1664525+1013904223; d=d*1664525+1013904223;
        e=e*1664525+1013904223; f=f*1664525+1013904223;
        g=g*1664525+1013904223; h=h*1664525+1013904223;
    }
    return a^b^c^d^e^f^g^h;
}

__attribute__((noinline)) static uint64_t alu_wide(uint64_t n) {
    uint64_t a=1,b=2,c=3,d=4,e=5,f=6,g=7,h=8;
    for (uint64_t i = 0; i < n; ++i) {
        __asm__ volatile(
            "add %[a], %[a], #1\n add %[b], %[b], #2\n"
            "add %[c], %[c], #3\n add %[d], %[d], #4\n"
            "add %[e], %[e], #5\n add %[f], %[f], #6\n"
            "add %[g], %[g], #7\n add %[h], %[h], #8\n"
            : [a] "+r"(a), [b] "+r"(b), [c] "+r"(c), [d] "+r"(d),
              [e] "+r"(e), [f] "+r"(f), [g] "+r"(g), [h] "+r"(h));
    }
    return a+b+c+d+e+f+g+h;
}

__attribute__((noinline)) static uint64_t branch(uint64_t n, uint64_t mask) {
    uint64_t x=1234567, acc=0;
    for (uint64_t i = 0; i < n; ++i) {
        x=rng(x);
        uint64_t test=x&mask;
        __asm__ volatile(
            "tbz %w[test], #0, 1f\n"
            "add %[acc], %[acc], #3\n"
            "b 2f\n"
            "1: sub %[acc], %[acc], #5\n"
            "2:\n"
            : [acc] "+r"(acc) : [test] "r"(test) : "cc");
    }
    return acc^x;
}

__attribute__((noinline)) static uint64_t memory(uint64_t n, uint32_t size) {
    uint32_t *order=malloc((size_t)size*sizeof(*order));
    uint32_t *next=malloc((size_t)size*sizeof(*next));
    if (!order || !next) { fputs("allocation failed\n", stderr); exit(2); }
    for (uint32_t i=0;i<size;++i) order[i]=i;
    uint64_t x=1234567;
    for (uint32_t i=size-1;i>0;--i) {
        x=rng(x); uint32_t j=(uint32_t)(x%(i+1));
        uint32_t t=order[i];order[i]=order[j];order[j]=t;
    }
    for (uint32_t i=0;i<size;++i) next[order[i]]=order[(i+1)%size];
    uint32_t cursor=order[0];
    for (uint64_t i=0;i<n;++i) cursor=next[cursor];
    free(next);free(order);
    return cursor;
}

int main(int argc, char **argv) {
    if (argc < 3 || argc > 4) return 2;
    alarm(30);
    uint64_t n=strtoull(argv[2],NULL,10);
    if (!n) return 2;
    uint64_t result;
    if (!strcmp(argv[1],"serial")) result=serial(n);
    else if (!strcmp(argv[1],"parallel")) result=parallel(n);
    else if (!strcmp(argv[1],"alu_wide")) result=alu_wide(n);
    else if (!strcmp(argv[1],"branch_predictable")) result=branch(n,0);
    else if (!strcmp(argv[1],"branch_random")) result=branch(n,UINT64_MAX);
    else if (!strcmp(argv[1],"memory")) {
        uint64_t size=argc == 4 ? strtoull(argv[3],NULL,10) : 8U*1024*1024;
        if (size < 2 || size > 8U*1024*1024) return 2;
        result=memory(n,(uint32_t)size);
    }
    else return 2;
    printf("%llu\n",(unsigned long long)result);
    return 0;
}
