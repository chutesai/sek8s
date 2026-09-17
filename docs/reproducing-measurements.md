# Reproducing the Guest Image Measurements

The Chutes guest VM image is built reproducibly: the same source, on any machine, produces an
image with byte-identical TDX measurements. This guide walks through building it yourself and
comparing your result to the measurements Chutes publishes.

You do not need TDX hardware, an NVIDIA GPU, or any credential belonging to Chutes. The build
runs in an ordinary KVM virtual machine, and every input it depends on is public.

Verified on both Intel Xeon and AMD EPYC hosts: four independent builds across two machines in
different datacentres produced identical MRTD, RTMR1, RTMR2 and RTMR3.

## What you will need

A machine with:

- **Ubuntu 24.04**, with `sudo` access
- **KVM available** — `/dev/kvm` must exist. This means bare metal or a VM with nested
  virtualisation enabled. Without it the build falls back to emulation and takes many hours.
- **~150 GB free disk** — a 6.7 GB base image, a ~27 GB intermediate checkpoint, and a ~45 GB
  final image
- **16+ vCPUs and 32+ GB RAM** recommended. A build takes roughly 30 minutes on a 16-core host.
- **Outbound internet** to `vm.chutes.ai`, `snapshot.ubuntu.com`, the Ubuntu archive, NVIDIA's
  CUDA repository and Intel's SGX repository

## The build runs as root

The build must be invoked as **root, with `HOME=/root`**:

```bash
sudo env HOME=/root ansible-playbook ...
```

This is a hard requirement, not a convention. Many of the plays assume root outright, and two
things are resolved from the invoking process's `HOME` rather than the target user's — rustup and
the SSH keypair used to reach the build VM. Running as another user, or with `sudo -E` (which
keeps *your* `HOME`), makes the build look for them in the wrong place.

A preflight check catches the rustup case immediately rather than letting it fail thirty minutes
in, once the GPU and k3s phases have already run. If you see it complain about rustup, this is
almost always why.

For the same reason, install the root-owned prerequisites below as root — not for your login user.

## Preparing the host

Chutes uses `ansible/host/playbooks/build-setup.yml` to provision its own build machines. It
defaults to `build_user: root`, consistent with the above. **You probably do not want to run it.**
It is written for a dedicated build host: it rewrites the machine's apt sources to a benchmarked
mirror, installs Docker, and clones the repository to a path of its choosing. On a machine you use for anything else, that is disruptive — and on a host
that already runs Docker from Docker's own repository, installing `docker.io` will conflict.

Install the prerequisites directly instead:

```bash
sudo apt-get update
sudo apt-get install -y \
    git ansible qemu-utils cpu-checker \
    libvirt-daemon-system virtinst libguestfs-tools \
    genisoimage cloud-image-utils cryptsetup aria2 docker.io

sudo systemctl enable --now libvirtd
```

Two more that are easy to miss, because both are needed **as root** — the build is invoked with
`HOME=/root`, and it looks for them there:

```bash
# rustup — the build compiles the sr25519 initramfs signer from source
sudo env HOME=/root sh -c \
  "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path --default-toolchain none"

# an SSH keypair — Ansible uses it to reach the throwaway build VM
sudo test -f /root/.ssh/id_ed25519 || sudo ssh-keygen -t ed25519 -N '' -f /root/.ssh/id_ed25519
```

Confirm KVM is really available, since this is the difference between 30 minutes and most of a
day:

```bash
sudo kvm-ok     # expects: KVM acceleration can be used
```

## Building

```bash
git clone https://github.com/chutesai/sek8s.git
cd sek8s

# Build the exact release you want to verify
git checkout <tag-or-commit>

cd ansible/guest
sudo env HOME=/root ansible-playbook \
    -i inventory-reproduce.yml \
    playbooks/chutes-miner-vm.yml
```

`inventory-reproduce.yml` is the same production build Chutes runs, with every secret removed. It
needs nothing from you: the LUKS passphrase is a published constant (it does not affect the
measured registers — verified by building with different passphrases and getting identical
results), and the root signing key it bakes in is a *public* key fetched from `vm.chutes.ai`.

`HOME=/root` matters. Do not use `sudo -E`, which preserves your own `HOME` and makes the build
look for rustup in the wrong place.

The playbook pauses once near the start to confirm the build. Press Enter. To run unattended,
redirect stdin from `/dev/null`.

### What fails loudly, and what does not

Most misconfigurations are caught in a preflight play that runs before the build VM is even
started, so you find out in seconds rather than half an hour in:

| Problem | When you find out |
|---|---|
| rustup not at `$HOME/.cargo/bin` | Preflight, naming the path it looked in |
| Base image version or hash unset | Preflight, with the command to build one |
| Root signing key unreachable or malformed | Preflight — it is fetched and validated with `openssl` |
| No SSH keypair for the build VM | Before any download, naming the path and the `ssh-keygen` to run |
| `/dev/kvm` missing | Before any download, rather than silently building under emulation |
| Base image does not match its pinned hash | At download; the fetch is rejected outright |

The KVM check is deliberately a hard failure. Without it the build still *succeeds* and still
produces correct measurements — it just falls back to software emulation and takes hours instead
of minutes, with no symptom other than appearing to hang. Failing up front is more useful than
a correct answer tomorrow.

### What the build does

It downloads a published **base image** — Ubuntu 24.04 plus a fixed set of packages — and builds
on top of it. That layer is pinned by version and SHA-256 in `ansible/guest/BASE_IMAGE_VERSION`
and `BASE_IMAGE_SHA256`, and the download is rejected if the hash does not match.

The base image exists because reproducibility requires pinning the Ubuntu archive, and the archive
itself carries no version pins. It is built separately by `playbooks/base-image.yml` from a dated
Canonical image and a `snapshot.ubuntu.com` timestamp, both recorded in the artifact's
`.provenance` sidecar, so you can rebuild the layer itself from public inputs if you want to go
one level deeper.

If you do, **expect a different SHA-256 and do not treat that as a failure.** qcow2 is a sparse
allocating format: cluster ordering and refcount metadata depend on the order a running VM happens
to write, so two builds of the same packages produce different containers holding the same files.
What reproduces is the contents. Building the layer on an Intel and an AMD host gave 201,694
filesystem entries with 28 differing — all build-time ephemeral (logs, a journal directory named
after a random machine-id, snapd state, apt caches, and the two initrds, since initramfs is not
byte-reproducible) — with an identical package set of 1,477 packages at identical versions, and
none of the differences under a measured path.

So `BASE_IMAGE_SHA256` tells you that you received *our* artifact rather than something else. It is
not the test of whether you rebuilt the layer correctly; the guest measurements matching is.

One input is genuinely unpinned: `/var/lib/ubuntu-advantage/apt-esm/` is fetched live from
`esm.ubuntu.com` and did differ between those two builds. It sits outside the measured paths, so it
cannot move a measurement — but it is a live archive writing into the image, and it is worth knowing
that the reason it is harmless is the path list rather than the pinning.

## Reading your measurements

```bash
cat measurements/<version>/measurements.yaml     # relative to your checkout
```

Four values matter:

| Register | Covers |
|---|---|
| `mrtd` | Initial TD memory — the firmware |
| `rtmr1` | Kernel and boot chain |
| `rtmr2` | Initramfs, which carries the RTMR3 hash manifest |
| `runtime_rtmr3` | The measured filesystem — roughly 49,000 files |

Compare against the published set:

```bash
curl -s https://api.chutes.ai/servers/tee/measurements
```

All four should match for the version you built.

## If they do not match

RTMR3 covers file contents, and RTMR2 moves with it because the RTMR3 hash manifest is baked into
the initramfs. So RTMR2 *and* RTMR3 differing together points at a file difference, not two
problems.

The image records the hash of every file it measures. Diffing that manifest between your image
and a reference names the exact files rather than leaving you to guess:

```bash
sudo virt-cat -a guest-tools/image/prod/<version>/<version>.qcow2 \
    /etc/tdx-rtmr3-expected-hashes > mine.txt
diff mine.txt reference.txt
```

Worth checking first:

- **Did you build the same commit?** The measurements cover the source that ships inside the image.
- **Is `BASE_IMAGE_SHA256` the one for that release?** A different base layer changes most of
  `/usr`, which is the bulk of what RTMR3 measures.
- **Did the build actually complete?** `failed=0` in the `PLAY RECAP`. Three `fatal:` lines
  followed by `...ignoring` are expected — they are probes checking whether CUDA, helm and k3s are
  already installed, before the repositories that provide them are configured.

If you find a genuine difference, please report it — it is a bug in our build, not in your setup.

## What this does and does not prove

It proves the published measurements correspond to the published source: an image attesting to
these values was built from code you can read.

It does not prove the source is free of flaws, and it says nothing about the workloads that run
inside the VM. Workload code is verified separately, by signature at admission, and is not part of
the VM measurements.
