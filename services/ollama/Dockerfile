FROM ollama/ollama:latest

# No extra system packages needed — the entrypoint uses the ollama
# binary directly for readiness checks, avoiding apt-get issues in
# the ollama/ollama base image.

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
