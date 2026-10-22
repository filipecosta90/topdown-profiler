#include <inttypes.h>
#include <libproc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <unistd.h>

__attribute__((noinline)) uint64_t trace_work(uint64_t steps) {
    uint64_t x = 1;
    for (uint64_t i = 0; i < steps; i++) {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
    }
    return x;
}

int main(int argc, char **argv) {
    alarm(5);
    uint64_t steps = 100000;
    if (argc > 2) return 2;
    if (argc == 2) {
        char *end = NULL;
        steps = strtoull(argv[1], &end, 10);
        if (!argv[1][0] || *end || steps > 1000000) return 2;
    }
    usleep(100000);
    struct rusage_info_v4 before = {0}, after = {0};
    if (proc_pid_rusage(getpid(), RUSAGE_INFO_V4, (rusage_info_t *)&before)) return 3;
    uint64_t checksum = trace_work(steps);
    if (proc_pid_rusage(getpid(), RUSAGE_INFO_V4, (rusage_info_t *)&after)) return 3;
    printf("{\"steps\":%" PRIu64 ",\"checksum\":\"%" PRIu64 "\","
           "\"process_accounted_instructions\":%" PRIu64 ",\"process_accounted_cycles\":%" PRIu64 "}\n",
           steps, checksum, after.ri_instructions - before.ri_instructions,
           after.ri_cycles - before.ri_cycles);
    return 0;
}
