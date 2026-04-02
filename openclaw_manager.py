from __future__ import annotations

import json
import os
import secrets
import socket
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, NotFound


DEFAULT_IMAGE = os.getenv("OPENCLAW_IMAGE", "ghcr.io/openclaw/openclaw:slim")
DEFAULT_DATA_DIR = Path(os.getenv("OPENCLAW_MANAGER_DATA_DIR", "./data")).resolve()
REGISTRY_FILE = DEFAULT_DATA_DIR / "instances.json"
BASE_USER_DIR = DEFAULT_DATA_DIR / "users"
DEFAULT_BIND_HOST = os.getenv("OPENCLAW_BIND_HOST", "127.0.0.1")
DEFAULT_PORT_START = int(os.getenv("OPENCLAW_PORT_START", "20000"))
DEFAULT_PORT_END = int(os.getenv("OPENCLAW_PORT_END", "29999"))
DEFAULT_TZ = os.getenv("OPENCLAW_TZ", "UTC")
DEFAULT_STORAGE_MODE = os.getenv("OPENCLAW_STORAGE_MODE", "volume").strip().lower()
GATEWAY_READY_TIMEOUT_SEC = int(os.getenv("OPENCLAW_GATEWAY_READY_TIMEOUT_SEC", "90"))
INSTANCE_AUTH_MODE = os.getenv("OPENCLAW_INSTANCE_AUTH_MODE", "token").strip().lower()
MIGRATE_EXISTING_ON_START = os.getenv("OPENCLAW_MIGRATE_EXISTING_ON_START", "1").lower() in {"1", "true", "yes", "on"}
TOKEN_MODE_DISABLE_DEVICE_AUTH = os.getenv("OPENCLAW_TOKEN_MODE_DISABLE_DEVICE_AUTH", "1").lower() in {"1", "true", "yes", "on"}
ALLOW_ALL_ORIGINS = os.getenv("OPENCLAW_ALLOW_ALL_ORIGINS", "1").lower() in {"1", "true", "yes", "on"}
ALLOWED_ORIGIN_HOSTS = [
    h.strip()
    for h in os.getenv("OPENCLAW_ALLOWED_ORIGIN_HOSTS", "localhost,127.0.0.1,host.docker.internal").split(",")
    if h.strip()
]


class ManagerError(Exception):
    pass


@dataclass
class Instance:
    user_id: str
    container_name: str
    host_port: int
    token: str | None
    auth_mode: str
    created_at: str
    config_dir: str
    workspace_dir: str
    image: str
    provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None


class OpenClawManager:
    def __init__(self) -> None:
        self.data_dir = DEFAULT_DATA_DIR
        self.registry_file = REGISTRY_FILE
        self.base_user_dir = BASE_USER_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.base_user_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.client = docker.from_env()
        except DockerException as exc:
            raise ManagerError(f"Docker connection failed: {exc}") from exc

        if not self.registry_file.exists():
            self._write_registry({})

    def _auth_mode(self) -> str:
        if INSTANCE_AUTH_MODE not in {"none", "token"}:
            raise ManagerError("OPENCLAW_INSTANCE_AUTH_MODE must be 'none' or 'token'.")
        return INSTANCE_AUTH_MODE

    def _read_registry(self) -> dict[str, Any]:
        with self.registry_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write_registry(self, data: dict[str, Any]) -> None:
        with self.registry_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def _slug(self, user_id: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "-" for ch in user_id.lower()).strip("-")
        if not cleaned:
            raise ManagerError("Invalid user_id. Use letters/numbers and optional separators.")
        return cleaned[:40]

    def _is_port_free(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((DEFAULT_BIND_HOST, port))
            except OSError:
                return False
        return True

    def _pick_port(self, used_ports: set[int]) -> int:
        for port in range(DEFAULT_PORT_START, DEFAULT_PORT_END + 1):
            if port in used_ports:
                continue
            if self._is_port_free(port):
                return port
        raise ManagerError("No free ports in configured range.")

    def _container_state(self, container_name: str) -> str:
        try:
            c = self.client.containers.get(container_name)
            return c.status
        except NotFound:
            return "missing"
        except DockerException:
            return "unknown"

    def _allowed_origins_for_port(self, host_port: int) -> list[str]:
        if ALLOW_ALL_ORIGINS:
            return ["*"]
        hosts = list(ALLOWED_ORIGIN_HOSTS)
        if DEFAULT_BIND_HOST not in hosts and DEFAULT_BIND_HOST not in {"0.0.0.0", "::"}:
            hosts.append(DEFAULT_BIND_HOST)

        origins: list[str] = []
        for host in hosts:
            origins.append(f"http://{host}:18789")
            origins.append(f"http://{host}:{host_port}")
        # Keep deterministic order and remove duplicates.
        return list(dict.fromkeys(origins))

    def _gateway_bind_value(self, auth_mode: str) -> str:
        # Containers must bind on a non-loopback interface so published host ports work.
        # Host exposure is still controlled by OPENCLAW_BIND_HOST (127.0.0.1 by default).
        _ = auth_mode
        return "lan"

    def _exec_openclaw(self, container_name: str, args: list[str]) -> str:
        try:
            container = self.client.containers.get(container_name)
            result = container.exec_run(args)
        except DockerException as exc:
            raise ManagerError(f"Failed to exec in container '{container_name}': {exc}") from exc

        output_bytes: bytes
        if isinstance(result.output, bytes):
            output_bytes = result.output
        else:
            output_bytes = b""
        output = output_bytes.decode("utf-8", errors="replace")

        if result.exit_code != 0:
            raise ManagerError(f"Command failed in '{container_name}': {' '.join(args)}\n{output}")
        return output

    def _wait_gateway_ready(self, container_name: str, timeout_sec: int = GATEWAY_READY_TIMEOUT_SEC) -> bool:
        deadline = time.time() + timeout_sec
        running_since: float | None = None
        check_cmd = [
            "node",
            "-e",
            "fetch('http://127.0.0.1:18789/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))",
        ]
        while time.time() < deadline:
            state = self._container_state(container_name)
            if state in {"exited", "dead", "missing"}:
                raise ManagerError(f"Container '{container_name}' is not running while waiting for readiness (state={state}).")
            if state != "running":
                running_since = None
                time.sleep(2)
                continue
            if running_since is None:
                running_since = time.time()
            try:
                self._exec_openclaw(container_name, check_cmd)
                return True
            except ManagerError:
                # Some OpenClaw builds take time to expose /healthz; if process stays running, continue.
                if time.time() - running_since >= 12:
                    return True
                time.sleep(2)
        return False

    def _bootstrap_user_config(
        self,
        image: str,
        mounts: dict[str, dict[str, str]],
        token: str | None,
        host_port: int,
        auth_mode: str,
        provider: str | None,
        llm_base_url: str | None,
        llm_model: str | None,
    ) -> None:
        # Initialize per-user config non-interactively so gateway can bind immediately.
        self.client.containers.run(
            image=image,
            command=["sh", "-lc", "mkdir -p /home/node/.openclaw/workspace && chown -R 1000:1000 /home/node/.openclaw"],
            remove=True,
            volumes=mounts,
            user="0:0",
        )
        base_env = {
            "HOME": "/home/node",
            "TERM": "xterm-256color",
            "TZ": DEFAULT_TZ,
        }
        if token:
            base_env["OPENCLAW_GATEWAY_TOKEN"] = token
        allowed_origins = json.dumps(self._allowed_origins_for_port(host_port))
        bind_value = self._gateway_bind_value(auth_mode)
        setup_commands = [
            ["node", "openclaw.mjs", "config", "set", "gateway.mode", "local"],
            ["node", "openclaw.mjs", "config", "set", "gateway.bind", bind_value],
            [
                "node",
                "openclaw.mjs",
                "config",
                "set",
                "gateway.controlUi.allowedOrigins",
                allowed_origins,
                "--strict-json",
            ],
        ]
        if auth_mode == "none":
            setup_commands.extend(
                [
                    ["node", "openclaw.mjs", "config", "set", "gateway.auth.mode", "none"],
                    ["node", "openclaw.mjs", "config", "set", "gateway.controlUi.dangerouslyDisableDeviceAuth", "true"],
                    ["node", "openclaw.mjs", "config", "set", "gateway.controlUi.allowInsecureAuth", "true"],
                ]
            )
        else:
            setup_commands.append(["node", "openclaw.mjs", "config", "set", "gateway.auth.mode", "token"])
            if TOKEN_MODE_DISABLE_DEVICE_AUTH:
                setup_commands.append(["node", "openclaw.mjs", "config", "set", "gateway.controlUi.dangerouslyDisableDeviceAuth", "true"])
        if provider == "ollama":
            model_id = llm_model or "mistral"
            base_url = llm_base_url or "http://host.docker.internal:11434"
            provider_cfg = json.dumps(
                {
                    "baseUrl": base_url,
                    "models": [
                        {
                            "id": model_id,
                            "name": model_id,
                            "api": "ollama",
                        }
                    ],
                }
            )
            setup_commands.extend(
                [
                    ["node", "openclaw.mjs", "config", "set", "models.providers.ollama", provider_cfg, "--strict-json"],
                    ["node", "openclaw.mjs", "config", "set", "agents.defaults.model.primary", f"ollama/{model_id}"],
                ]
            )
        for cmd in setup_commands:
            self.client.containers.run(
                image=image,
                command=cmd,
                remove=True,
                environment=base_env,
                volumes=mounts,
            )

    def _build_storage_mounts(self, slug: str) -> tuple[dict[str, dict[str, str]], str, str]:
        if DEFAULT_STORAGE_MODE == "bind":
            user_root = self.base_user_dir / slug
            config_ref = str(user_root / "config")
            workspace_ref = str(user_root / "workspace")
            Path(config_ref).mkdir(parents=True, exist_ok=True)
            Path(workspace_ref).mkdir(parents=True, exist_ok=True)
        else:
            # Use named volumes by default so manager-in-container can spawn sibling containers reliably.
            config_ref = f"openclaw-{slug}-config"
            workspace_ref = f"openclaw-{slug}-workspace"
            self.client.volumes.create(name=config_ref, labels={"managed-by": "openclaw-manager", "openclaw.user": slug})
            self.client.volumes.create(name=workspace_ref, labels={"managed-by": "openclaw-manager", "openclaw.user": slug})

        mounts = {
            config_ref: {"bind": "/home/node/.openclaw", "mode": "rw"},
            workspace_ref: {"bind": "/home/node/.openclaw/workspace", "mode": "rw"},
        }
        return mounts, config_ref, workspace_ref

    def _device_auth_disabled(self, container_name: str) -> bool:
        try:
            out = self._exec_openclaw(
                container_name,
                ["node", "openclaw.mjs", "config", "get", "gateway.controlUi.dangerouslyDisableDeviceAuth"],
            ).strip()
        except ManagerError:
            return False
        return out.lower() == "true"

    def _mounts_from_row(self, row: dict[str, Any]) -> dict[str, dict[str, str]]:
        return {
            str(row["config_dir"]): {"bind": "/home/node/.openclaw", "mode": "rw"},
            str(row["workspace_dir"]): {"bind": "/home/node/.openclaw/workspace", "mode": "rw"},
        }

    def list_instances(self) -> list[dict[str, Any]]:
        reg = self._read_registry()
        instances: list[dict[str, Any]] = []
        for user_id, row in reg.items():
            row = dict(row)
            row.setdefault("auth_mode", self._auth_mode())
            if row.get("auth_mode") == "none":
                row["token"] = None
            row["status"] = self._container_state(row["container_name"])
            row["url"] = f"http://{DEFAULT_BIND_HOST}:{row['host_port']}"
            instances.append(row)
        return sorted(instances, key=lambda x: x["created_at"])

    def get_instance(self, user_id: str) -> dict[str, Any]:
        reg = self._read_registry()
        if user_id not in reg:
            raise ManagerError(f"User '{user_id}' has no instance.")
        row = dict(reg[user_id])
        row.setdefault("auth_mode", self._auth_mode())
        if row.get("auth_mode") == "none":
            row["token"] = None
        row["status"] = self._container_state(row["container_name"])
        row["url"] = f"http://{DEFAULT_BIND_HOST}:{row['host_port']}"
        return row

    def _provider_settings(self, provider: str | None) -> tuple[str | None, dict[str, str], str | None, str | None, str | None]:
        if provider is None:
            return None, {}, None, None, None
        normalized = provider.strip().lower()
        if normalized == "":
            return None, {}, None, None, None
        if normalized == "ollama":
            llm_base_url = os.getenv("OPENCLAW_OLLAMA_BASE_URL", "http://host.docker.internal:11434")
            llm_model = os.getenv("OPENCLAW_OLLAMA_MODEL", "mistral")
            env = {
                "OPENCLAW_LLM_PROVIDER": "ollama",
                "OPENCLAW_LLM_BASE_URL": llm_base_url,
                "OPENCLAW_LLM_MODEL": llm_model,
            }
            return "ollama", env, None, llm_base_url, llm_model
        raise ManagerError(f"Unsupported provider '{provider}'. Supported: ollama")

    def create_instance(
        self,
        user_id: str,
        port: int | None = None,
        image: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        reg = self._read_registry()
        if user_id in reg:
            raise ManagerError(f"User '{user_id}' already has an instance.")

        slug = self._slug(user_id)
        container_name = f"openclaw-{slug}"
        image_to_use = image or DEFAULT_IMAGE
        used_ports = {int(v["host_port"]) for v in reg.values()}

        if port is None:
            host_port = self._pick_port(used_ports)
        else:
            if port in used_ports:
                raise ManagerError(f"Port {port} is already assigned.")
            if not self._is_port_free(port):
                raise ManagerError(f"Port {port} is not available on host.")
            host_port = port

        mounts, config_ref, workspace_ref = self._build_storage_mounts(slug)

        resolved_provider, provider_env, provider_auth_mode, llm_base_url, llm_model = self._provider_settings(provider)
        auth_mode = provider_auth_mode or self._auth_mode()
        token = secrets.token_urlsafe(32) if auth_mode == "token" else None
        created_at = datetime.now(timezone.utc).isoformat()

        env = {
            "HOME": "/home/node",
            "TERM": "xterm-256color",
            "TZ": DEFAULT_TZ,
        }
        if token:
            env["OPENCLAW_GATEWAY_TOKEN"] = token
        env.update(provider_env)
        bind_value = self._gateway_bind_value(auth_mode)

        command = [
            "node",
            "openclaw.mjs",
            "gateway",
            "--bind",
            bind_value,
            "--port",
            "18789",
            "--allow-unconfigured",
        ]
        container_started = False

        try:
            # Ensure a fresh image exists locally.
            self.client.images.pull(image_to_use)
            self._bootstrap_user_config(
                image=image_to_use,
                mounts=mounts,
                token=token,
                host_port=host_port,
                auth_mode=auth_mode,
                provider=resolved_provider,
                llm_base_url=llm_base_url,
                llm_model=llm_model,
            )
            run_kwargs: dict[str, Any] = dict(
                image=image_to_use,
                name=container_name,
                command=command,
                detach=True,
                init=True,
                restart_policy={"Name": "unless-stopped"},
                environment=env,
                ports={"18789/tcp": (DEFAULT_BIND_HOST, host_port)},
                volumes=mounts,
                labels={
                    "managed-by": "openclaw-manager",
                    "openclaw.user_id": user_id,
                },
            )
            if resolved_provider == "ollama":
                run_kwargs["extra_hosts"] = {"host.docker.internal": "host-gateway"}
            self.client.containers.run(**run_kwargs)
            container_started = True
            self._wait_gateway_ready(container_name)
        except DockerException as exc:
            raise ManagerError(f"Failed to create container: {exc}") from exc
        except ManagerError:
            if container_started:
                try:
                    c = self.client.containers.get(container_name)
                    c.remove(force=True)
                except DockerException:
                    pass
            raise

        instance = Instance(
            user_id=user_id,
            container_name=container_name,
            host_port=host_port,
            token=token,
            auth_mode=auth_mode,
            created_at=created_at,
            config_dir=str(config_ref),
            workspace_dir=str(workspace_ref),
            image=image_to_use,
            provider=resolved_provider,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
        )
        reg[user_id] = asdict(instance)
        self._write_registry(reg)
        return self.get_instance(user_id)

    def delete_instance(self, user_id: str) -> None:
        reg = self._read_registry()
        if user_id not in reg:
            raise ManagerError(f"User '{user_id}' has no instance.")

        row = reg[user_id]
        try:
            c = self.client.containers.get(row["container_name"])
            c.remove(force=True)
        except NotFound:
            pass
        except DockerException as exc:
            raise ManagerError(f"Failed to remove container: {exc}") from exc

        reg.pop(user_id, None)
        self._write_registry(reg)

    def restart_instance(self, user_id: str) -> dict[str, Any]:
        reg = self._read_registry()
        if user_id not in reg:
            raise ManagerError(f"User '{user_id}' has no instance.")
        row = reg[user_id]
        try:
            c = self.client.containers.get(row["container_name"])
            c.restart(timeout=10)
        except NotFound as exc:
            raise ManagerError("Container is missing. Recreate the instance.") from exc
        except DockerException as exc:
            raise ManagerError(f"Failed to restart container: {exc}") from exc
        return self.get_instance(user_id)

    def approve_pending_pairings(self, user_id: str) -> int:
        reg = self._read_registry()
        if user_id not in reg:
            raise ManagerError(f"User '{user_id}' has no instance.")

        row = reg[user_id]
        row_auth_mode = str(row.get("auth_mode", self._auth_mode()))
        if row_auth_mode == "none":
            return 0
        if self._container_state(row["container_name"]) != "running":
            return 0

        output = self._exec_openclaw(row["container_name"], ["node", "openclaw.mjs", "devices", "list", "--json"])
        try:
            data = json.loads(output) if output.strip() else {}
        except json.JSONDecodeError as exc:
            raise ManagerError(f"Invalid devices JSON for '{user_id}': {output}") from exc
        pending = data.get("pending", [])
        approved = 0
        for item in pending:
            request_id = item.get("requestId")
            if not request_id:
                continue
            self._exec_openclaw(
                row["container_name"],
                ["node", "openclaw.mjs", "devices", "approve", request_id, "--json"],
            )
            approved += 1
        return approved

    def approve_all_pending_pairings(self) -> int:
        if self._auth_mode() == "none":
            return 0
        reg = self._read_registry()
        total = 0
        for user_id in reg:
            try:
                total += self.approve_pending_pairings(user_id)
            except ManagerError:
                # Best-effort background sweep; per-user errors should not stop other approvals.
                continue
        return total

    def migrate_existing_instances(self) -> int:
        reg = self._read_registry()
        migrated = 0
        default_auth_mode = self._auth_mode()
        for user_id, row in reg.items():
            try:
                row_auth_mode = str(row.get("auth_mode", default_auth_mode)).strip().lower()
                auth_mode = row_auth_mode if row_auth_mode in {"none", "token"} else default_auth_mode
                current_mode = str(row.get("auth_mode", ""))
                current_token = row.get("token")
                state = self._container_state(str(row["container_name"]))
                expected_bind = self._gateway_bind_value(auth_mode)
                current_bind = None
                current_device_auth_disabled = False
                if state != "missing":
                    c_probe = self.client.containers.get(str(row["container_name"]))
                    cmd_probe = c_probe.attrs.get("Config", {}).get("Cmd", []) or []
                    if "--bind" in cmd_probe:
                        i = cmd_probe.index("--bind")
                        if i + 1 < len(cmd_probe):
                            current_bind = str(cmd_probe[i + 1])
                    current_device_auth_disabled = self._device_auth_disabled(str(row["container_name"]))

                if auth_mode == "none" and current_mode == "none" and not current_token and state == "running" and current_bind == expected_bind:
                    continue
                if (
                    auth_mode == "token"
                    and current_mode == "token"
                    and current_token
                    and state == "running"
                    and current_bind == expected_bind
                    and current_device_auth_disabled == TOKEN_MODE_DISABLE_DEVICE_AUTH
                ):
                    continue

                mounts = self._mounts_from_row(row)
                token = row.get("token") if auth_mode == "token" else None
                self._bootstrap_user_config(
                    image=str(row["image"]),
                    mounts=mounts,
                    token=token,
                    host_port=int(row["host_port"]),
                    auth_mode=auth_mode,
                    provider=row.get("provider"),
                    llm_base_url=row.get("llm_base_url"),
                    llm_model=row.get("llm_model"),
                )
                row["auth_mode"] = auth_mode
                if auth_mode == "none":
                    row["token"] = None
                elif not row.get("token"):
                    row["token"] = secrets.token_urlsafe(32)
                reg[user_id] = row

                state = self._container_state(str(row["container_name"]))
                if state != "missing":
                    c = self.client.containers.get(str(row["container_name"]))
                    env = c.attrs.get("Config", {}).get("Env", [])
                    env_map = {}
                    for item in env:
                        if "=" in item:
                            k, v = item.split("=", 1)
                            env_map[k] = v
                    if auth_mode == "token":
                        env_map["OPENCLAW_GATEWAY_TOKEN"] = str(reg[user_id]["token"])
                    else:
                        env_map.pop("OPENCLAW_GATEWAY_TOKEN", None)
                    bind_value = self._gateway_bind_value(auth_mode)
                    if state == "running":
                        c.stop(timeout=10)
                    c.remove(force=True)
                    self.client.containers.run(
                        image=str(row["image"]),
                        name=str(row["container_name"]),
                        command=[
                            "node",
                            "openclaw.mjs",
                            "gateway",
                            "--bind",
                            bind_value,
                            "--port",
                            "18789",
                            "--allow-unconfigured",
                        ],
                        detach=True,
                        init=True,
                        restart_policy={"Name": "unless-stopped"},
                        environment=env_map,
                        ports={"18789/tcp": (DEFAULT_BIND_HOST, int(row["host_port"]))},
                        volumes=mounts,
                        labels={
                            "managed-by": "openclaw-manager",
                            "openclaw.user_id": user_id,
                        },
                    )
                    self._wait_gateway_ready(str(row["container_name"]))
                migrated += 1
            except Exception:
                continue
        self._write_registry(reg)
        return migrated
