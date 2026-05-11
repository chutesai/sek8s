/*
 * tdx-rtmr-extend — Extend a TDX Runtime Measurement Register (RTMR).
 *
 * Usage: tdx-rtmr-extend --index <0-3> --data <96-hex-chars>
 *
 * The 96-character hex string encodes 48 bytes (SHA-384 output length).
 * This tool is invoked from the initramfs init-bottom script to extend
 * RTMR3 with hashes of security-sensitive files on the root filesystem.
 *
 * Uses libtdx-attest's tdx_att_extend(), which handles multiple kernel
 * ABIs transparently:
 *   - sysfs attribute  /sys/class/misc/tdx_guest/measurements/rtmr<N>
 *   - ioctl TDX_CMD_EXTEND_RTMR via /dev/tdx_guest (V2 and V3)
 *
 * Kernel support: RTMR extend requires kernel >= ~6.16 (tsm-mr patches) or an
 * Intel-patched kernel with the ioctl backport.  Ubuntu 24.04 linux-image-generic
 * (6.8) does not support it — tdx_att_extend() returns a non-zero error code and
 * this binary exits 1.  The initramfs script that calls this binary is non-fatal,
 * so boot continues normally; RTMR3 remains at its initial all-zeros value.
 *
 * Requires: libtdx-attest (from Intel SGX repo, installed by attestation-service)
 * Compile:  gcc -O2 -o tdx-rtmr-extend tdx-rtmr-extend.c \
 *               -I/usr/include -L/usr/lib/x86_64-linux-gnu -ltdx_attest
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include <tdx_attest.h>

static int hex_to_bin(const char *hex, uint8_t *bin, size_t len)
{
    if (strlen(hex) != len * 2)
        return -1;
    for (size_t i = 0; i < len; i++) {
        unsigned int v;
        if (sscanf(hex + 2 * i, "%2x", &v) != 1)
            return -1;
        bin[i] = (uint8_t)v;
    }
    return 0;
}

int main(int argc, char *argv[])
{
    int opt_index = -1;
    char *data_hex = NULL;

    static struct option opts[] = {
        {"index", required_argument, 0, 'i'},
        {"data",  required_argument, 0, 'd'},
        {0, 0, 0, 0}
    };

    int c;
    while ((c = getopt_long(argc, argv, "i:d:", opts, NULL)) != -1) {
        switch (c) {
        case 'i':
            opt_index = atoi(optarg);
            break;
        case 'd':
            data_hex = optarg;
            break;
        default:
            fprintf(stderr,
                    "Usage: tdx-rtmr-extend --index <0-3> --data <%d-hex-chars>\n",
                    TDX_EXTEND_RTMR_DATA_LEN * 2);
            return 1;
        }
    }

    if (opt_index < 0 || opt_index > 3 || data_hex == NULL) {
        fprintf(stderr,
                "Usage: tdx-rtmr-extend --index <0-3> --data <%d-hex-chars>\n",
                TDX_EXTEND_RTMR_DATA_LEN * 2);
        return 1;
    }

    tdx_rtmr_event_t event;
    memset(&event, 0, sizeof(event));
    event.version     = 1;
    event.rtmr_index  = (uint64_t)opt_index;
    event.event_data_size = 0;

    if (hex_to_bin(data_hex, event.extend_data, TDX_EXTEND_RTMR_DATA_LEN) != 0) {
        fprintf(stderr,
                "Error: --data must be exactly %d hex characters (%d bytes)\n",
                TDX_EXTEND_RTMR_DATA_LEN * 2, TDX_EXTEND_RTMR_DATA_LEN);
        return 1;
    }

    tdx_attest_error_t ret = tdx_att_extend(&event);
    if (ret != TDX_ATTEST_SUCCESS) {
        fprintf(stderr, "tdx_att_extend failed: 0x%X\n", ret);
        return 1;
    }

    return 0;
}
