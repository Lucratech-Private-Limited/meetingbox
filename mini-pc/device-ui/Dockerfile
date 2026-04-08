FROM python:3.11-slim

# System dependencies for Kivy GUI on Linux (X11/SDL2 backend):
# SDL2, OpenGL/GLES, X11 client libs, touch input, build tools,
# PortAudio for sounddevice (mic test).
# Notes:
#   libegl1-mesa-dev was removed in Debian Bookworm — EGL is now vendor-neutral.
#   libmtdev1 was renamed to libmtdev1t64 in Bookworm's time_t transition.
#   GStreamer dev libs and libinput-dev are not required for the SDL2 window backend.
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user with host-matching groups for device access
RUN groupadd -g 44 video 2>/dev/null || true \
    && groupadd -g 105 input 2>/dev/null || true \
    && useradd -m -u 1000 -s /bin/bash -G video,input uiuser \
    && chown -R uiuser:uiuser /app

# Kivy config: use SDL2 window provider, disable console log
ENV KIVY_WINDOW=sdl2
ENV KIVY_NO_CONSOLELOG=1
ENV KIVY_LOG_LEVEL=info
ENV PYTHONUNBUFFERED=1
# Ensure 'screens', 'config', 'api_client', etc. resolve to /app/src
ENV PYTHONPATH=/app/src

USER uiuser

WORKDIR /app/src
CMD ["python", "main.py"]
