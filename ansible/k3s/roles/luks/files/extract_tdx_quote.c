#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <ctype.h>

// TDX Quote Header (48 bytes)
typedef struct {
    uint16_t version;        // Quote version (e.g., 4 for TDX)
    uint16_t att_key_type;   // Attestation key type (e.g., 2 for ECDSA-256)
    uint32_t tee_type;       // TEE type (0x00000081 for TDX)
    uint32_t reserved;       // Reserved
    uint8_t qe_vendor_id[16]; // QE Vendor ID
    uint8_t user_data[20];   // User data
} tdx_quote_header_t;

// TD Report field offsets (relative to TD report start, based on Intel TDX specification)
#define TD_REPORT_MRTD_OFFSET           136     // 48 bytes - MR_TD (Trust Domain measurement)
#define TD_REPORT_RTMR0_OFFSET          328     // 48 bytes
#define TD_REPORT_RTMR1_OFFSET          376     // 48 bytes  
#define TD_REPORT_RTMR2_OFFSET          424     // 48 bytes
#define TD_REPORT_RTMR3_OFFSET          472     // 48 bytes
#define TD_REPORT_REPORTDATA_OFFSET     520     // 64 bytes

void print_hex(uint8_t *data, size_t len, const char *name) {
    printf("%s: ", name);
    for (size_t i = 0; i < len; i++) {
        printf("%02X", data[i]);
        if (i % 16 == 15) printf("\n");
        else if (i % 4 == 3) printf(" ");
    }
    if (len % 16 != 0) printf("\n");
}

void print_string(uint8_t *data, size_t len, const char *name) {
    // Check if printable ASCII
    int is_printable = 1;
    size_t text_len = 0;
    for (size_t i = 0; i < len; i++) {
        if (data[i] == 0) break; // Stop at null terminator
        if (!isprint(data[i]) && !isspace(data[i])) {
            is_printable = 0;
            break;
        }
        text_len++;
    }

    if (is_printable && text_len > 0) {
        printf("%s (text): ", name);
        for (size_t i = 0; i < text_len; i++) {
            printf("%c", data[i]);
        }
        printf("\n");
    }
    
    // Always print hex for debugging
    printf("%s (hex): ", name);
    for (size_t i = 0; i < len && data[i] != 0; i++) {
        printf("%02X", data[i]);
    }
    printf("\n");
}

void print_json(uint8_t *reportdata, uint8_t *mrtd, uint8_t *rtmr0, uint8_t *rtmr1, uint8_t *rtmr2, uint8_t *rtmr3) {
    // Check if printable ASCII for nonce
    int is_printable = 1;
    size_t text_len = 0;
    char nonce_str[129]; // Allow for hex representation
    
    for (size_t i = 0; i < 64; i++) {
        if (reportdata[i] == 0) break;
        if (!isprint(reportdata[i]) && !isspace(reportdata[i])) {
            is_printable = 0;
            break;
        }
        text_len++;
    }

    if (is_printable && text_len > 0) {
        strncpy(nonce_str, (char*)reportdata, text_len);
        nonce_str[text_len] = '\0';
    } else {
        text_len = 0;
        for (size_t i = 0; i < 64 && reportdata[i] != 0; i++) {
            text_len += snprintf(nonce_str + text_len, sizeof(nonce_str) - text_len, "%02X", reportdata[i]);
        }
    }

    printf("{\n");
    printf("  \"nonce\": \"%s\",\n", nonce_str);
    printf("  \"MRTD\": \"");
    for (size_t i = 0; i < 48; i++) printf("%02X", mrtd[i]);
    printf("\",\n");
    printf("  \"RTMRs\": {\n");
    printf("    \"RTMR0\": \"");
    for (size_t i = 0; i < 48; i++) printf("%02X", rtmr0[i]);
    printf("\",\n");
    printf("    \"RTMR1\": \"");
    for (size_t i = 0; i < 48; i++) printf("%02X", rtmr1[i]);
    printf("\",\n");
    printf("    \"RTMR2\": \"");
    for (size_t i = 0; i < 48; i++) printf("%02X", rtmr2[i]);
    printf("\",\n");
    printf("    \"RTMR3\": \"");
    for (size_t i = 0; i < 48; i++) printf("%02X", rtmr3[i]);
    printf("\"\n");
    printf("  }\n");
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

    // Validate size (min: header + TD report = 48 + 584)
    if (size < 632) {
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
    if (!json_output) {
        printf("Quote Header: version=%u, tee_type=0x%08x\n", header->version, header->tee_type);
    }
    
    if (header->version != 4) {
        fprintf(stderr, "Invalid quote: version=%u (expected 4)\n", header->version);
        free(quote);
        return 1;
    }

    if (header->tee_type != 0x00000081) {
        fprintf(stderr, "Invalid quote: tee_type=0x%08x (expected 0x00000081 for TDX)\n", header->tee_type);
        free(quote);
        return 1;
    }

    // Parse TD Report (starts at offset 48, is 584 bytes long)
    uint8_t *td_report = quote + 48;
    
    // Extract fields using the actual offsets discovered through analysis
    uint8_t *reportdata = td_report + TD_REPORT_REPORTDATA_OFFSET;  // offset 520
    uint8_t *mrtd = td_report + TD_REPORT_MRTD_OFFSET;              // offset 136  
    uint8_t *rtmr0 = td_report + TD_REPORT_RTMR0_OFFSET;            // offset 328
    uint8_t *rtmr1 = td_report + TD_REPORT_RTMR1_OFFSET;            // offset 376
    uint8_t *rtmr2 = td_report + TD_REPORT_RTMR2_OFFSET;            // offset 424
    uint8_t *rtmr3 = td_report + TD_REPORT_RTMR3_OFFSET;            // offset 472

    // Output results
    if (json_output) {
        print_json(reportdata, mrtd, rtmr0, rtmr1, rtmr2, rtmr3);
    } else {
        print_string(reportdata, 64, "Nonce");
        print_hex(mrtd, 48, "MRTD");
        print_hex(rtmr0, 48, "RTMR0");
        print_hex(rtmr1, 48, "RTMR1");
        print_hex(rtmr2, 48, "RTMR2");
        print_hex(rtmr3, 48, "RTMR3");
    }

    free(quote);
    return 0;
}