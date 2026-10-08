/*
 * tdx-rtmr-extend — Extend a TDX Runtime Measurement Register via sysfs.
 *
 * Usage: tdx-rtmr-extend --index <0-3> --data <96-hex-chars>
 *
 * Writes a 48-byte SHA-384 digest to the tsm-mr sysfs interface:
 *   /sys/class/misc/tdx_guest/measurements/rtmr<N>:sha384
 *
 * The tsm-mr sysfs interface was merged in Linux 6.16 (tag tsm-for-6.16).
 * Ubuntu 24.04 with linux-image-generic-hwe-24.04 (kernel 6.17) supports it.
 *
 * No external libraries required — standard C and a sysfs file write.
 * Compile: gcc -O2 -o tdx-rtmr-extend tdx-rtmr-extend.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <getopt.h>

#define RTMR_DIGEST_LEN  48
#define HEX_LEN          (RTMR_DIGEST_LEN * 2)
#define SYSFS_FMT        "/sys/class/misc/tdx_guest/measurements/rtmr%d:sha384"

static int hex_to_bin(const char *hex, unsigned char *out, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        unsigned int byte;
        if (sscanf(hex + i * 2, "%2x", &byte) != 1)
            return -1;
        out[i] = (unsigned char)byte;
    }
    return 0;
}

int main(int argc, char *argv[])
{
    int index = -1;
    const char *data = NULL;

    static const struct option opts[] = {
        {"index", required_argument, NULL, 'i'},
        {"data",  required_argument, NULL, 'd'},
        {NULL, 0, NULL, 0}
    };

    int c;
    while ((c = getopt_long(argc, argv, "i:d:", opts, NULL)) != -1) {
        switch (c) {
        case 'i': index = atoi(optarg); break;
        case 'd': data  = optarg;       break;
        default:
            fprintf(stderr, "Usage: tdx-rtmr-extend --index <0-3> --data <%d-hex-chars>\n", HEX_LEN);
            return 1;
        }
    }

    if (index < 0 || index > 3 || !data) {
        fprintf(stderr, "Usage: tdx-rtmr-extend --index <0-3> --data <%d-hex-chars>\n", HEX_LEN);
        return 1;
    }

    if ((int)strlen(data) != HEX_LEN) {
        fprintf(stderr, "error: --data must be exactly %d hex characters (SHA-384)\n", HEX_LEN);
        return 1;
    }

    unsigned char digest[RTMR_DIGEST_LEN];
    if (hex_to_bin(data, digest, RTMR_DIGEST_LEN) != 0) {
        fprintf(stderr, "error: invalid hex in --data\n");
        return 1;
    }

    char path[128];
    snprintf(path, sizeof(path), SYSFS_FMT, index);

    int fd = open(path, O_WRONLY);
    if (fd < 0) {
        perror(path);
        return 1;
    }

    ssize_t written = write(fd, digest, RTMR_DIGEST_LEN);
    close(fd);

    if (written != (ssize_t)RTMR_DIGEST_LEN) {
        fprintf(stderr, "error: short write to %s (%zd/%d bytes)\n", path, written, RTMR_DIGEST_LEN);
        return 1;
    }

    return 0;
}
