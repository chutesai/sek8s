#!/bin/bash
# gpu-operator.sh: Custom boot-time upgrade script for the NVIDIA GPU Operator.
#
# Called by 04-helm-chart-upgrade.sh when the GPU operator version marker does not
# match the installed release. Uses --disable-openapi-validation and
# --set operator.upgradeCRD=true to handle CRD migration safely across major
# chart versions (e.g. v24.9.x -> v26.x) on persistent clusters.
#
# Args:
#   $1 - expected_version (from /etc/chutes/chart-versions/gpu-operator)
#   $2 - installed_version (from helm list)
#
# Inherits KUBECONFIG and HELM_*_HOME from the calling environment
# (set by k3s-cluster-init.service).
set -euo pipefail

expected_version="$1"
installed_version="$2"

echo "GPU operator upgrade: installed=${installed_version} -> expected=${expected_version}"

helm upgrade gpu-operator nvidia/gpu-operator \
    --namespace gpu-operator \
    --version "${expected_version}" \
    --set driver.enabled=false \
    --set toolkit.enabled=false \
    --set operator.upgradeCRD=true \
    --disable-openapi-validation \
    --kubeconfig="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
