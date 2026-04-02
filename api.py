from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from openclaw_manager import INSTANCE_AUTH_MODE, MIGRATE_EXISTING_ON_START, ManagerError, OpenClawManager


AUTO_PAIR_ENABLED = os.getenv("OPENCLAW_AUTO_PAIR_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
AUTO_PAIR_INTERVAL_SEC = int(os.getenv("OPENCLAW_AUTO_PAIR_INTERVAL_SEC", "3"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    auto_pair_thread: threading.Thread | None = None
    migrate_thread: threading.Thread | None = None
    manager = get_manager()

    def migrate_worker() -> None:
        try:
            manager.migrate_existing_instances()
        except Exception:
            pass

    def auto_pair_worker() -> None:
        while not stop.is_set():
            try:
                manager.approve_all_pending_pairings()
            except Exception:
                pass
            stop.wait(AUTO_PAIR_INTERVAL_SEC)

    if MIGRATE_EXISTING_ON_START:
        migrate_thread = threading.Thread(target=migrate_worker, daemon=True)
        migrate_thread.start()

    if AUTO_PAIR_ENABLED and INSTANCE_AUTH_MODE == "token":
        auto_pair_thread = threading.Thread(target=auto_pair_worker, daemon=True)
        auto_pair_thread.start()

    yield

    stop.set()
    if auto_pair_thread:
        auto_pair_thread.join(timeout=2)
    if migrate_thread:
        migrate_thread.join(timeout=2)


app = FastAPI(title="OpenClaw Instance Manager", version="0.1.0", lifespan=lifespan)


@lru_cache(maxsize=1)
def get_manager() -> OpenClawManager:
    return OpenClawManager()


class CreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=120)
    port: int | None = Field(default=None, ge=1024, le=65535)
    image: str | None = None
    provider: str | None = None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/instances")
def list_instances() -> list[dict]:
    try:
        return get_manager().list_instances()
    except ManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/instances/{user_id}")
def get_instance(user_id: str) -> dict:
    try:
        return get_manager().get_instance(user_id)
    except ManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/instances")
def create_instance(req: CreateRequest) -> dict:
    try:
        return get_manager().create_instance(user_id=req.user_id, port=req.port, image=req.image, provider=req.provider)
    except ManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/instances/{user_id}/restart")
def restart_instance(user_id: str) -> dict:
    try:
        return get_manager().restart_instance(user_id)
    except ManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/instances/{user_id}")
def delete_instance(user_id: str) -> dict[str, str]:
    try:
        get_manager().delete_instance(user_id)
    except ManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "user_id": user_id}
