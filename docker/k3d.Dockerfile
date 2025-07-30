FROM rancher/k3s:v1.33.1-k3s1 as base

# Set up apk
WORKDIR /build
RUN wget http://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/apk-tools-static-2.14.9-r2.apk && \
    tar -xzf apk-tools-static-2.14.9-r2.apk && \
    cp sbin/apk.static /sbin/apk && \
    chmod +x /sbin/apk && \
    mkdir -p /etc/apk && \
    echo "http://dl-cdn.alpinelinux.org/alpine/v3.22/main" > /etc/apk/repositories && \
    echo "http://dl-cdn.alpinelinux.org/alpine/v3.22/community" >> /etc/apk/repositories && \
    mkdir -p /lib/apk/db && \
    mkdir -p /var/cache/apk && \
    mkdir -p /etc/apk/keys && \
    apk --root / --initdb add --no-cache --allow-untrusted && \
    wget http://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/alpine-keys-2.5-r0.apk && \
    tar -xzf alpine-keys-*.apk && \
    cp etc/apk/keys/* /etc/apk/keys/ 2>/dev/null || true


# Install additional development tools
RUN apk add --no-cache \
    curl \
    jq \
    vim \
    htop \
    bash \
    tcpdump \
    bind-tools

# Install OPA
RUN curl -L -o opa https://openpolicyagent.org/downloads/latest/opa_linux_amd64_static && \
    chmod 755 ./opa && \
    mv opa /usr/local/bin

# Copy custom manifests (auto-deployed at startup)
# COPY custom-manifests/ /var/lib/rancher/k3s/server/manifests/

# Copy custom scripts
# COPY scripts/ /usr/local/bin/

# Copy k3s configuration
COPY config/k3s-config.yml /etc/rancher/k3s/config.yml

# Development environment variables
ENV ENVIRONMENT=development
ENV K3S_KUBECONFIG_MODE=644
ENV DEBUG=true

# Create a welcome message
RUN echo 'echo "🎯 Welcome to your custom k3s development cluster!"' >> /etc/profile



# The official k3s entrypoint handles everything else