#!/bin/bash
# List available audio input devices inside the meetingbox-audio container.
# Use the index with AUDIO_INPUT_DEVICE_INDEX or the name with AUDIO_INPUT_DEVICE_NAME in .env
#
# Usage: ./scripts/list_audio_devices.sh
# Or:    docker compose exec audio python -c "
#   import pyaudio
#   p = pyaudio.PyAudio()
#   for i in range(p.get_device_count()):
#     info = p.get_device_info_by_index(i)
#     if info.get('maxInputChannels', 0) > 0:
#       print(i, info.get('name', ''))
# "

set -e
cd "$(dirname "$0")/.."

echo "Audio input devices (as seen inside meetingbox-audio container):"
echo "Index  Device name"
echo "-----  ----------"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec audio python -c "
import pyaudio
p = pyaudio.PyAudio()
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    if info.get('maxInputChannels', 0) > 0:
        print(f\"{i:5d}  {info.get('name', '')}\")
"
echo ""
echo "To use device index 1, add to .env:  AUDIO_INPUT_DEVICE_INDEX=1"
echo "To use by name (substring), add:     AUDIO_INPUT_DEVICE_NAME=USB"
