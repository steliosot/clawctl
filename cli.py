from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests
import typer


app = typer.Typer(help="CLI for OpenClaw instance manager API.")
API_URL = os.getenv("OPENCLAW_MANAGER_API", "http://127.0.0.1:8080")
DEFAULT_PROJECT_DIR = Path(os.getenv("OPENCLAW_MANAGER_PROJECT_DIR", ".")).resolve()
MANAGER_HEALTH_URL = f"{API_URL.rstrip('/')}/healthz"
MANAGER_CONTAINER_NAME = "clawctl-server"
LEGACY_MANAGER_CONTAINER_NAME = "openclaw-manager"
OLLAMA_CONTAINER_NAME = "ollama"
OLLAMA_IMAGE = "ollama/ollama:latest"
OLLAMA_PORT = "11434:11434"
OLLAMA_VOLUME = "ollama:/root/.ollama"

OLLAMA_MODEL_CATALOG: list[tuple[str, str]] = [
    ("phi4-mini:latest", "2.5 GB"),
    ("mistral:latest", "4.4 GB"),
    ("qwen2.5:7b", "4.7 GB"),
    ("qwen2.5-coder:7b", "4.7 GB"),
    ("llama3.1:8b", "4.9 GB"),
]


def _print_response(r: requests.Response) -> None:
    try:
        data = r.json()
    except Exception:
        typer.echo(r.text)
        raise typer.Exit(code=1)

    if r.status_code >= 400:
        typer.echo(json.dumps(data, indent=2))
        raise typer.Exit(code=1)
    typer.echo(json.dumps(data, indent=2))


def _print_instance_hint(data: dict) -> None:
    auth_mode = data.get("auth_mode", "token")
    url = data.get("url")
    if not url:
        return
    if auth_mode == "none":
        typer.echo(f"Open directly: {url}")
    else:
        token = data.get("token")
        if token:
            typer.echo(f"Open: {url} (use token: {token})")
        else:
            typer.echo(f"Open: {url}")


def _run(cmd: list[str], cwd: Path | None = None, fail_message: str | None = None) -> None:
    try:
        result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False)
    except FileNotFoundError as exc:
        raise typer.Exit(code=1) from exc
    if result.returncode != 0:
        if fail_message:
            typer.echo(fail_message)
        raise typer.Exit(code=result.returncode)


def _run_capture(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise typer.Exit(code=1) from exc


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _wait_manager_health(timeout_seconds: int = 90) -> bool:
    for _ in range(timeout_seconds):
        try:
            r = requests.get(MANAGER_HEALTH_URL, timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _compose_up(project_dir: Path) -> None:
    compose_file = project_dir / "docker-compose.yml"
    if not compose_file.exists():
        typer.echo(f"docker-compose.yml not found in {project_dir}")
        raise typer.Exit(code=1)
    cmd = ["docker", "compose", "up", "-d", "--build"]
    legacy = _run_capture(["docker", "inspect", LEGACY_MANAGER_CONTAINER_NAME])
    current = _run_capture(["docker", "inspect", MANAGER_CONTAINER_NAME])
    if legacy.returncode == 0 and current.returncode != 0:
        typer.echo(
            f"Detected legacy manager container '{LEGACY_MANAGER_CONTAINER_NAME}'. "
            f"Replacing with '{MANAGER_CONTAINER_NAME}'."
        )
        _run(["docker", "rm", "-f", LEGACY_MANAGER_CONTAINER_NAME])

    first = _run_capture(cmd, cwd=project_dir)
    if first.returncode == 0:
        typer.echo(first.stdout, nl=False)
        return

    output = f"{first.stdout}\n{first.stderr}".lower()
    conflict_names = (MANAGER_CONTAINER_NAME, LEGACY_MANAGER_CONTAINER_NAME)
    conflict_detected = any(f'container name "/{name}" is already in use' in output for name in conflict_names)
    if conflict_detected:
        typer.echo("Detected manager container-name conflict. Removing stale container(s) and retrying once...")
        _remove_container_if_exists(MANAGER_CONTAINER_NAME)
        _remove_container_if_exists(LEGACY_MANAGER_CONTAINER_NAME)
        second = _run_capture(cmd, cwd=project_dir)
        if second.returncode == 0:
            typer.echo(second.stdout, nl=False)
            return
        typer.echo(second.stdout, nl=False)
        typer.echo(second.stderr, nl=False)
        typer.echo("Failed to start manager with docker compose after conflict retry.")
        raise typer.Exit(code=second.returncode)

    typer.echo(first.stdout, nl=False)
    typer.echo(first.stderr, nl=False)
    typer.echo("Failed to start manager with docker compose.")
    raise typer.Exit(code=first.returncode)


def _ollama_container_exists() -> bool:
    result = subprocess.run(
        ["docker", "inspect", OLLAMA_CONTAINER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _ensure_ollama_running() -> None:
    if _ollama_container_exists():
        _run(["docker", "start", OLLAMA_CONTAINER_NAME], fail_message="Failed to start existing ollama container.")
        return
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            OLLAMA_CONTAINER_NAME,
            "-p",
            OLLAMA_PORT,
            "-v",
            OLLAMA_VOLUME,
            OLLAMA_IMAGE,
        ],
        fail_message="Failed to create/start ollama container.",
    )


def _pull_ollama_model(model: str) -> None:
    _run(
        ["docker", "exec", OLLAMA_CONTAINER_NAME, "ollama", "pull", model],
        fail_message=f"Failed to pull ollama model '{model}'.",
    )


def _ollama_list_model_tags() -> list[str]:
    result = _run_capture(["docker", "exec", OLLAMA_CONTAINER_NAME, "ollama", "list"])
    if result.returncode != 0:
        return []
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    tags: list[str] = []
    for line in lines[1:]:
        parts = line.split()
        if not parts:
            continue
        tags.append(parts[0])
    return tags


def _resolve_canonical_ollama_tag(requested_model: str) -> str:
    tags = _ollama_list_model_tags()
    if not tags:
        return requested_model
    if requested_model in tags:
        return requested_model

    base = requested_model.split(":", 1)[0]
    latest = f"{base}:latest"
    if latest in tags:
        return latest

    family_matches = sorted([tag for tag in tags if tag.startswith(base + ":")])
    if family_matches:
        return family_matches[0]
    return requested_model


def _remove_container_if_exists(name: str) -> None:
    probe = _run_capture(["docker", "inspect", name])
    if probe.returncode == 0:
        _run(["docker", "rm", "-f", name])


def _docker_ids(cmd: list[str]) -> list[str]:
    result = _run_capture(cmd)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _reset_environment(project_dir: Path) -> None:
    typer.echo("Reset: removing manager and managed user containers...")
    _remove_container_if_exists(MANAGER_CONTAINER_NAME)
    _remove_container_if_exists(LEGACY_MANAGER_CONTAINER_NAME)
    user_containers = _docker_ids(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=managed-by=openclaw-manager",
            "--format",
            "{{.ID}}",
        ]
    )
    if user_containers:
        _run(["docker", "rm", "-f", *user_containers])

    typer.echo("Reset: removing managed user volumes...")
    user_volumes = _docker_ids(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            "label=managed-by=openclaw-manager",
        ]
    )
    if user_volumes:
        _run(["docker", "volume", "rm", *user_volumes])

    typer.echo("Reset: clearing local manager data...")
    users_dir = project_dir / "data" / "users"
    instances_file = project_dir / "data" / "instances.json"
    if users_dir.exists():
        shutil.rmtree(users_dir, ignore_errors=True)
    users_dir.mkdir(parents=True, exist_ok=True)
    instances_file.parent.mkdir(parents=True, exist_ok=True)
    instances_file.write_text("{}\n", encoding="utf-8")

    typer.echo("Reset: removing ollama container and volume...")
    _remove_container_if_exists(OLLAMA_CONTAINER_NAME)
    _run_capture(["docker", "volume", "rm", "ollama"])


def _create_instance_request(user: str, port: int | None, image: str | None, provider: str | None, model: str | None) -> dict:
    payload = {"user_id": user, "port": port, "image": image, "provider": provider, "model": model}
    r = requests.post(f"{API_URL}/instances", json=payload, timeout=180)
    _print_response(r)
    data = r.json()
    _print_instance_hint(data)
    return data


@app.command()
def create(
    user: str = typer.Option(..., "--user", "-u"),
    port: int | None = typer.Option(None, "--port"),
    image: str | None = typer.Option(None, "--image"),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
) -> None:
    _create_instance_request(user=user, port=port, image=image, provider=provider, model=model)


@app.command("list")
def list_instances() -> None:
    r = requests.get(f"{API_URL}/instances", timeout=20)
    _print_response(r)


@app.command()
def info(user: str = typer.Option(..., "--user", "-u")) -> None:
    r = requests.get(f"{API_URL}/instances/{user}", timeout=20)
    _print_response(r)
    _print_instance_hint(r.json())


@app.command()
def restart(user: str = typer.Option(..., "--user", "-u")) -> None:
    r = requests.post(f"{API_URL}/instances/{user}/restart", timeout=60)
    _print_response(r)


@app.command()
def delete(user: str = typer.Option(..., "--user", "-u")) -> None:
    r = requests.delete(f"{API_URL}/instances/{user}", timeout=60)
    _print_response(r)


@app.command("wait-ready")
def wait_ready(
    user: str = typer.Option(..., "--user", "-u"),
    timeout_seconds: int = typer.Option(90, "--timeout", min=5, max=600),
) -> None:
    r = requests.get(f"{API_URL}/instances/{user}", timeout=20)
    _print_response(r)
    info = r.json()
    url = info["url"].rstrip("/")
    health_url = f"{url}/healthz"

    deadline = timeout_seconds
    for second in range(deadline):
        try:
            h = requests.get(health_url, timeout=2)
            if h.status_code == 200:
                typer.echo(json.dumps({"ready": True, "url": url}, indent=2))
                return
        except Exception:
            pass
        if second < deadline - 1:
            time.sleep(1)
    typer.echo(json.dumps({"ready": False, "url": url, "timeout_seconds": timeout_seconds}, indent=2))
    raise typer.Exit(code=1)


@app.command()
def up(
    non_interactive: bool = typer.Option(False, "--non-interactive"),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    create_user: bool = typer.Option(False, "--create-user"),
    user: str | None = typer.Option(None, "--user"),
    port: int | None = typer.Option(None, "--port"),
    skip_ollama: bool = typer.Option(False, "--skip-ollama"),
    reset: bool = typer.Option(False, "--reset"),
) -> None:
    if not _docker_available():
        typer.echo("Docker is not available. Please install/start Docker first.")
        raise typer.Exit(code=1)

    if reset:
        _reset_environment(DEFAULT_PROJECT_DIR)

    typer.echo("Step 1: Starting manager server...")
    _compose_up(DEFAULT_PROJECT_DIR)
    typer.echo("Waiting for manager health...")
    if not _wait_manager_health():
        typer.echo(f"Manager health check failed: {MANAGER_HEALTH_URL}")
        raise typer.Exit(code=1)
    typer.echo("Manager is healthy.")

    selected_provider = (provider or "").strip().lower() or None
    selected_model = (model or "").strip() or None

    if non_interactive:
        if selected_provider not in {None, "cloud", "ollama", "vllm"}:
            typer.echo("Invalid --provider. Use cloud, ollama, or vllm.")
            raise typer.Exit(code=1)
    else:
        typer.echo("Step 2: Provider setup (optional)")
        if selected_provider is None:
            typer.echo("Choose provider:")
            typer.echo("  0) Skip for now")
            typer.echo("  1) Cloud (configure API key later in OpenClaw UI)")
            typer.echo("  2) Local Ollama")
            typer.echo("  3) Local VLLM (not supported yet)")
            choice = typer.prompt("Selection", default="0").strip()
            if choice == "1":
                selected_provider = "cloud"
            elif choice == "2":
                selected_provider = "ollama"
            elif choice == "3":
                typer.echo("Local VLLM is not supported yet; skipping provider setup.")
                selected_provider = None
            else:
                selected_provider = None

    if selected_provider == "vllm":
        typer.echo("Local VLLM is not supported yet; continuing without provider.")
        selected_provider = None

    if selected_provider == "ollama" and not skip_ollama:
        typer.echo("Step 3: Setting up local Ollama...")
        _ensure_ollama_running()
        if selected_model is None:
            if non_interactive:
                selected_model = "mistral:latest"
            else:
                typer.echo("Select model to pull:")
                for idx, (model_name, size) in enumerate(OLLAMA_MODEL_CATALOG, start=1):
                    typer.echo(f"  {idx}) {model_name} ({size})")
                picked = typer.prompt("Model", default="2").strip()
                try:
                    index = int(picked) - 1
                    selected_model = OLLAMA_MODEL_CATALOG[index][0]
                except Exception:
                    selected_model = "mistral:latest"
        typer.echo(f"Pulling model: {selected_model}")
        _pull_ollama_model(selected_model)
        canonical = _resolve_canonical_ollama_tag(selected_model)
        if canonical != selected_model:
            typer.echo(f"Ollama installed canonical tag '{canonical}' (requested '{selected_model}'). Using canonical tag.")
            selected_model = canonical
    elif selected_provider == "cloud":
        typer.echo("Cloud selected. Configure provider API key in OpenClaw UI after opening an instance.")

    should_create_user = create_user
    if not non_interactive and not create_user:
        should_create_user = typer.confirm("Step 4: Create first user instance now?", default=True)

    if not should_create_user:
        typer.echo("Setup complete. Next step:")
        if selected_provider == "ollama":
            next_model = selected_model or "mistral:latest"
            typer.echo(f"  clawctl create --user alice --port 21003 --provider ollama --model {next_model}")
        else:
            typer.echo("  clawctl create --user alice --port 21003")
        return

    if user is None and non_interactive:
        typer.echo("--create-user requires --user in --non-interactive mode.")
        raise typer.Exit(code=1)

    first_user = user or typer.prompt("User id", default="alice").strip()
    first_port = port
    if not non_interactive and first_port is None:
        port_raw = typer.prompt("Port (optional)", default="").strip()
        if port_raw:
            try:
                first_port = int(port_raw)
            except ValueError:
                typer.echo("Invalid port value.")
                raise typer.Exit(code=1)

    create_provider: str | None = None
    create_model: str | None = None
    if selected_provider == "ollama":
        create_provider = "ollama"
        create_model = selected_model or "mistral:latest"

    typer.echo("Creating first user instance...")
    _create_instance_request(
        user=first_user,
        port=first_port,
        image=None,
        provider=create_provider,
        model=create_model,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
