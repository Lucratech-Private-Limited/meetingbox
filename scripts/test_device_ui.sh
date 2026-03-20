#!/bin/bash
# Test Device UI with Mock Backend

echo "Testing MeetingBox Device UI with mock backend..."

cd device-ui/

# Set mock backend environment variable
export MOCK_BACKEND=1
export DEV_MODE=1
export LOG_TO_CONSOLE=1

# Run the UI
python3 src/main.py
