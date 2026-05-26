FROM python:3.11-slim

# --- Build time (why `docker compose ... --build` can take 10–20+ minutes) ---
# 1) The next RUN installs a large Debian stack (SDL2+Mesa+X11 *-dev*, build-essential,
#    network-manager for nmcli, audio tools). That step downloads hundreds of MB — mostly
#    network + mirror speed, not Python.
# 2) Docker caches that layer until the RUN text or FROM base changes. The floating tag
#    `python:3.11-slim` can move to a newer Debian release on Docker Hub; when it does,
#    this entire apt layer is rebuilt from scratch (looks like "it used to be 2 minutes").
# 3) Avoid `docker compose build --no-cache` and avoid `--pull` unless you intend to refresh
#    the base image. After one successful build, only changing app files usually hits cache
#    for apt and rebuilds pip/COPY only (~minutes → ~1–3 min typical).

# System dependencies for Kivy GUI on Linux (X11/SDL2 backend):
# SDL2, OpenGL/GLES, X11 client libs, touch input, build tools,
# PortAudio for sounddevice (mic test).
# Notes:
#   libegl1-mesa-dev was removed in Debian Bookworm — EGL is now vendor-neutral.
#   libmtdev1 was renamed to libmtdev1t64 in Bookworm's time_t transition.
#   GStreamer dev libs and libinput-dev are not required for the SDL2 window backend.
#
# Apt: Hash Sum mismatch on .deb files is often mirror/CDN or HTTP pipelining; we clear lists,
# disable pipelining, retry fetches, and re-run update+install up to 3 times.
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt_install() { \
        apt-get install -y --no-install-recommends \
            -o Acquire::Retries=5 \
            -o Acquire::http::Pipeline-Depth=0 \
            "$@"; \
    }; \
    success=0; \
    for attempt in 1 2 3; do \
        rm -rf /var/lib/apt/lists/*; \
        if apt-get update \
            -o Acquire::Retries=5 \
            -o Acquire::http::Pipeline-Depth=0 \
        && apt_install \
            ca-certificates \
            libsdl2-dev \
            libsdl2-image-dev \
            libsdl2-mixer-dev \
            libsdl2-ttf-dev \
            libgl1-mesa-dev \
            libgles2-mesa-dev \
            libx11-dev \
            libxext-dev \
            libxrender-dev \
            libxcursor-dev \
            libxfixes-dev \
            libxi-dev \
            libxrandr-dev \
            libmtdev1t64 \
            libportaudio2 \
            portaudio19-dev \
            libasound2-dev \
            python3-dev \
            libspeexdsp1 \
            build-essential \
            pkg-config \
            xdg-utils \
            xclip \
            xauth \
            x11-xserver-utils \
            fonts-dejavu-core \
            util-linux \
            iproute2 \
            espeak-ng \
            alsa-utils \
            network-manager \
            sudo \
            bluez \
            pulseaudio-utils \
            pipewire \
            wireplumber \
        ; then \
            success=1; \
            break; \
        fi; \
        echo "apt attempt ${attempt} failed (e.g. Hash Sum mismatch); retrying in 8s..." >&2; \
        sleep 8; \
    done; \
    test "$success" -eq 1; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Pull mini-pc/audio/ into /app/audio so device-ui's audio_supervisor can spawn
# audio_capture.py as a child process. ``audio`` is provided via Compose
# ``additional_contexts`` (see ../docker-compose.yml device-ui service).
COPY --from=audio . /app/audio/

RUN chmod +x /app/docker-entrypoint.sh

# Create non-root user with host-matching groups for device access
# `audio` is required for /dev/snd (mic test uses sounddevice → PortAudio → ALSA).
RUN groupadd -g 44 video 2>/dev/null || true \
    && groupadd -g 105 input 2>/dev/null || true \
    && useradd -m -u 1000 -s /bin/bash -G video,input,audio uiuser \
    && chown -R uiuser:uiuser /app \
    && printf '%s\n' 'uiuser ALL=(root) NOPASSWD: /usr/local/bin/meetingbox-host-reboot, /usr/local/bin/meetingbox-host-poweroff, /bin/sh /usr/local/bin/meetingbox-host-reboot, /bin/sh /usr/local/bin/meetingbox-host-poweroff, /bin/sh /usr/local/bin/meetingbox-bluetooth *, /bin/sh /usr/local/bin/meetingbox-timedatectl *, /usr/bin/tee /sys/class/backlight/*/brightness, /usr/bin/tee /sys/class/backlight/*/bl_power, /usr/bin/nmcli, /usr/bin/bluetoothctl, /usr/bin/rfkill, /usr/sbin/rfkill, /sbin/rfkill' > /etc/sudoers.d/meetingbox-host-power \
    && chmod 0440 /etc/sudoers.d/meetingbox-host-power \
    && visudo -cf /etc/sudoers.d/meetingbox-host-power


# Kivy config: use SDL2 window provider, disable console log
ENV KIVY_WINDOW=sdl2
ENV KIVY_NO_CONSOLELOG=1
ENV KIVY_LOG_LEVEL=info
ENV PYTHONUNBUFFERED=1
# Ensure 'screens', 'config', 'api_client', etc. resolve to /app/src
ENV PYTHONPATH=/app/src

USER uiuser

WORKDIR /app/src
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "main.py"]
