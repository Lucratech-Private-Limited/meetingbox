FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
  build-essential \
  python3-dev \
  portaudio19-dev \
  libasound2-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m audiouser
USER audiouser

CMD ["python", "audio_capture.py"]

