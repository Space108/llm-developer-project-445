"""Backend + оркестратор: HTTP-граница чата.

    uvicorn app.main:app --host 127.0.0.1 --port 8000

  POST /api/session                      {"user": "anna"} → открыть сессию
  POST /api/chat                         {"session_id", "message"} → SSE-поток хода
  POST /api/cards/{pending_id}/decision  подтверждение карточки (с шага 2)

События SSE хода: plan / card / reply / error / done.

TODO(шаг 1):
  1. lifespan: подключить LLM (config.setup_model(); в бонус-шаге 8* —
     llm_pool.setup_pool(), если задан LLM_SECONDARY_BASE_URL).
  2. CORS: allow_origins=["*"] — фронтенд живёт на другом порту стенда.
  3. GET /api/health.
  4. POST /api/session: sessions.open_session(login); ответ: session_id,
     name, role, visible_tools — инструменты allowlist сессии
     (с шага 4 — permissions.visible_tools(...), до него — весь пул).
  5. POST /api/chat: события хода через asyncio.Queue гонятся
     StreamingResponse'ом (media_type="text/event-stream", формат
     'data: {json}\\n\\n'); сбой хода — событие error, не падение потока.

TODO(шаг 2): POST /api/cards/{pending_id}/decision — кнопка карточки:
  «да» → orchestrator.execute_pending, «нет» → отмена.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import orchestrator, sessions
from core import config


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.setup_model()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SessionBody(BaseModel):
    user: str


class ChatBody(BaseModel):
    session_id: str
    message: str


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "api"}


@app.post("/api/session")
async def open_session(body: SessionBody):
    try:
        session = await sessions.open_session(body.user)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {
        "session_id": session.session_id,
        "name": session.user.name,
        "role": session.user.role,
        "visible_tools": session.pool.tool_names(),
    }


@app.post("/api/chat")
async def chat(body: ChatBody, request: Request):
    session = sessions.get(body.session_id)
    if session is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            await orchestrator.run_turn(session, body.message, emit)
        except Exception as e:  # noqa: BLE001 — сбой хода уходит событием, поток жив
            await queue.put({"type": "error", "detail": str(e)[:300]})
        finally:
            await queue.put({"type": "done"})
            await queue.put(None)

    task = asyncio.create_task(run())

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
