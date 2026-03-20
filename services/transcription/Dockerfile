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

# Download medium model (~1.5GB) — used for all transcription (Telugu/multilingual)
RUN cd /app/whisper.cpp/models && \
  bash -c 'wget -q --show-progress -O ggml-medium.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin' && \
  test -f ggml-medium.bin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "transcription_service.py"]