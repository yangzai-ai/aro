"""Project API composition; generic API and core know nothing about helpdesk."""

import asyncio
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import Response

from api.app import create_app as create_base_app
from api.auth import require_auth
from api.settings import APISettings
from core.kernel.lifecycle import wait_for_cleanup
from projects.helpdesk.workbench import Workbench


def create_app(settings: APISettings | None = None) -> FastAPI:
    app = create_base_app(settings)
    store = app.state.sessions

    def owned(session_id: str | None) -> Workbench:
        session = store.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "会话不存在或已过期，请开始新对话。")
        capability = next(
            (c for c in session.engine.capabilities if isinstance(c, Workbench)), None
        )
        if capability is None:
            raise HTTPException(404, "当前配置未启用业务工作台。")
        return capability

    @app.get("/api/helpdesk/panel", dependencies=[Depends(require_auth)])
    async def panel(
        x_aro_session: str | None = Header(default=None, max_length=64),
        selected: str | None = Query(default=None, max_length=80),
    ) -> dict[str, object]:
        await store.expire()
        capability = owned(x_aro_session)
        try:
            result = await capability.panel(selected)
            if store.sessions.get(x_aro_session) is None:
                raise HTTPException(404, "会话已关闭。")
            return result
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "态势读取失败，请检查数据源。") from exc

    @app.get("/api/helpdesk/agents", dependencies=[Depends(require_auth)])
    async def agents() -> list[dict[str, str]]:
        """Read the configured catalogue even before a conversation exists."""
        store.load_experts()
        return [{"name": item.name, "description": item.description} for item in store.experts]

    @app.get("/api/helpdesk/artifacts/{artifact_id}", dependencies=[Depends(require_auth)])
    async def download(
        artifact_id: str,
        x_aro_session: str | None = Header(default=None, max_length=64),
    ) -> Response:
        await store.expire()
        capability = owned(x_aro_session)
        try:
            item = await capability.read_artifact(artifact_id)
        except Exception as exc:
            raise HTTPException(404, "报告不存在或已过期。") from exc
        if store.sessions.get(x_aro_session) is None:
            raise HTTPException(404, "会话已关闭。")
        # Bytes acquired under service lock: expiry can no longer remove an unopened file.
        return Response(
            item.content,
            media_type=item.metadata.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{item.metadata.filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.delete("/api/helpdesk/session", dependencies=[Depends(require_auth)])
    async def close_session(
        x_aro_session: str | None = Header(default=None, max_length=64),
    ) -> dict[str, str]:
        session = store.sessions.pop(x_aro_session, None)
        if session is None:
            raise HTTPException(404, "会话不存在或已关闭。")
        session.updated = time.monotonic()
        await wait_for_cleanup(asyncio.create_task(store.close_session(session)))
        return {"status": "closed"}

    return app


app = create_app()
