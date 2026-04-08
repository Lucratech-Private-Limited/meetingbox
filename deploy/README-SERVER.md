# Server deploy

Use the **`server/`** package: [server/README.md](../server/README.md)

```bash
cd server
cp .env.example .env
cd frontend && npm ci && npm run build && cd ..
docker compose up -d --build
```
