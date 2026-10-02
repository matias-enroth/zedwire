/*
 * zxsvr - POSIX (Linux, macOS) port of ZXpand-Vitamins' zxsvr.cs serial server
 *
 * Serves a ZX81 .p file to a ZXpand/ZXpand+ over a serial link so that
 * LOAD "$" on the Zeddy can pull it in.
 *
 * Based on zxsvr.cs by Charlie Robson (sirmorris), from
 * https://github.com/charlierobson/ZXpand-Vitamins/tree/master/serial-server
 * This port: MIT license (see LICENSE), written with AI assistance - see README.
 *
 * Protocol (as implemented by the ZXpand firmware, see the original
 * C# source at charlierobson/ZXpand-Vitamins):
 *   'I'        -> reply with 2-byte little-endian file length
 *   'T' n len  -> reply with `len` bytes (0 means 256) starting at
 *                 block n (offset n*256), followed by a 2-byte
 *                 little-endian sum-of-bytes checksum
 *   'X'        -> sign-off; server exits (or re-arms, with -l)
 *
 * Usage: zxsvr [-l|--loop] <file.p> <serial-device>
 *   e.g. zxsvr -l game.p /dev/ttyUSB0
 */

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

#define BAUD              B38400
#define READ_TIMEOUT_DS   4    /* VTIME is in deciseconds: 4 = 400ms, matching the C# ReadTimeout */
#define MAX_CONSEC_ERRORS 20   /* bail out rather than spin a core if the device vanishes */

static int fd = -1;
static struct termios orig_tty;
static int have_orig_tty = 0;

static void restore_and_close(void) {
    if (fd >= 0) {
        if (have_orig_tty) tcsetattr(fd, TCSANOW, &orig_tty);
        close(fd);
        fd = -1;
    }
}

static void on_sigint(int sig) {
    (void)sig;
    restore_and_close();
    _exit(130);
}

static uint8_t *load_file(const char *path, long *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "Could not open '%s': %s\n", path, strerror(errno));
        return NULL;
    }
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long len = ftell(f);
    if (len < 0) { fclose(f); return NULL; }
    rewind(f);

    uint8_t *buf = malloc((size_t)len);
    if (!buf) { fclose(f); return NULL; }

    if (fread(buf, 1, (size_t)len, f) != (size_t)len) {
        fprintf(stderr, "Short read on '%s'\n", path);
        free(buf);
        fclose(f);
        return NULL;
    }
    fclose(f);

    *out_len = len;
    return buf;
}

static int open_serial(const char *device) {
    fd = open(device, O_RDWR | O_NOCTTY);
    if (fd < 0) {
        fprintf(stderr, "Could not open serial port '%s': %s\n", device, strerror(errno));
        return -1;
    }

    if (tcgetattr(fd, &orig_tty) != 0) {
        fprintf(stderr, "tcgetattr failed: %s\n", strerror(errno));
        close(fd);
        fd = -1;
        return -1;
    }
    have_orig_tty = 1;

    struct termios tty = orig_tty;
    cfmakeraw(&tty);
    cfsetispeed(&tty, BAUD);
    cfsetospeed(&tty, BAUD);

    tty.c_cflag |= (CLOCAL | CREAD);
    tty.c_cflag &= ~PARENB;    /* no parity   */
    tty.c_cflag &= ~CSTOPB;    /* 1 stop bit  */
    tty.c_cflag &= ~CSIZE;
    tty.c_cflag |= CS8;        /* 8 data bits */
    tty.c_cflag &= ~CRTSCTS;   /* no hw flow control */

    /* VMIN=0, VTIME=N: read() blocks until either 1 byte arrives or
     * N deciseconds pass with none, returning 0 on timeout - this is
     * the POSIX equivalent of SerialPort.ReadTimeout in the C# original. */
    tty.c_cc[VMIN]  = 0;
    tty.c_cc[VTIME] = READ_TIMEOUT_DS;

    tcflush(fd, TCIOFLUSH);

    if (tcsetattr(fd, TCSANOW, &tty) != 0) {
        fprintf(stderr, "tcsetattr failed: %s\n", strerror(errno));
        close(fd);
        fd = -1;
        return -1;
    }

    return fd;
}

/* Returns 1 with *out set on a byte read, 0 on timeout (mirrors the
 * TimeoutException catch-and-continue in the C# original), or -1 if
 * the port itself has gone bad (e.g. adapter unplugged) and it's time
 * to give up rather than spin retrying forever. */
static int read_byte(uint8_t *out) {
    static int consec_errors = 0;
    ssize_t n = read(fd, out, 1);
    if (n == 1) { consec_errors = 0; return 1; }
    if (n < 0 && errno != EAGAIN && errno != EINTR) {
        fprintf(stderr, "read error: %s\n", strerror(errno));
        if (++consec_errors >= MAX_CONSEC_ERRORS) {
            fprintf(stderr, "Too many consecutive read errors - is the adapter still connected? Giving up.\n");
            return -1;
        }
    }
    return 0;
}

static void write_bytes(const uint8_t *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, buf + off, len - off);
        if (n < 0) {
            if (errno == EINTR) continue;
            fprintf(stderr, "write error: %s\n", strerror(errno));
            return;
        }
        off += (size_t)n;
    }
}

static void send_block(const uint8_t *data, long data_len, long offset, int block_len) {
    uint8_t out[256 + 2];
    unsigned sum = 0;
    for (int i = 0; i < block_len; ++i) {
        long idx = offset + i;
        out[i] = (idx < data_len) ? data[idx] : 0; /* pad past EOF, like reading past array would */
        sum += out[i];
    }
    out[block_len]     = (uint8_t)(sum & 0xff);   /* 2-byte little-endian checksum */
    out[block_len + 1] = (uint8_t)((sum >> 8) & 0xff);
    write_bytes(out, (size_t)block_len + 2);
    printf(" $%04X", sum & 0xffff);
    fflush(stdout);
}

typedef enum { SESSION_OK, SESSION_GARBLED, SESSION_FATAL } session_result_t;

/* Prints a byte the way it travels on the wire: start bit, data bits
 * least significant first, stop bit. */
static void print_wire(const char *label, uint8_t b) {
    printf("   %-14s %02X:  0 |", label, b);
    for (int i = 0; i < 8; ++i) printf(" %d", (b >> i) & 1);
    printf(" | 1\n");
}

/* A stray byte where the sign-off should be: show it against 'X' bit by
 * bit, so a timing slip on the ZXpand side is easy to see. */
static void report_garbled_signoff(uint8_t got) {
    uint8_t diff = got ^ 'X';
    printf("\n   Expected the sign-off 'X' here - the whole file has been sent.\n");
    printf("   On the wire (start | bit 0 ... bit 7 | stop):\n");
    print_wire("expected 'X'", 'X');
    print_wire("received", got);
    printf("   %-14s         ", "");    /* line up under the data bits */
    for (int i = 0; i < 8; ++i) printf(" %c", (diff >> i) & 1 ? '^' : ' ');
    printf("\n");
}

/* After a garbled sign-off more junk often follows (e.g. FF). Read until
 * the line goes quiet so it doesn't leak into the next session. */
static int drain_input(void) {
    uint8_t b;
    int first = 1, r;
    while ((r = read_byte(&b)) == 1) {
        printf(first ? "   followed by: %02X" : " %02X", b);
        first = 0;
    }
    if (!first) printf("\n");
    tcflush(fd, TCIFLUSH);
    return r;           /* 0 = quiet, -1 = port failed */
}

/* Serves one LOAD "$" session: I/T* ... X. Returns SESSION_OK on a clean
 * 'X' sign-off, SESSION_GARBLED when an unknown byte arrives after the
 * whole file has been sent (the load has worked; only the sign-off byte got
 * mangled - see README), and SESSION_FATAL if the port itself failed. */
static session_result_t serve_one_session(const uint8_t *file_data, long file_len) {
    long sent_end = 0;      /* end of the highest block sent so far */
    for (;;) {
        uint8_t b;
        int r = read_byte(&b);
        if (r == -1) return SESSION_FATAL;
        if (r == 0) continue; /* timeout, keep waiting for the next byte */

        uint8_t cmd = b;
        if (cmd >= 0x20 && cmd < 0x7f)
            printf("\n %c [%02X] ", (char)cmd, cmd);
        else
            printf("\n . [%02X] ", cmd);

        if (cmd == 'I') {
            sent_end = 0;
            printf(" -> %ld", file_len);
            uint8_t len_bytes[2] = { (uint8_t)(file_len & 0xff), (uint8_t)((file_len >> 8) & 0xff) };
            write_bytes(len_bytes, 2);
        } else if (cmd == 'T') {
            uint8_t block_num, block_len_b;
            int r1 = read_byte(&block_num);
            if (r1 == -1) return SESSION_FATAL;
            if (r1 == 0) continue;
            int r2 = read_byte(&block_len_b);
            if (r2 == -1) return SESSION_FATAL;
            if (r2 == 0) continue;
            int block_len = block_len_b == 0 ? 256 : block_len_b;

            printf(" %3d, %3d -> ", block_num, block_len);
            send_block(file_data, file_len, (long)block_num * 256, block_len);
            if ((long)block_num * 256 + block_len > sent_end)
                sent_end = (long)block_num * 256 + block_len;
        } else if (cmd == 'X') {
            printf(" -> OK!\n");
            fflush(stdout);
            return SESSION_OK;
        } else if (sent_end >= file_len) {
            printf(" ?");
            report_garbled_signoff(cmd);
            if (drain_input() == -1) return SESSION_FATAL;
            printf("   Treating it as a garbled sign-off. The load itself should be fine.\n");
            fflush(stdout);
            return SESSION_GARBLED;
        } else {
            printf(" ?");
        }
        fflush(stdout);
    }
}

int main(int argc, char **argv) {
    int loop_mode = 0;
    const char *file_path = NULL;
    const char *device_path = NULL;

    int bad_args = 0;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "-l") == 0 || strcmp(argv[i], "--loop") == 0) {
            loop_mode = 1;
        } else if (argv[i][0] == '-') {
            bad_args = 1;           /* -h, --help, or anything unknown */
        } else if (!file_path) {
            file_path = argv[i];
        } else if (!device_path) {
            device_path = argv[i];
        } else {
            bad_args = 1;
        }
    }

    if (bad_args || !file_path || !device_path) {
        fprintf(stderr, "Usage: %s [-l|--loop] <p-file> <serial-device>\n", argv[0]);
        fprintf(stderr, "  -l, --loop   keep serving after each LOAD \"$\" instead of exiting\n");
        fprintf(stderr, "  e.g. %s -l game.p /dev/ttyUSB0\n", argv[0]);
        return 1;
    }

    long file_len = 0;
    uint8_t *file_data = load_file(file_path, &file_len);
    if (!file_data) return 1;
    if (file_len == 0 || file_len > 0xffff) {
        /* the I reply carries the length in 2 bytes */
        fprintf(stderr, "'%s' is %ld bytes; a .p file must be 1 to 65535 bytes.\n",
                file_path, file_len);
        free(file_data);
        return 1;
    }
    printf("%ld bytes read.\n", file_len);

    if (open_serial(device_path) < 0) { free(file_data); return 1; }
    signal(SIGINT, on_sigint);

    printf("Using serial port '%s' 38400,8,N,1\n", device_path);
    printf(loop_mode ? "Server running (loop mode, Ctrl-C to quit).\n" : "Server running.\n");
    fflush(stdout);

    int clean = 0, garbled = 0;
    do {
        session_result_t result = serve_one_session(file_data, file_len);
        if (result == SESSION_FATAL) break;
        if (result == SESSION_OK) ++clean; else ++garbled;
        if (loop_mode)
            printf("\n-- ready for another LOAD \"$\" (sign-offs so far: %d clean, %d garbled) --\n",
                   clean, garbled);
        fflush(stdout);
    } while (loop_mode);

    restore_and_close();
    free(file_data);
    return 0;
}
