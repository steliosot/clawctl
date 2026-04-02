# openclaw-k (Containerized OpenClaw Manager)

Simple manager that creates one OpenClaw Docker container per user.

## What You Get

- Manager API in Docker (`http://127.0.0.1:8080`)
- Python CLI (`cli.py`) to create/list/delete user containers
- One user = one OpenClaw container + one port
- Token auth mode by default

## 1) Start the manager (Dockerized)

```bash
# from repo root
docker compose up -d --build
```

Check:

```bash
curl http://127.0.0.1:8080/healthz
```

Expected:

```json
{"status":"ok"}
```

## 2) Install and Use the CLI

Install from GitHub (recommended):

```bash
pip install "git+https://github.com/steliosot/openclaw-k.git"
```

Then point CLI to manager API:

```bash
export OPENCLAW_MANAGER_API=http://127.0.0.1:8080
```

Use:

```bash
openclaw-k create --user alice --port 20030
openclaw-k info --user alice
openclaw-k list
```

Local dev option (from repo):

```bash
# from repo root
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set manager API:

```bash
export OPENCLAW_MANAGER_API=http://127.0.0.1:8080
```

Create user container:

```bash
openclaw-k create --user alice --port 20030
```

Show user info:

```bash
openclaw-k info --user alice
```

List all:

```bash
openclaw-k list
```

Restart/delete:

```bash
openclaw-k restart --user alice
openclaw-k delete --user alice
```

## 3) Run Ollama Locally (Optional)

Start Ollama in Docker on port `11434`:

```bash
docker run -d \
  --name ollama \
  -p 11434:11434 \
  -v ollama:/root/.ollama \
  ollama/ollama:latest
```

Pull models you want to use:

```bash
docker exec -it ollama ollama pull mistral
docker exec -it ollama ollama pull codellama:7b
```

Quick check:

```bash
curl http://127.0.0.1:11434/api/tags
```

## 4) Recommended Ollama Models (Tool-Capable)

Use tool-capable models for OpenClaw agent workflows.

| Model | Size | Good for |
|---|---:|---|
| `phi4-mini:latest` | 2.5GB | Lightweight, fast local testing |
| `mistral:latest` | 4.4GB | Strong default general-purpose choice |
| `qwen2.5:7b` | 4.7GB | Balanced reasoning + coding |
| `qwen2.5-coder:7b` | 4.7GB | Coding-focused tasks |
| `llama3.1:8b` | 4.9GB | General assistant with long context |

Pull examples:

```bash
docker exec -it ollama ollama pull phi4-mini:latest
docker exec -it ollama ollama pull mistral:latest
docker exec -it ollama ollama pull qwen2.5:7b
docker exec -it ollama ollama pull qwen2.5-coder:7b
docker exec -it ollama ollama pull llama3.1:8b
```

Important:

- `codellama:7b` is completion-only in Ollama and does not support tools for OpenClaw agent mode.
- If you pass a model tag that Ollama does not expose directly (for example `qwen2.5:7b` on some installs), the manager auto-resolves it to an available local alias (usually `qwen2.5:latest`).
- If unsure, verify capabilities with:

```bash
docker exec -it ollama ollama show <model>
```

Check `Capabilities` includes `tools`.

## 5) Create Instances (Default vs Ollama)

Default OpenClaw behavior (no provider override):

```bash
openclaw-k create --user bob-default --port 21003
```

Ollama with default model (`mistral`):

```bash
openclaw-k create --user bob-ollama --port 21004 --provider ollama
```

Ollama with explicit model:

```bash
openclaw-k create --user bob-code --port 21005 --provider ollama --model codellama:7b
```

Show instance details:

```bash
openclaw-k info --user bob-code
openclaw-k list
```

## 6) Use the API Programmatically (Python)

Install once:

```bash
pip install requests
```

Example 1: create a user instance

```python
import requests

BASE = "http://127.0.0.1:8080"

r = requests.post(
    f"{BASE}/instances",
    json={"user_id": "alice", "port": 20030},
    timeout=20,
)
r.raise_for_status()
data = r.json()
print("URL:", data["url"])
print("Token:", data.get("token"))
```

Example output:

```text
URL: http://127.0.0.1:20030
Token: YOUR_TOKEN_HERE
```

Example 2: get one instance and list all

```python
import requests
from pprint import pprint

BASE = "http://127.0.0.1:8080"

one = requests.get(f"{BASE}/instances/alice", timeout=20)
one.raise_for_status()
pprint(one.json())

all_instances = requests.get(f"{BASE}/instances", timeout=20)
all_instances.raise_for_status()
pprint(all_instances.json())
```

## 7) Login as the user

From `openclaw-k info --user alice`, use:

- `url` (example: `http://127.0.0.1:20030`)
- `token`

Open URL in browser and authenticate with token.

## 8) Clean reset (optional)

Delete all manager-created user containers and volumes:

```bash
docker ps -a --filter label=managed-by=openclaw-manager --format '{{.Names}}' | xargs -r docker rm -f
docker volume ls -q --filter label=managed-by=openclaw-manager | xargs -r docker volume rm
```

Clear manager state:

```bash
# from repo root
rm -rf data/users
mkdir -p data/users
printf '{}\n' > data/instances.json
```

## 9) Command Reference (with sample output)

`python cli.py create --user alice --port 20030`

Sample output:

```json
{
  "container_name": "openclaw-user-alice",
  "port": 20030,
  "status": "running",
  "token": "YOUR_TOKEN_HERE",
  "url": "http://127.0.0.1:20030",
  "user_id": "alice"
}
```

`python cli.py create --user alice --port 20031 --provider ollama --model codellama:7b`

Sample output:

```json
{
  "auth_mode": "token",
  "container_name": "openclaw-alice",
  "host_port": 20031,
  "llm_base_url": "http://host.docker.internal:11434",
  "llm_model": "codellama:7b",
  "provider": "ollama",
  "status": "running",
  "token": "YOUR_TOKEN_HERE",
  "url": "http://127.0.0.1:20031",
  "user_id": "alice"
}
```

`python cli.py info --user alice`

Sample output:

```json
{
  "container_name": "openclaw-user-alice",
  "port": 20030,
  "status": "running",
  "token": "YOUR_TOKEN_HERE",
  "url": "http://127.0.0.1:20030",
  "user_id": "alice"
}
```

`python cli.py list`

Sample output:

```json
[
  {
    "container_name": "openclaw-user-alice",
    "port": 20030,
    "status": "running",
    "token": "YOUR_TOKEN_HERE",
    "url": "http://127.0.0.1:20030",
    "user_id": "alice"
  }
]
```

`curl http://127.0.0.1:8080/healthz`

Sample output:

```json
{"status":"ok"}
```

## 10) Attach to a User Container (`-it`)

Find the container name:

```bash
docker ps --filter label=managed-by=openclaw-manager --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Attach an interactive shell:

```bash
docker exec -it openclaw-user-alice sh
```

If `sh` is unavailable, try:

```bash
docker exec -it openclaw-user-alice bash
```

Useful checks from host:

```bash
docker logs --tail 100 openclaw-user-alice
docker inspect openclaw-user-alice --format '{{json .State}}'
```

## 11) Container Size Per User

Each user container shares the same OpenClaw image layers.
Per-user extra disk usage is mostly:

- container writable layer
- user Docker volume (persistent data)

Check per-container writable size:

```bash
docker ps -s --filter label=managed-by=openclaw-manager
```

Check per-user volume size:

```bash
docker system df -v
```

Check live CPU/RAM per user container:

```bash
docker stats --no-stream openclaw-user-alice
```

## 12) Main config (docker-compose defaults)

- `OPENCLAW_INSTANCE_AUTH_MODE=token`
- `OPENCLAW_TOKEN_MODE_DISABLE_DEVICE_AUTH=1`
- `OPENCLAW_MIGRATE_EXISTING_ON_START=1`
- `OPENCLAW_BIND_HOST=127.0.0.1`
- Port range: `20000-29999`
