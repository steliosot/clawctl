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

## 2) Use the CLI

Install CLI deps once:

```bash
# from repo root
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Point CLI to manager API:

```bash
export OPENCLAW_MANAGER_API=http://127.0.0.1:8080
```

Create user container:

```bash
python cli.py create --user alice --port 20030
```

Show user info:

```bash
python cli.py info --user alice
```

List all:

```bash
python cli.py list
```

Restart/delete:

```bash
python cli.py restart --user alice
python cli.py delete --user alice
```

## 3) Use the API Programmatically (Python)

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

## 4) Login as the user

From `python cli.py info --user alice`, use:

- `url` (example: `http://127.0.0.1:20030`)
- `token`

Open URL in browser and authenticate with token.

## 5) Clean reset (optional)

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

## 6) Command Reference (with sample output)

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

## 7) Attach to a User Container (`-it`)

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

## 8) Container Size Per User

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

## 9) Main config (docker-compose defaults)

- `OPENCLAW_INSTANCE_AUTH_MODE=token`
- `OPENCLAW_TOKEN_MODE_DISABLE_DEVICE_AUTH=1`
- `OPENCLAW_MIGRATE_EXISTING_ON_START=1`
- `OPENCLAW_BIND_HOST=127.0.0.1`
- Port range: `20000-29999`
