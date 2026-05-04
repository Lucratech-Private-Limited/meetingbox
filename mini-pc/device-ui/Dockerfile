FROM python:3.11-slim

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
            build-essential \
            pkg-config \
            xdg-utils \
            xclip \
            xauth \
            x11-xserver-utils \
            util-linux \
            iproute2 \
            espeak-ng \
            network-manager \
            sudo \
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

RUN chmod +x /app/docker-entrypoint.sh

# Create non-root user with host-matching groups for device access
# `audio` is required for /dev/snd (mic test uses sounddevice → PortAudio → ALSA).
RUN groupadd -g 44 video 2>/dev/null || true \
    && groupadd -g 105 input 2>/dev/null || true \
    && useradd -m -u 1000 -s /bin/bash -G video,input,audio uiuser \
    && chown -R uiuser:uiuser /app \
    && printf '%s\n' 'uiuser ALL=(root) NOPASSWD: /usr/local/bin/meetingbox-host-reboot, /usr/local/bin/meetingbox-host-poweroff, /bin/sh /usr/local/bin/meetingbox-host-reboot, /bin/sh /usr/local/bin/meetingbox-host-poweroff' > /etc/sudoers.d/meetingbox-host-power \
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
