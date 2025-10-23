#!/bin/bash
# TLS Certificate Setup Script for Attestation Service (Bare Metal)
# Generates self-signed certificates with network interface detection

set -euo pipefail

# Configuration
CERT_DIR="/etc/attestation-service/certs"
CERT_KEY="${CERT_DIR}/server.key"
CERT_CRT="${CERT_DIR}/server.crt"
CERT_CSR="${CERT_DIR}/server.csr"
OPENSSL_CNF="${CERT_DIR}/openssl.cnf"
SERVICE_USER="tdx-attest"
SERVICE_GROUP="tdx-attest"

# Configuration defaults (can be overridden by environment or config file)
EXCLUDED_INTERFACES="${EXCLUDED_INTERFACES:-docker0,virbr0,veth,br-,kube}"
INCLUDE_PRIVATE_IPS="${INCLUDE_PRIVATE_IPS:-true}"
INCLUDE_IPV6="${INCLUDE_IPV6:-true}"
INCLUDE_PUBLIC_IP="${INCLUDE_PUBLIC_IP:-true}"
PUBLIC_IP_TIMEOUT="${PUBLIC_IP_TIMEOUT:-5}"
CERT_VALIDITY_DAYS="${CERT_VALIDITY_DAYS:-36500}"
FORCE_REGENERATE="${FORCE_REGENERATE:-false}"
ADDITIONAL_HOSTNAMES="${ADDITIONAL_HOSTNAMES:-}"
ADDITIONAL_IPS="${ADDITIONAL_IPS:-}"

# Load config file if it exists
CONFIG_FILE="/etc/attestation-service/cert-config.env"
if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
fi

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/first-boot-attestation-tls.log
}

# Function to check if an interface should be excluded
is_interface_excluded() {
    local interface="$1"
    local excluded_patterns
    IFS=',' read -ra excluded_patterns <<< "$EXCLUDED_INTERFACES"
    
    for pattern in "${excluded_patterns[@]}"; do
        if [[ "$interface" == *"$pattern"* ]]; then
            return 0
        fi
    done
    return 1
}

# Function to check if an IP is private
is_private_ip() {
    local ip="$1"
    
    if [[ "$ip" =~ ^10\. ]] || \
       [[ "$ip" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\. ]] || \
       [[ "$ip" =~ ^192\.168\. ]] || \
       [[ "$ip" =~ ^169\.254\. ]]; then
        return 0
    fi
    return 1
}

# Function to get all active network interfaces
get_active_interfaces() {
    local interfaces=()
    
    while IFS= read -r line; do
        local interface=$(echo "$line" | grep -oP '^\d+: \K[^:@]+')
        local state=$(echo "$line" | grep -oP 'state \K\w+')
        
        if [[ "$interface" != "lo" ]] && [[ "$state" == "UP" ]] && ! is_interface_excluded "$interface"; then
            interfaces+=("$interface")
        fi
    done < <(ip link show | grep -E '^[0-9]+:.*state UP')
    
    printf '%s\n' "${interfaces[@]}"
}

# Function to get all IP addresses from relevant interfaces
get_all_ips() {
    local ips=()
    local interfaces=($(get_active_interfaces))
    
    for interface in "${interfaces[@]}"; do
        # Get IPv4 addresses
        while IFS= read -r ip; do
            if [[ -n "$ip" ]]; then
                if [[ "$INCLUDE_PRIVATE_IPS" == "true" ]] || ! is_private_ip "$ip"; then
                    ips+=("$ip")
                fi
            fi
        done < <(ip -4 addr show "$interface" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || true)
        
        # Get IPv6 addresses if enabled
        if [[ "$INCLUDE_IPV6" == "true" ]]; then
            while IFS= read -r ip; do
                if [[ -n "$ip" ]] && [[ "$ip" != "::1" ]] && [[ ! "$ip" =~ ^fe80: ]]; then
                    ips+=("$ip")
                fi
            done < <(ip -6 addr show "$interface" 2>/dev/null | grep -oP '(?<=inet6\s)[^/\s]+' || true)
        fi
    done
    
    printf '%s\n' "${ips[@]}" | sort -u
}

# Function to get public IP address
get_public_ip() {
    local public_ip=""
    
    # Skip if disabled
    if [[ "$INCLUDE_PUBLIC_IP" != "true" ]]; then
        return 0
    fi
    
    local services=(
        "ifconfig.me"
        "icanhazip.com" 
        "ipecho.net/plain"
        "checkip.amazonaws.com"
    )
    
    for service in "${services[@]}"; do
        public_ip=$(curl -s --max-time "$PUBLIC_IP_TIMEOUT" "$service" 2>/dev/null | grep -oE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$' || true)
        if [[ -n "$public_ip" ]]; then
            log "Detected public IP from $service: $public_ip"
            echo "$public_ip"
            return 0
        fi
    done
    
    log "Warning: Could not detect public IP address"
    return 1
}

# Function to get hostname
get_hostname() {
    local hostname=""
    
    # Try FQDN first
    hostname=$(hostname -f 2>/dev/null || true)
    
    # Fallback to short hostname
    if [[ -z "$hostname" ]]; then
        hostname=$(hostname 2>/dev/null || true)
    fi
    
    # Final fallback
    if [[ -z "$hostname" ]]; then
        hostname="attestation-service"
    fi
    
    echo "$hostname"
}

# Function to display network information
display_network_info() {
    log "=== Network Configuration Summary ==="
    log "Hostname: $(get_hostname)"
    
    local interfaces=($(get_active_interfaces))
    log "Active interfaces: ${interfaces[*]}"
    
    for interface in "${interfaces[@]}"; do
        local ipv4=$(ip -4 addr show "$interface" 2>/dev/null | grep -oP '(?<=inet\s)[^/\s]+' | tr '\n' ' ' || true)
        local ipv6=$(ip -6 addr show "$interface" 2>/dev/null | grep -oP '(?<=inet6\s)[^/\s]+' | grep -v '^fe80:' | tr '\n' ' ' || true)
        log "  $interface: IPv4=[$ipv4] IPv6=[$ipv6]"
    done
    
    local public_ip=$(get_public_ip)
    if [[ -n "$public_ip" ]]; then
        log "Public IP (external): $public_ip"
    else
        log "Public IP: Could not detect"
    fi
    
    log "=================================="
}

# Main certificate generation function
generate_certificates() {
    log "Starting TLS certificate generation for attestation service"
    
    # Display network information
    display_network_info
    
    # Check if certificates already exist and handle force regeneration
    if [[ -f "$CERT_CRT" ]]; then
        if [[ "$FORCE_REGENERATE" == "true" ]]; then
            log "Certificates exist but FORCE_REGENERATE=true, regenerating..."
            rm -f "$CERT_CRT" "$CERT_KEY" "$CERT_CSR"
        else
            log "Certificates already exist at $CERT_CRT, skipping generation"
            return 0
        fi
    fi
    
    # Ensure certificate directory exists
    if [[ ! -d "$CERT_DIR" ]]; then
        log "Creating certificate directory: $CERT_DIR"
        mkdir -p "$CERT_DIR"
        chown root:${SERVICE_GROUP} "$CERT_DIR"
        chmod 750 "$CERT_DIR"
    fi
    
    # Get dynamic values
    local hostname=$(get_hostname)
    local all_ips=($(get_all_ips))
    local public_ip=$(get_public_ip)
    
    log "Certificate will include:"
    log "  Hostname: $hostname"
    log "  Local IPs (${#all_ips[@]}): ${all_ips[*]}"
    if [[ -n "$public_ip" ]]; then
        log "  Public IP: $public_ip"
    fi
    
    # Generate private key
    if [[ ! -f "$CERT_KEY" ]]; then
        log "Generating private key"
        openssl genrsa -out "$CERT_KEY" 2048
    else
        log "Private key already exists"
    fi
    
    # Create OpenSSL configuration with dynamic SANs
    log "Creating OpenSSL configuration with SANs"
    cat > "$OPENSSL_CNF" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = attestation-service

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = attestation-service
DNS.2 = localhost
DNS.3 = $hostname
IP.1 = 127.0.0.1
IP.2 = ::1
EOF

    # Add additional hostnames if specified
    local dns_counter=4
    if [[ -n "$ADDITIONAL_HOSTNAMES" ]]; then
        IFS=',' read -ra additional_hosts <<< "$ADDITIONAL_HOSTNAMES"
        for host in "${additional_hosts[@]}"; do
            host=$(echo "$host" | xargs)
            if [[ -n "$host" ]]; then
                echo "DNS.$dns_counter = $host" >> "$OPENSSL_CNF"
                log "Added additional hostname: $host"
                ((dns_counter++))
            fi
        done
    fi

    # Add all detected local IPs to the certificate
    local ip_counter=3
    for ip in "${all_ips[@]}"; do
        echo "IP.$ip_counter = $ip" >> "$OPENSSL_CNF"
        ((ip_counter++))
    done
    
    # Add public IP if detected and different from local IPs
    if [[ -n "$public_ip" ]] && ! printf '%s\n' "${all_ips[@]}" | grep -Fxq "$public_ip"; then
        echo "IP.$ip_counter = $public_ip" >> "$OPENSSL_CNF"
        log "Added public IP to certificate: $public_ip"
        ((ip_counter++))
    fi
    
    # Add additional IPs if specified
    if [[ -n "$ADDITIONAL_IPS" ]]; then
        IFS=',' read -ra additional_ips <<< "$ADDITIONAL_IPS"
        for ip in "${additional_ips[@]}"; do
            ip=$(echo "$ip" | xargs)
            if [[ -n "$ip" ]]; then
                echo "IP.$ip_counter = $ip" >> "$OPENSSL_CNF"
                log "Added additional IP: $ip"
                ((ip_counter++))
            fi
        done
    fi
    
    # Generate certificate signing request
    log "Generating certificate signing request"
    openssl req -new \
        -key "$CERT_KEY" \
        -out "$CERT_CSR" \
        -config "$OPENSSL_CNF"
    
    # Generate self-signed certificate
    if [[ "$CERT_VALIDITY_DAYS" == "never" ]]; then
        local days=36500
        log "Generating self-signed certificate (valid for ~100 years)"
    else
        local days="$CERT_VALIDITY_DAYS"
        log "Generating self-signed certificate (valid for $days days)"
    fi
    
    openssl x509 -req -days "$days" \
        -in "$CERT_CSR" \
        -signkey "$CERT_KEY" \
        -out "$CERT_CRT" \
        -extensions v3_req \
        -extfile "$OPENSSL_CNF"
    
    # Set proper permissions
    log "Setting certificate permissions"
    chown root:root "$OPENSSL_CNF"
    chmod 644 "$OPENSSL_CNF"
    
    chown ${SERVICE_USER}:${SERVICE_GROUP} "$CERT_KEY" "$CERT_CRT"
    chmod 640 "$CERT_KEY" "$CERT_CRT"
    
    # Clean up CSR
    rm -f "$CERT_CSR"
    
    log "Certificate generation completed successfully"
    
    # Display certificate info
    log "Certificate Subject Alternative Names:"
    openssl x509 -in "$CERT_CRT" -text -noout | grep -A 20 "Subject Alternative Name" | head -20 || true
}

# Function to restart attestation service if it's running
restart_service_if_running() {
    if systemctl is-active --quiet attestation-service 2>/dev/null; then
        log "Restarting attestation-service to use new certificates"
        systemctl restart attestation-service
    else
        log "Attestation service is not running, skipping restart"
    fi
}

# Main execution
main() {
    # Check if running as root
    if [[ $EUID -ne 0 ]]; then
        log "ERROR: This script must be run as root"
        exit 1
    fi
    
    # Check if openssl is available
    if ! command -v openssl >/dev/null 2>&1; then
        log "ERROR: openssl command not found"
        exit 1
    fi
    
    # Check if service user exists
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
        log "ERROR: Service user '$SERVICE_USER' does not exist"
        exit 1
    fi
    
    # Generate certificates
    generate_certificates
    
    # Restart service if needed
    restart_service_if_running
    
    log "TLS certificate setup completed successfully"
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi