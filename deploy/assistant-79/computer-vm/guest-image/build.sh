#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
bash install.sh
# Return to cloud-final before cleaning its state in a separate unit.
systemd-run --no-block --unit=kai-image-seal \
  --property=After=cloud-final.service \
  --property=StandardOutput=tty --property=StandardError=tty \
  --property=TTYPath=/dev/ttyS0 /bin/bash /root/kai-seal-image
