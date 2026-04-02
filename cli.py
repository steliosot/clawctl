from __future__ import annotations

import json
import os
import time

import requests
import typer


app = typer.Typer(help="CLI for OpenClaw instance manager API.")
API_URL = os.getenv("OPENCLAW_MANAGER_API", "http://127.0.0.1:8080")


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


@app.command()
def create(
    user: str = typer.Option(..., "--user", "-u"),
    port: int | None = typer.Option(None, "--port"),
    image: str | None = typer.Option(None, "--image"),
    provider: str | None = typer.Option(None, "--provider"),
) -> None:
    payload = {"user_id": user, "port": port, "image": image, "provider": provider}
    r = requests.post(f"{API_URL}/instances", json=payload, timeout=180)
    _print_response(r)
    _print_instance_hint(r.json())


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


if __name__ == "__main__":
    app()
