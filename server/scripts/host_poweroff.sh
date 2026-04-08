#!/bin/sh
# Power off the physical host from inside meetingbox-web (Docker + nsenter).
# Mount as /usr/local/bin/meetingbox-host-poweroff and set MEETINGBOX_POWEROFF_HELPER.
set -e
sync
if [ -f /.dockerenv ] && command -v nsenter >/dev/null 2>&1; then
  if nsenter -t 1 -m -u -i -n true 2>/dev/null; then
    exec nsenter -t 1 -m -u -i -n sh -c 'sync; poweroff'
  fi
fi
exec poweroff
