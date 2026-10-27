# Isolated KVM capability probe, not the production Computer provider.
# 79's verified Debian 12 base includes the CA bundle needed by HTTPS apt.
# Elsewhere, supply a verified Debian 12 base with ca-certificates installed.
ARG BASE_IMAGE=kai/base-python:20261026-4766d8b5
FROM ${BASE_IMAGE}
RUN sed -i \
      's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g; s|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      qemu-system-x86 qemu-utils genisoimage openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 28010 kai-vm \
    && useradd -u 28010 -g 28010 -d /nonexistent -s /usr/sbin/nologin kai-vm
# Use a private, owner-readable KVM device inode with the same device number.
# Do not change permissions on the host's global /dev/kvm.
USER 28010:28010
ENTRYPOINT ["qemu-system-x86_64"]
