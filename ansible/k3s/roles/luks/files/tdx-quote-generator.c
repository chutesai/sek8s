#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <string.h>
#include <errno.h>
#include <sys/socket.h>
#include <linux/vm_sockets.h>
#include <linux/tdx-guest.h>
#include <stdlib.h>
#include <stdint.h>
#include <getopt.h>

// Constants
#define TDX_DEV "/dev/tdx_guest"
#define VSOCK_PORT 4050
#define VMADDR_CID_HOST 2
#define QUOTE_BUFFER_SIZE 8192
#define MAX_USER_DATA_LEN 64

void print_usage(const char* prog_name) {
    printf("Usage: %s [OPTIONS]\n", prog_name);
    printf("Options:\n");
    printf("  -d, --user-data DATA    Include custom user data in quote (max 64 bytes)\n");
    printf("  -o, --output FILE       Output quote to file (default: stdout)\n");
    printf("  -h, --help              Show this help message\n");
    printf("\nGenerates a TDX quote and outputs it in binary format.\n");
}

void print_hex(const char* label, const uint8_t* data, size_t len) {
    fprintf(stderr, "%s: ", label);
    for (size_t i = 0; i < len; i++) {
        fprintf(stderr, "%02x", data[i]);
        if (i % 16 == 15) fprintf(stderr, "\n");
        else if (i % 4 == 3) fprintf(stderr, " ");
    }
    if (len % 16 != 0) fprintf(stderr, "\n");
}

int generate_quote(const char* user_data, const char* output_file) {
    int fd = -1;
    int sock = -1;
    FILE* output = stdout;
    int ret = 1;

    // Open TDX device
    fd = open(TDX_DEV, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "Error: Cannot open %s: %s\n", TDX_DEV, strerror(errno));
        fprintf(stderr, "Make sure you're running in a TDX guest environment.\n");
        return 1;
    }

    // Prepare report data
    struct tdx_report_req req;
    memset(&req, 0, sizeof(req));
    
    // Set user data if provided
    if (user_data) {
        size_t len = strlen(user_data);
        if (len > TDX_REPORTDATA_LEN) {
            fprintf(stderr, "Error: User data too long (%zu bytes, max %d)\n", 
                    len, TDX_REPORTDATA_LEN);
            goto cleanup;
        }
        memcpy(req.reportdata, user_data, len);
        fprintf(stderr, "Including user data: %s\n", user_data);
    } else {
        // Default pattern for testing
        for (int i = 0; i < TDX_REPORTDATA_LEN; i++) {
            req.reportdata[i] = i;
        }
    }

    // Get TDREPORT
    fprintf(stderr, "Generating TDREPORT...\n");
    if (ioctl(fd, TDX_CMD_GET_REPORT0, &req) < 0) {
        fprintf(stderr, "Error: Failed to generate TDREPORT: %s\n", strerror(errno));
        goto cleanup;
    }

    fprintf(stderr, "TDREPORT generated successfully (%d bytes)\n", TDX_REPORT_LEN);
    print_hex("TDREPORT (first 32 bytes)", req.tdreport, 32);

    close(fd);
    fd = -1;

    // Connect to QGS via vsock
    sock = socket(AF_VSOCK, SOCK_STREAM, 0);
    if (sock < 0) {
        fprintf(stderr, "Error: Cannot create vsock: %s\n", strerror(errno));
        goto cleanup;
    }

    struct sockaddr_vm addr;
    memset(&addr, 0, sizeof(addr));
    addr.svm_family = AF_VSOCK;
    addr.svm_cid = VMADDR_CID_HOST;
    addr.svm_port = VSOCK_PORT;

    fprintf(stderr, "Connecting to QGS (port %d)...\n", VSOCK_PORT);
    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        fprintf(stderr, "Error: Cannot connect to QGS: %s\n", strerror(errno));
        fprintf(stderr, "Make sure QGS is running on the host.\n");
        goto cleanup;
    }

    // Send TDREPORT in raw mode
    fprintf(stderr, "Sending TDREPORT to QGS (%d bytes)...\n", TDX_REPORT_LEN);
    ssize_t sent = send(sock, req.tdreport, TDX_REPORT_LEN, 0);
    if (sent != TDX_REPORT_LEN) {
        fprintf(stderr, "Error: Failed to send TDREPORT: sent %zd bytes, expected %d\n", 
                sent, TDX_REPORT_LEN);
        goto cleanup;
    }

    // Receive quote from QGS
    fprintf(stderr, "Waiting for quote response...\n");
    uint8_t buffer[QUOTE_BUFFER_SIZE];
    ssize_t bytes_received = recv(sock, buffer, sizeof(buffer), 0);
    
    if (bytes_received <= 0) {
        fprintf(stderr, "Error: Failed to receive quote: %zd bytes, %s\n", 
                bytes_received, strerror(errno));
        goto cleanup;
    }

    fprintf(stderr, "Received quote: %zd bytes\n", bytes_received);

    // Validate quote size
    if (bytes_received < 1000) {
        fprintf(stderr, "Warning: Quote seems too small (%zd bytes)\n", bytes_received);
        print_hex("Response", buffer, bytes_received < 64 ? bytes_received : 64);
    } else {
        print_hex("Quote (first 32 bytes)", buffer, 32);
    }

    // Open output file if specified
    if (output_file) {
        output = fopen(output_file, "wb");
        if (!output) {
            fprintf(stderr, "Error: Cannot open output file %s: %s\n", 
                    output_file, strerror(errno));
            goto cleanup;
        }
    }

    // Write quote to output
    size_t written = fwrite(buffer, 1, bytes_received, output);
    if (written != (size_t)bytes_received) {
        fprintf(stderr, "Error: Failed to write complete quote: %zu/%zd bytes\n", 
                written, bytes_received);
        goto cleanup;
    }

    if (output_file) {
        fprintf(stderr, "Quote saved to %s\n", output_file);
    }

    ret = 0; // Success

cleanup:
    if (fd >= 0) close(fd);
    if (sock >= 0) close(sock);
    if (output && output != stdout) fclose(output);
    return ret;
}

int main(int argc, char *argv[]) {
    char* user_data = NULL;
    char* output_file = NULL;
    
    static struct option long_options[] = {
        {"user-data", required_argument, 0, 'd'},
        {"output",    required_argument, 0, 'o'},
        {"help",      no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "d:o:h", long_options, NULL)) != -1) {
        switch (opt) {
            case 'd':
                user_data = optarg;
                if (strlen(user_data) > MAX_USER_DATA_LEN) {
                    fprintf(stderr, "Error: User data too long (max %d bytes)\n", 
                            MAX_USER_DATA_LEN);
                    return 1;
                }
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

    return generate_quote(user_data, output_file);
}