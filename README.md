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

## 3) Login as the user

From `python cli.py info --user alice`, use:

- `url` (example: `http://127.0.0.1:20030`)
- `token`

Open URL in browser and authenticate with token.

## 4) Clean reset (optional)

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

## Command Reference (with sample output)

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

## Main config (docker-compose defaults)

- `OPENCLAW_INSTANCE_AUTH_MODE=token`
- `OPENCLAW_TOKEN_MODE_DISABLE_DEVICE_AUTH=1`
- `OPENCLAW_MIGRATE_EXISTING_ON_START=1`
- `OPENCLAW_BIND_HOST=127.0.0.1`
- Port range: `20000-29999`
