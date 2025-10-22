#!/bin/bash
set -e

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> /var/log/first-boot-miner-kubeconfig.log
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Configuration
CERT_NAME="${CERT_NAME:-miner}"
NAMESPACE="${NAMESPACE:-default}"
STORE_AS="${STORE_AS:-secret}" # secret or configmap
CERT_ORGANIZATION="${CERT_ORGANIZATION:-miner}"
TEMP_DIR=$(mktemp -d)

# Public IP detection configuration
INCLUDE_PUBLIC_IP="${INCLUDE_PUBLIC_IP:-true}"
PUBLIC_IP_TIMEOUT="${PUBLIC_IP_TIMEOUT:-5}"
USE_PUBLIC_IP_FOR_API_SERVER="${USE_PUBLIC_IP_FOR_API_SERVER:-true}"

# Set KUBECONFIG for kubectl
log "Starting certificate generation for $CERT_NAME in namespace $NAMESPACE as $STORE_AS..."

# Function to get public IP address (borrowed from first script)
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

# Get node IP (local interface IP)
log "Determining node IP..."
NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
if [ -z "$NODE_IP" ]; then
    log "Failed to determine node IP, falling back to localhost"
    NODE_IP="127.0.0.1"
fi
log "Local node IP set to $NODE_IP"

# Get public IP
log "Detecting public IP..."
PUBLIC_IP=$(get_public_ip)
if [[ -n "$PUBLIC_IP" ]]; then
    log "Public IP detected: $PUBLIC_IP"
    
    # Decide which IP to use for API server endpoint
    if [[ "$USE_PUBLIC_IP_FOR_API_SERVER" == "true" ]]; then
        API_SERVER_IP="$PUBLIC_IP"
        log "Using public IP for API server endpoint"
    else
        API_SERVER_IP="$NODE_IP"
        log "Using local IP for API server endpoint (default)"
    fi
else
    log "No public IP detected, using local IP for API server endpoint"
    API_SERVER_IP="$NODE_IP"
fi

# Check if kubeconfig Secret or ConfigMap already exists
log "Checking for existing $STORE_AS $CERT_NAME-kubeconfig in namespace $NAMESPACE..."
if kubectl get "$STORE_AS" "$CERT_NAME-kubeconfig" -n "$NAMESPACE" >/dev/null 2>&1; then
    log "$STORE_AS $CERT_NAME-kubeconfig already exists in namespace $NAMESPACE, skipping certificate generation"
    exit 0
fi

# Generate private key
log "Generating private key..."
openssl genrsa -out "$TEMP_DIR/$CERT_NAME.key" 2048

# Create OpenSSL configuration with Subject Alternative Names including public IP
log "Creating OpenSSL configuration with SANs..."
cat > "$TEMP_DIR/$CERT_NAME.conf" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = $CERT_NAME
O = $CERT_ORGANIZATION

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $CERT_NAME
DNS.2 = localhost
DNS.3 = $(hostname)
IP.1 = 127.0.0.1
IP.2 = $NODE_IP
EOF

# Add public IP to certificate if detected and different from local IP
if [[ -n "$PUBLIC_IP" ]] && [[ "$PUBLIC_IP" != "$NODE_IP" ]]; then
    echo "IP.3 = $PUBLIC_IP" >> "$TEMP_DIR/$CERT_NAME.conf"
    log "Added public IP to certificate SANs: $PUBLIC_IP"
fi

# Generate certificate signing request with SAN configuration
log "Generating certificate signing request with SANs..."
openssl req -new -key "$TEMP_DIR/$CERT_NAME.key" -out "$TEMP_DIR/$CERT_NAME.csr" -config "$TEMP_DIR/$CERT_NAME.conf"

# Encode CSR to base64
CSR_CONTENT=$(cat "$TEMP_DIR/$CERT_NAME.csr" | base64 | tr -d '\n')

# Apply CSR to Kubernetes
log "Applying CSR to Kubernetes..."
cat <<EOF | kubectl apply -f -
apiVersion: certificates.k8s.io/v1
kind: CertificateSigningRequest
metadata:
  name: $CERT_NAME-csr
spec:
  request: $CSR_CONTENT
  signerName: kubernetes.io/kube-apiserver-client
  usages:
  - client auth
EOF

# Approve CSR
log "Approving CSR $CERT_NAME-csr..."
kubectl certificate approve "$CERT_NAME-csr"

# Wait for certificate to be issued (up to 60 seconds)
log "Waiting for certificate to be issued..."
for i in {1..12}; do
    CERT_DATA=$(kubectl get csr "$CERT_NAME-csr" -o jsonpath='{.status.certificate}' 2>/dev/null)
    if [ -n "$CERT_DATA" ]; then
        log "Certificate issued"
        break
    fi
    sleep 5
done
if [ -z "$CERT_DATA" ]; then
    log "Error: Certificate not issued after 60 seconds"
    exit 1
fi

# Decode and save certificate
log "Saving certificate..."
echo "$CERT_DATA" | base64 -d > "$TEMP_DIR/$CERT_NAME.crt"

# Display certificate SANs for verification
log "Certificate Subject Alternative Names:"
openssl x509 -in "$TEMP_DIR/$CERT_NAME.crt" -text -noout | grep -A 10 "Subject Alternative Name" | head -10 || log "Could not display SANs"

# Get cluster CA data
log "Getting cluster CA data..."
CLUSTER_CA=$(kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')

# Get API server URL with chosen IP
log "Getting API server URL..."
API_SERVER_PORT=$(kubectl config view --raw -o jsonpath='{.clusters[0].cluster.server}' | grep -oE ':[0-9]+$' | cut -d: -f2)
API_SERVER="https://$API_SERVER_IP:$API_SERVER_PORT"
log "API server endpoint: $API_SERVER"

# Encode certificate and private key
CERT_CONTENT=$(cat "$TEMP_DIR/$CERT_NAME.crt" | base64 | tr -d '\n')
KEY_CONTENT=$(cat "$TEMP_DIR/$CERT_NAME.key" | base64 | tr -d '\n')

# Create kubeconfig content
log "Creating kubeconfig content..."
NODE_NAME=$(hostname)
KUBECONFIG_CONTENT=$(cat <<EOF
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: $CLUSTER_CA
    server: $API_SERVER
  name: $NODE_NAME
contexts:
- context:
    cluster: $NODE_NAME
    user: $NODE_NAME
    namespace: $NAMESPACE
  name: $NODE_NAME
current-context: $NODE_NAME
users:
- name: $NODE_NAME
  user:
    client-certificate-data: $CERT_CONTENT
    client-key-data: $KEY_CONTENT
EOF
)

# Store kubeconfig as Secret or ConfigMap
log "Storing kubeconfig as $STORE_AS in namespace $NAMESPACE..."
if [ "$STORE_AS" = "secret" ]; then
    kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: $CERT_NAME-kubeconfig
  namespace: $NAMESPACE
type: Opaque
data:
  kubeconfig: $(echo "$KUBECONFIG_CONTENT" | base64 | tr -d '\n')
EOF
elif [ "$STORE_AS" = "configmap" ]; then
    kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: $CERT_NAME-kubeconfig
  namespace: $NAMESPACE
data:
  kubeconfig: |
$(echo "$KUBECONFIG_CONTENT" | sed 's/^/    /')
EOF
else
    log "Error: Invalid STORE_AS value ($STORE_AS), must be 'secret' or 'configmap'"
    exit 1
fi

# Clean up temporary files
log "Cleaning up temporary files..."
rm -rf "$TEMP_DIR"

# Clean up CSR
log "Cleaning up CSR $CERT_NAME-csr..."
kubectl delete csr "$CERT_NAME-csr" >/dev/null 2>&1 || true

log "Certificate generation completed successfully! Kubeconfig stored as $STORE_AS $CERT_NAME-kubeconfig in namespace $NAMESPACE"

# Summary of network configuration
log "=== Network Configuration Summary ==="
log "Local IP: $NODE_IP"
if [[ -n "$PUBLIC_IP" ]]; then
    log "Public IP: $PUBLIC_IP"
    log "Certificate includes both local and public IPs"
else
    log "Public IP: Not detected"
    log "Certificate includes only local IP"
fi
log "API Server Endpoint: $API_SERVER"
log "======================================="

# Instructions for retrieving kubeconfig
if [ "$STORE_AS" = "secret" ]; then
    log "To retrieve kubeconfig: kubectl get secret $CERT_NAME-kubeconfig -n $NAMESPACE -o jsonpath='{.data.kubeconfig}' | base64 -d > $CERT_NAME-kubeconfig.yaml"
else
    log "To retrieve kubeconfig: kubectl get configmap $CERT_NAME-kubeconfig -n $NAMESPACE -o jsonpath='{.data.kubeconfig}' > $CERT_NAME-kubeconfig.yaml"
fi

log "Miner kubeconfig setup completed."