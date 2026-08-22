"""YAML configuration parser for TEE VM launch.

Parses a YAML config file, validates it against the JSON schema, and outputs
shell variable assignments to stdout for consumption by quick-launch.sh.

Can be invoked as:
  python3 -m chutes_cvm.guest.config <config.yaml>
  python3 -m chutes_cvm.guest.config --benchmark <config.yaml>
"""

import json
import os
import shlex
import sys

import yaml
from chutes_cvm.paths import SCRIPTS_DIR


def validate_config(config, schema_path):
    """Validate config against JSON schema."""
    try:
        import jsonschema
    except ImportError:
        print(
            "Error: jsonschema not installed. Config validation is required.",
            file=sys.stderr,
        )
        print("Install with: pip3 install jsonschema", file=sys.stderr)
        return False

    try:
        with open(schema_path, "r") as f:
            schema = json.load(f)

        jsonschema.validate(instance=config, schema=schema)
        return True
    except jsonschema.ValidationError as e:
        print(f"Config validation error: {e.message}", file=sys.stderr)
        print(f"Path: {' -> '.join(str(p) for p in e.path)}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(f"Error: Schema file not found: {schema_path}", file=sys.stderr)
        print("This is required for config validation.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: Schema validation failed: {e}", file=sys.stderr)
        return False


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    benchmark_mode = False

    if args and args[0] == "--benchmark":
        benchmark_mode = True
        args = args[1:]

    if len(args) != 1:
        print(
            "Usage: python3 -m chutes_cvm.guest.config [--benchmark] <config.yaml>",
            file=sys.stderr,
        )
        sys.exit(1)

    config_file = args[0]

    if not os.path.exists(config_file):
        print(f"Error: Config file not found: {config_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading config file: {e}", file=sys.stderr)
        sys.exit(1)

    schema_name = (
        "config-schema.benchmark.json" if benchmark_mode else "config-schema.json"
    )
    schema_path = os.path.join(str(SCRIPTS_DIR), "config", schema_name)

    if not validate_config(config, schema_path):
        print(
            "\nConfig validation failed. Please fix the errors above.", file=sys.stderr
        )
        print(
            "Validation is required to prevent launching VMs with invalid configuration.",
            file=sys.stderr,
        )
        sys.exit(1)

    vm_config = config.get("vm", {})
    hostname = vm_config.get("hostname", "")
    base_image = vm_config.get("base_image", "")
    vm_image_directory = vm_config.get("vm_image_directory", "")

    miner_ss58 = config.get("miner", {}).get("ss58", "")
    miner_seed = config.get("miner", {}).get("seed", "")

    network = config.get("network", {})
    vm_ip = network.get("vm_ip", "192.168.100.2")
    bridge_ip = network.get("bridge_ip", "192.168.100.1/24")
    vm_dns = network.get("dns", "8.8.8.8")
    public_iface = network.get("public_interface", "")
    network_type = network.get("type", "tap")
    ssh_port = network.get("ssh_port", 2222)

    if "advanced" in config:
        print(
            "Error: 'advanced' section is no longer supported. Remove it to match the current schema.",
            file=sys.stderr,
        )
        sys.exit(1)

    volumes = config.get("volumes", {})
    cache_cfg = volumes.get("cache", {})
    if "enabled" in cache_cfg:
        print(
            "Error: 'volumes.cache.enabled' has been removed. Delete it from your config.",
            file=sys.stderr,
        )
        sys.exit(1)
    cache_size = cache_cfg.get("size", "5000G")
    cache_volume = cache_cfg.get("path", "")

    storage_cfg = volumes.get("storage", {})
    storage_size = storage_cfg.get("size", "500G")
    storage_volume = storage_cfg.get("path", "")

    config_volume = volumes.get("config", {}).get("path", "")

    devices = config.get("devices", {})
    bind_devices = devices.get("bind_devices", True)

    runtime = config.get("runtime", {})
    foreground = runtime.get("foreground", False)

    docker_hub = config.get("docker_hub") or {}
    if not isinstance(docker_hub, dict):
        docker_hub = {}
    docker_hub_username = docker_hub.get("username", "") or ""
    docker_hub_token = docker_hub.get("token", "") or ""

    # RC-gate only: host path to the operator RSA private key. create-config.sh copies
    # it onto the config volume as operator-signing-key.pem (the initramfs rc-sign
    # signs the attestation nonce with it for rc=true measurements).
    rc = config.get("rc") or {}
    if not isinstance(rc, dict):
        rc = {}
    operator_signing_key = rc.get("operator_signing_key", "") or ""

    print(f"HOSTNAME={shlex.quote(hostname)}")
    print(f"BASE_IMAGE={shlex.quote(base_image)}")
    print(f"VM_IMAGE_DIR={shlex.quote(vm_image_directory)}")
    print(f"MINER_SS58={shlex.quote(miner_ss58)}")
    print(f"MINER_SEED={shlex.quote(miner_seed)}")
    print(f"VM_IP={shlex.quote(vm_ip)}")
    print(f"BRIDGE_IP={shlex.quote(bridge_ip)}")
    print(f"VM_DNS={shlex.quote(vm_dns)}")
    print(f"PUBLIC_IFACE={shlex.quote(public_iface)}")
    print(f"NETWORK_TYPE={shlex.quote(network_type)}")
    print(f"SSH_PORT={shlex.quote(str(ssh_port))}")
    print(f"CACHE_SIZE={shlex.quote(cache_size)}")
    print(f"CACHE_VOLUME={shlex.quote(cache_volume)}")
    print(f"STORAGE_SIZE={shlex.quote(storage_size)}")
    print(f"STORAGE_VOLUME={shlex.quote(storage_volume)}")
    print(f"CONFIG_VOLUME={shlex.quote(config_volume)}")
    print(f"SKIP_BIND={'true' if not bind_devices else 'false'}")
    print(f"FOREGROUND={'true' if foreground else 'false'}")
    print(f"DOCKER_HUB_USERNAME={shlex.quote(docker_hub_username)}")
    print(f"DOCKER_HUB_TOKEN={shlex.quote(docker_hub_token)}")
    print(f"OPERATOR_SIGNING_KEY={shlex.quote(operator_signing_key)}")


if __name__ == "__main__":
    main()
