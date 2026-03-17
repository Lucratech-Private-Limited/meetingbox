#!/bin/bash
# ============================================================
# Ollama Entrypoint
# Starts the Ollama server, waits for it to be ready,
# pulls the configured model, and removes any unwanted models.
# ============================================================

set -e

MODEL="${LOCAL_LLM_MODEL:-phi3:mini}"

echo "[ollama-init] Starting Ollama server..."
ollama serve &
SERVER_PID=$!

# Wait for server to be ready using the ollama binary itself (no curl needed).
echo "[ollama-init] Waiting for Ollama server to be ready..."
MAX_RETRIES=30
RETRY=0
until ollama list >/dev/null 2>&1; do
    RETRY=$((RETRY + 1))
    if [ $RETRY -ge $MAX_RETRIES ]; then
        echo "[ollama-init] ERROR: Ollama server did not start in time"
        exit 1
    fi
    sleep 2
done
echo "[ollama-init] Ollama server is ready"

# Remove old/unwanted models to save disk space
echo "[ollama-init] Checking for unwanted models..."
INSTALLED=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' || true)
for m in $INSTALLED; do
    # Keep the configured model, remove everything else
    if [ "$m" != "$MODEL" ] && [ "$m" != "${MODEL}:latest" ]; then
        echo "[ollama-init] Removing unwanted model: $m"
        ollama rm "$m" 2>/dev/null || true
    fi
done

# Pull the configured model if not already present
echo "[ollama-init] Ensuring model '$MODEL' is available..."
if ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "[ollama-init] Model '$MODEL' already downloaded"
else
    echo "[ollama-init] Pulling model '$MODEL' (this may take a few minutes on first run)..."
    ollama pull "$MODEL"
    echo "[ollama-init] Model '$MODEL' ready"
fi

echo "[ollama-init] Setup complete. Ollama server running (PID $SERVER_PID)"

# Keep the server running in the foreground
wait $SERVER_PID
