/* Own-source CPU counter probe. Single-threaded, bounded buffers, no ART. */
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define LEASE_NS UINT64_C(5000000000)
static volatile sig_atomic_t stopped;
static uint64_t heartbeat;
static char token[33], directory[128], input[128];
static size_t input_used;
static const char *proc_root = "/proc";
static void stop_signal(int sig) { (void)sig; stopped = 1; }
static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) { stopped = 1; return 0; }
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
static int alive(void) { return !stopped && now_ns() - heartbeat < LEASE_NS; }
static int control(void) {
    char bytes[128];
    for (;;) {
        ssize_t n = read(STDIN_FILENO, bytes, sizeof(bytes));
        if (n < 0) {
            if (errno == EINTR) continue;
            return errno == EAGAIN || errno == EWOULDBLOCK;
        }
        if (!n) return 0;
        for (ssize_t i = 0; i < n; i++) {
            if (bytes[i] == '\n') {
                input[input_used] = 0;
                if (input_used != 37 || memcmp(input, "PING ", 5) || strcmp(input + 5, token)) return 0;
                heartbeat = now_ns(); input_used = 0;
            } else {
                if (input_used >= sizeof(input) - 1) return 0;
                input[input_used++] = bytes[i];
            }
        }
    }
}
/* STOP, EOF, malformed input and broken output all terminate without extra threads. */
static int wait_io(int output, uint64_t deadline) {
    while (alive()) {
        uint64_t now = now_ns(), limit = heartbeat + LEASE_NS;
        if (deadline < limit) limit = deadline;
        if (now >= limit) return alive();
        uint64_t remaining = limit - now;
        struct timespec timeout = {(time_t)(remaining / UINT64_C(1000000000)),
                                   (long)(remaining % UINT64_C(1000000000))};
        struct pollfd fds[2] = {{STDIN_FILENO, POLLIN, 0}, {STDOUT_FILENO, POLLOUT, 0}};
        int rc = ppoll(fds, output ? 2 : 1, &timeout, NULL);
        if (rc < 0) { if (errno == EINTR) continue; return 0; }
        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) { if (!control()) return 0; }
        if (fds[0].revents & POLLNVAL) return 0;
        if (output && (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL))) return 0;
        if (output && (fds[1].revents & POLLOUT)) return alive();
    }
    return 0;
}
static int emit(const char *line) {
    size_t size = strlen(line), sent = 0;
    while (sent < size && alive()) {
        ssize_t n = write(STDOUT_FILENO, line + sent, size - sent);
        if (n > 0) { sent += (size_t)n; continue; }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (!wait_io(1, heartbeat + LEASE_NS)) return 0;
        } else return 0;
    }
    return sent == size;
}
static int read_proc(const char *suffix, char *buffer, size_t capacity) {
    char path[4096];
    int length = snprintf(path, sizeof(path), "%s%s", proc_root, suffix);
    if (length < 0 || (size_t)length >= sizeof(path)) return -1;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
#ifdef PERFCOLLECT_TEST_ROOT
        fprintf(stderr, "open failed: %s errno=%d\n", path, errno);
#endif
        return -1;
    }
    ssize_t n;
    do { n = read(fd, buffer, capacity - 1); } while (n < 0 && errno == EINTR);
    close(fd);
    if (n < 0) return -1;
    buffer[n] = 0;
    return (int)n;
}
static int number(const char *s, const char *end, uint64_t *value) {
    uint64_t v = 0;
    if (s == end) return 0;
    for (; s < end; s++) {
        if (*s < '0' || *s > '9' || v > (UINT64_MAX - (unsigned)(*s - '0')) / 10) return 0;
        v = v * 10 + (unsigned)(*s - '0');
    }
    *value = v; return 1;
}
static int stat_proc(int pid, uint64_t *start, uint64_t *ticks) {
    char suffix[64], buffer[4096], prefix[32];
    snprintf(suffix, sizeof(suffix), "/%d/stat", pid);
    snprintf(prefix, sizeof(prefix), "%d (", pid);
    int n = read_proc(suffix, buffer, sizeof(buffer));
    if (n < 0 || n == (int)sizeof(buffer) - 1 || strncmp(buffer, prefix, strlen(prefix))) return 0;
    char *p = strrchr(buffer, ')');
    if (!p) return 0;
    p++; uint64_t user = 0, system = 0;
    for (int field = 3; field <= 22; field++) {
        while (*p == ' ' || *p == '\t' || *p == '\n') p++;
        char *begin = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\n') p++;
        if (p == begin) return 0;
        if (field == 14 && !number(begin, p, &user)) return 0;
        if (field == 15 && !number(begin, p, &system)) return 0;
        if (field == 22 && !number(begin, p, start)) return 0;
    }
    if (UINT64_MAX - user < system) return 0;
    *ticks = user + system; return 1;
}
static int name_matches(int pid, const unsigned char *expected, size_t length) {
    char suffix[64], buffer[514];
    snprintf(suffix, sizeof(suffix), "/%d/cmdline", pid);
    int n = read_proc(suffix, buffer, length + 2);
    return n >= (int)length + 1 && !buffer[length] && !memcmp(buffer, expected, length);
}
static int stat_system(uint64_t *total, uint64_t *busy) {
    char buffer[4096];
    if (read_proc("/stat", buffer, sizeof(buffer)) < 0 || strncmp(buffer, "cpu ", 4)) return 0;
    char *p = buffer + 4; uint64_t sum = 0, idle = 0; int field = 0;
    while (*p && *p != '\n') {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p || *p == '\n') break;
        char *begin = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\n') p++;
        uint64_t v;
        if (!number(begin, p, &v) || UINT64_MAX - sum < v) return 0;
        sum += v;
        if (field == 3 || field == 4) { if (UINT64_MAX - idle < v) return 0; idle += v; }
        field++;
    }
    /* Match the existing Java protocol: sum the aggregate line fields. */
    if (field < 4 || idle > sum) return 0;
    *total = sum; *busy = sum - idle; return 1;
}
static int hex_digit(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; }
static void cleanup(void) {
    if (!strcmp(proc_root, "/proc")) {
        char path[160]; snprintf(path, sizeof(path), "%s/probe.elf", directory);
        unlink(path); rmdir(directory);
    }
}
int main(int argc, char **argv) {
    if (argc != 6 || strlen(argv[1]) != 32) return 2;
    for (int i = 0; i < 32; i++) if (hex_digit(argv[1][i]) < 0) return 2;
    uint64_t pid_value, period;
    if (!number(argv[2], argv[2] + strlen(argv[2]), &pid_value) || !pid_value || pid_value > INT_MAX
        || !number(argv[4], argv[4] + strlen(argv[4]), &period) || period < 250 || period > 60000) return 2;
    size_t hex_length = strlen(argv[3]);
    if (!hex_length || hex_length > 1024 || hex_length % 2) return 2;
    unsigned char expected[512];
    for (size_t i = 0; i < hex_length / 2; i++) {
        int a = hex_digit(argv[3][2*i]), b = hex_digit(argv[3][2*i+1]);
        if (a < 0 || b < 0 || !(a || b)) return 2;
        expected[i] = (unsigned char)(a * 16 + b);
    }
    memcpy(token, argv[1], 33);
    snprintf(directory, sizeof(directory), "/data/local/tmp/perfcollect_cpu_%s", token);
    if (strcmp(argv[5], directory)) return 2;
#ifdef PERFCOLLECT_TEST_ROOT
    const char *test_root = getenv("PERFCOLLECT_PROC_ROOT");
    if (test_root && *test_root) proc_root = test_root;
#endif
    signal(SIGINT, stop_signal); signal(SIGTERM, stop_signal); signal(SIGPIPE, SIG_IGN);
    if (fcntl(STDIN_FILENO, F_SETFL, fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK) < 0
        || fcntl(STDOUT_FILENO, F_SETFL, fcntl(STDOUT_FILENO, F_GETFL) | O_NONBLOCK) < 0) return 3;
    heartbeat = now_ns(); atexit(cleanup);
    char output[2048];
    snprintf(output, sizeof(output), "{\"v\":1,\"kind\":\"ready\",\"token\":\"%s\",\"seq\":0,\"pid\":%d,\"clock\":\"device_monotonic\",\"device_ns\":\"%" PRIu64 "\",\"implementation\":\"native_c\"}\n", token, (int)pid_value, now_ns());
    if (!emit(output)) return 0;
    uint64_t seq = 0;
    while (alive()) {
        uint64_t started = now_ns(), before, after, ticks, ignored, total, busy;
        int valid = stat_proc((int)pid_value, &before, &ignored)
            && name_matches((int)pid_value, expected, hex_length / 2)
            && stat_system(&total, &busy)
            && stat_proc((int)pid_value, &after, &ticks)
            && name_matches((int)pid_value, expected, hex_length / 2)
            && before == after;
        uint64_t finished = now_ns(); seq++;
        if (!valid) {
#ifdef PERFCOLLECT_TEST_ROOT
            fprintf(stderr, "root=%s stat=%d name=%d sys=%d\n", proc_root,
                    stat_proc((int)pid_value, &before, &ignored),
                    name_matches((int)pid_value, expected, hex_length / 2), stat_system(&total, &busy));
#endif
            snprintf(output, sizeof(output), "{\"v\":1,\"kind\":\"error\",\"token\":\"%s\",\"seq\":%" PRIu64 ",\"reason\":\"read_or_identity_failed\"}\n", token, seq);
            emit(output); break;
        }
        int count = snprintf(output, sizeof(output), "{\"v\":1,\"kind\":\"sample\",\"token\":\"%s\",\"seq\":%" PRIu64 ",\"pid\":%d,\"name_hex\":\"%s\",\"start_ticks\":\"%" PRIu64 "\",\"proc_ticks\":\"%" PRIu64 "\",\"total\":\"%" PRIu64 "\",\"busy\":\"%" PRIu64 "\",\"t0_ns\":\"%" PRIu64 "\",\"t1_ns\":\"%" PRIu64 "\"}\n", token, seq, (int)pid_value, argv[3], after, ticks, total, busy, started, finished);
        if (count < 0 || (size_t)count >= sizeof(output) || !emit(output)) break;
        if (!wait_io(0, started + period * UINT64_C(1000000))) break;
    }
    return 0;
}
