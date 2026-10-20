#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <os/signpost.h>

__attribute__((noinline)) uint64_t alu_work(uint64_t n) {
    uint64_t x = 1;
    for (uint64_t i = 0; i < n; i++) {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
    }
    return x;
}

int main(void) {
    alarm(8);
    usleep(250000);
    os_log_t log = os_log_create("org.redis-performance.topdown-probe", OS_LOG_CATEGORY_POINTS_OF_INTEREST);
    os_signpost_id_t sid = os_signpost_id_generate(log);
    os_signpost_interval_begin(log, sid, "ALU");
    uint64_t x = alu_work(268435456ULL);
    os_signpost_interval_end(log, sid, "ALU");
    printf("checksum=%llu expected=9087321014236724183\n", (unsigned long long)x);
    return x != 9087321014236724183ULL;
}
