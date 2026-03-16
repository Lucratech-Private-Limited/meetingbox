FROM python:3.11-slim

# System dependencies for Kivy GUI on Linux (X11 backend):
# SDL2, OpenGL, X11 client libs, GStreamer, touch input, build tools,
# PortAudio for sounddevice (mic test)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsdl2-dev \
    libsdl2-image-dev \
    libsdl2-mixer-dev \
    libsdl2-ttf-dev \
    libgl1-mesa-dev \
    libgles2-mesa-dev \
    libegl1-mesa-dev \
    libx11-dev \
    libxext-dev \
    libxrender-dev \
    libxcursor-dev \
    libxfixes-dev \
    libxi-dev \
    libxrandr-dev \
    libgstreamer1.0-dev \
    gstreamer1.0-plugins-base \
    libmtdev1 \
    libinput-dev \
    libportaudio2 \
    build-essential \
    pkg-config \
    xdg-utils \
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
    && useradd -m -s /bin/bash -G video,input uiuser \
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
