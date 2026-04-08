# Server deploy

Use the **`server/`** package: [server/README.md](../server/README.md)

Build the React app first ([`frontend/README.md`](../frontend/README.md) if it is a separate checkout), then:

```bash
cd server
cp .env.example .env
# Set FRONTEND_DIST to your SPA dist/ (see .env.example)
docker compose up -d --build
```
