FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
  git \
  build-essential \
  cmake \
  ffmpeg \
  wget \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install whisper.cpp and build (cmake produces build/bin/whisper-cli)
RUN git clone https://github.com/ggerganov/whisper.cpp.git && \
  cd whisper.cpp && \
  cmake -B build && \
  cmake --build build --config Release

# Download tiny model — fast default for on-device transcription
RUN cd /app/whisper.cpp/models && \
  bash -c 'wget -q --show-progress -O ggml-tiny.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin' && \
  test -f ggml-tiny.bin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "transcription_service.py"]