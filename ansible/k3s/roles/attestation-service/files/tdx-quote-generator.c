#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include <tdx_attest.h>

void print_usage(const char *prog_name) {
    printf("Usage: %s [OPTIONS]\n", prog_name);
    printf("Options:\n");
    printf("  -d, --user-data DATA    Include user data in quote (max %d bytes)\n", TDX_REPORT_DATA_SIZE);
    printf("  -x, --hex               Treat user data as hex string\n");
    printf("  -o, --output FILE       Output quote to file (default: quote.bin)\n");
    printf("  -h, --help              Show this help message\n");
}

// Convert hex string to binary
int hex_to_bin(const char *hex, uint8_t *bin, size_t max_len) {
    size_t len = strlen(hex);
    if (len % 2 != 0 || len / 2 > max_len) {
        fprintf(stderr, "Error: Invalid hex string length (%zu, max %zu bytes)\n", len / 2, max_len);
        return -1;
    }
    for (size_t i = 0; i < len / 2; i++) {
        if (sscanf(hex + 2 * i, "%2hhx", &bin[i]) != 1) {
            fprintf(stderr, "Error: Invalid hex character at position %zu\n", i * 2);
            return -1;
        }
    }
    return len / 2;
}

int main(int argc, char *argv[]) {
    char *user_data = NULL;
    char *output_file = "quote.bin";
    int is_hex = 0;

    static struct option long_options[] = {
        {"user-data", required_argument, 0, 'd'},
        {"hex", no_argument, 0, 'x'},
        {"output", required_argument, 0, 'o'},
        {"help", no_argument, 0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "d:xo:h", long_options, NULL)) != -1) {
        switch (opt) {
            case 'd':
                user_data = optarg;
                break;
            case 'x':
                is_hex = 1;
                break;
            case 'o':
                output_file = optarg;
                break;
            case 'h':
                print_usage(argv[0]);
                return 0;
            default:
                print_usage(argv[0]);
                return 1;
        }
    }

    // Initialize report data
    tdx_report_data_t report_data = {0};
    if (user_data) {
        size_t len;
        if (is_hex) {
            len = hex_to_bin(user_data, report_data.d, TDX_REPORT_DATA_SIZE);
            if (len < 0) {
                fprintf(stderr, "Error: Failed to parse hex user data\n");
                return 1;
            }
        } else {
            len = strlen(user_data);
            if (len > TDX_REPORT_DATA_SIZE) {
                fprintf(stderr, "Warning: User data (%zu bytes) truncated to %d bytes\n", len, TDX_REPORT_DATA_SIZE);
                len = TDX_REPORT_DATA_SIZE;
            }
            memcpy(report_data.d, user_data, len);
        }
    }

    // Generate quote
    uint8_t *quote = NULL;
    uint32_t quote_size = 0;
    tdx_uuid_t att_key_id = {0}; // Default: let library select key
    tdx_attest_error_t ret = tdx_att_get_quote(
        &report_data,    // Report data
        NULL, 0,         // No specific attestation key ID list
        &att_key_id,     // Selected key ID (output)
        &quote,          // Quote buffer (output)
        &quote_size,     // Quote size (output)
        0);              // Flags (0 for default behavior)
    if (ret != TDX_ATTEST_SUCCESS) {
        printf("Failed to generate quote: 0x%X\n", ret);
        return 1;
    }

    // Save quote to file
    FILE *f = fopen(output_file, "wb");
    if (!f) {
        printf("Failed to open output file: %s\n", output_file);
        tdx_att_free_quote(quote);
        return 1;
    }
    size_t written = fwrite(quote, 1, quote_size, f);
    if (written != quote_size) {
        printf("Failed to write quote: wrote %zu/%u bytes\n", written, quote_size);
        fclose(f);
        tdx_att_free_quote(quote);
        return 1;
    }
    fclose(f);
    printf("Quote generated: %u bytes, saved to %s\n", quote_size, output_file);

    // Clean up
    tdx_att_free_quote(quote);
    return 0;
}