#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

// TDX Quote Header (16 bytes)
typedef struct {
    uint16_t version;        // Quote version (e.g., 4 for TDX)
    uint16_t att_key_type;   // Attestation key type (e.g., 2 for ECDSA-256)
    uint32_t att_key_data_0; // Reserved
    uint32_t att_key_data_1; // Reserved
    uint16_t tee_type;       // TEE type (0x81 for TDX)
    uint16_t reserved;       // Reserved
} tdx_quote_header_t;

// TD Report (simplified, relevant fields)
typedef struct {
    uint8_t cpusvn[16];      // CPU Security Version Number
    uint8_t tee_tcb_svn[16]; // TEE TCB SVN
    uint8_t mrseam[48];      // MRTD (Measurement of SEAM module)
    uint8_t mrsigner_seam[48]; // Signer of SEAM module
    uint8_t attributes[8];   // TDX attributes
    uint8_t rtmrs[192];      // RTMR0-RTMR3 (4 x 48 bytes)
} tdx_td_report_t;

void print_hex(uint8_t *data, size_t len, const char *name) {
    printf("%s: ", name);
    for (size_t i = 0; i < len; i++) {
        printf("%02x", data[i]);
    }
    printf("\n");
}

void print_json(uint8_t *mrseam, uint8_t *rtmrs) {
    printf("{\n");
    printf("  \"MRTD\": \"");
    for (size_t i = 0; i < 48; i++) printf("%02x", mrseam[i]);
    printf("\",\n");
    printf("  \"RTMRs\": [\n");
    for (int i = 0; i < 4; i++) {
        printf("    \"RTMR%d\": \"", i);
        for (size_t j = 0; j < 48; j++) printf("%02x", rtmrs[i * 48 + j]);
        printf("\"%s\n", i < 3 ? "," : "");
    }
    printf("  ]\n");
    printf("}\n");
}

int main(int argc, char *argv[]) {
    int json_output = 0;
    if (argc > 1 && strcmp(argv[1], "--json") == 0) {
        json_output = 1;
    }

    FILE *f = fopen("quote.bin", "rb");
    if (!f) {
        fprintf(stderr, "Failed to open quote.bin: %s\n", strerror(errno));
        return 1;
    }

    // Get file size
    fseek(f, 0, SEEK_END);
    size_t size = ftell(f);
    fseek(f, 0, SEEK_SET);

    // Validate size (min: header + TD report = 16 + 584)
    if (size < 600) {
        fprintf(stderr, "Quote file too small (%zu bytes)\n", size);
        fclose(f);
        return 1;
    }

    // Read quote
    uint8_t *quote = malloc(size);
    if (!quote || fread(quote, 1, size, f) != size) {
        fprintf(stderr, "Failed to read quote.bin\n");
        fclose(f);
        free(quote);
        return 1;
    }
    fclose(f);

    // Parse header
    tdx_quote_header_t *header = (tdx_quote_header_t *)quote;
    if (header->version != 4) {
        fprintf(stderr, "Invalid quote: version=%u, tee_type=0x%02x (expected TDX v4)\n",
                header->version, header->tee_type);
        free(quote);
        return 1;
    }

    // Parse TD Report (offset 16)
    tdx_td_report_t *report = (tdx_td_report_t *)(quote + 16);

    // Output MRTD and RTMRs
    if (json_output) {
        print_json(report->mrseam, report->rtmrs);
    } else {
        print_hex(report->mrseam, 48, "MRTD");
        for (int i = 0; i < 4; i++) {
            char name[16];
            snprintf(name, sizeof(name), "RTMR%d", i);
            print_hex(report->rtmrs + i * 48, 48, name);
        }
    }

    free(quote);
    return 0;
}
