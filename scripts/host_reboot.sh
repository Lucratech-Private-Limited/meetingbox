#!/bin/sh
# Reboot the physical host from inside meetingbox-web when Compose uses pid: host.
# Mount as /usr/local/bin/meetingbox-host-reboot and set MEETINGBOX_REBOOT_HELPER (see docker-compose).
set -e
sync
if [ -f /.dockerenv ] && command -v nsenter >/dev/null 2>&1; then
  if nsenter -t 1 -m -u -i -n true 2>/dev/null; then
    exec nsenter -t 1 -m -u -i -n sh -c 'sync; reboot'
  fi
fi
exec reboot
