from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import db
import settings
import version
from routers import analyze, config, logs, migrate, models, stats, upload, workflows
from services.paths import to_posix_path


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    settings.configure(data_dir)
    db.init_schema()

    app = FastAPI(
        title="ComfyUI 模型迁移管理",
        description="解析工作流引用、分析共享模型、原子迁移到目标目录。",
        version=version.__version__,
    )
    app.include_router(config.router)
    app.include_router(stats.router)
    app.include_router(models.router)
    app.include_router(workflows.router)
    app.include_router(upload.router)
    app.include_router(analyze.router)
    app.include_router(migrate.router)
    app.include_router(logs.router)

    @app.middleware("http")
    async def no_cache_html(request, call_next):
        response = await call_next(request)
        if request.url.path in ("/", "/index.html"):
            # Always revalidate the entry page so a rebuilt JS bundle is picked
            # up without a hard refresh.
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": version.__version__,
            "data_dir": to_posix_path(settings.data_dir()),
        }

    dist = settings.web_dist()
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse)
        def missing_frontend() -> str:
            return (
                "<h1>前端尚未构建</h1>"
                "<p>请在 <code>web/</code> 目录执行 <code>pnpm install &amp;&amp; pnpm build</code>，"
                "然后重启服务。</p>"
                "<p>API 文档：<a href='/docs'>/docs</a></p>"
            )

    return app


app = create_app()
