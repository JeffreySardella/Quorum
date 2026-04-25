from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Settings
from .jobs import Job, JobRegistry, JobStatus

_HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status.value,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
        "messages": job.messages[-50:],
    }


def _ollama_ok(url: str) -> bool:
    """Quick check whether Ollama is reachable."""
    try:
        import httpx
        r = httpx.get(url, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(settings: Settings, jobs: JobRegistry) -> FastAPI:
    app = FastAPI(title="Quorum")

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    # ── optional basic auth ─────────────────────────────────────────────
    if settings.web.auth_user:
        from starlette.middleware.authentication import AuthenticationMiddleware
        from starlette.authentication import (
            AuthCredentials,
            AuthenticationBackend,
            SimpleUser,
        )
        import base64

        class _BasicAuth(AuthenticationBackend):
            async def authenticate(self, conn):  # type: ignore[override]
                auth = conn.headers.get("Authorization")
                if not auth:
                    return None
                try:
                    scheme, credentials = auth.split()
                    if scheme.lower() != "basic":
                        return None
                    decoded = base64.b64decode(credentials).decode("utf-8")
                    username, _, password = decoded.partition(":")
                except Exception:
                    return None
                if secrets.compare_digest(username, settings.web.auth_user) and \
                   secrets.compare_digest(password, settings.web.auth_password):
                    return AuthCredentials(["authenticated"]), SimpleUser(username)
                return None

        app.add_middleware(AuthenticationMiddleware, backend=_BasicAuth())

        @app.middleware("http")
        async def _require_auth(request: Request, call_next):
            if hasattr(request, "user") and getattr(request.user, "is_authenticated", False):
                return await call_next(request)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Not authenticated"},
                headers={"WWW-Authenticate": "Basic"},
            )

    # ── page routes ─────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        ollama = _ollama_ok(settings.ollama_url)
        # Load library stats from QuorumDB
        from ..db import QuorumDB
        try:
            with QuorumDB(settings.db_path) as db:
                db_stats = db.dashboard_stats()
        except Exception:
            db_stats = None
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "settings": settings,
            "ollama_ok": ollama,
            "jobs": jobs.list_all(),
            "db_stats": db_stats,
        })

    @app.get("/commands", response_class=HTMLResponse)
    async def commands_page(request: Request):
        return templates.TemplateResponse("commands.html", {
            "request": request,
            "jobs": jobs.list_all(),
        })

    @app.get("/review", response_class=HTMLResponse)
    async def review_page(request: Request, type: str = "", sort: str = "confidence"):
        from ..db import QuorumDB
        queue = []
        stats = {}
        try:
            with QuorumDB(settings.db_path) as db:
                queue = db.get_review_queue(sort=sort, media_type=type or None, limit=50)
                stats = db.review_stats()
                # Enrich each item with signal details
                for item in queue:
                    signals = db.get_signals(item["id"])
                    item["signals"] = signals
                    item["top_candidate"] = max(signals, key=lambda s: s["confidence"])["candidate"] if signals else ""
        except Exception:
            pass
        return templates.TemplateResponse("review.html", {
            "request": request,
            "queue": queue,
            "stats": stats,
            "type_filter": type,
            "sort": sort,
        })

    @app.post("/api/review/{media_id}/approve")
    async def api_review_approve(media_id: int):
        from datetime import datetime

        from ..db import QuorumDB
        with QuorumDB(settings.db_path) as db:
            item = db.get_review_item(media_id)
            if not item:
                raise HTTPException(404, "Media not found")
            candidate = item["top_candidate"] or "unknown"
            db.insert_feedback(media_id, "approve", candidate, created_at=datetime.now().isoformat(timespec="seconds"))
        return {"status": "approved", "candidate": candidate}

    @app.post("/api/review/{media_id}/reject")
    async def api_review_reject(media_id: int):
        from datetime import datetime

        from ..db import QuorumDB
        with QuorumDB(settings.db_path) as db:
            item = db.get_review_item(media_id)
            if not item:
                raise HTTPException(404, "Media not found")
            candidate = item["top_candidate"] or "unknown"
            db.insert_feedback(media_id, "reject", candidate, created_at=datetime.now().isoformat(timespec="seconds"))
        return {"status": "rejected", "candidate": candidate}

    @app.post("/api/review/{media_id}/correct")
    async def api_review_correct(media_id: int, title: str = Form(...)):
        from datetime import datetime

        from ..db import QuorumDB
        with QuorumDB(settings.db_path) as db:
            item = db.get_review_item(media_id)
            if not item:
                raise HTTPException(404, "Media not found")
            original = item["top_candidate"] or "unknown"
            db.insert_feedback(media_id, "correct", original, correction=title, created_at=datetime.now().isoformat(timespec="seconds"))
        return {"status": "corrected", "title": title}

    @app.get("/library", response_class=HTMLResponse)
    async def library_page(request: Request):
        return templates.TemplateResponse("library.html", {
            "request": request,
        })

    @app.get("/faces", response_class=HTMLResponse)
    async def faces_page(request: Request):
        return templates.TemplateResponse("faces.html", {
            "request": request,
        })

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(
        request: Request, q: str = "", type: str = "", after: str = "", before: str = "",
    ):
        results = []
        if q:
            from ..db import QuorumDB
            from ..search import SearchEngine

            with QuorumDB(settings.db_path) as db:
                engine = SearchEngine(settings, db)
                try:
                    results = engine.search(
                        q,
                        media_type=type or None,
                        after=after or None,
                        before=before or None,
                    )
                finally:
                    engine.close()
        return templates.TemplateResponse("search.html", {
            "request": request,
            "query": q,
            "results": results,
            "type_filter": type,
            "after_filter": after,
            "before_filter": before,
        })

    @app.get("/api/search")
    async def api_search(
        q: str = "", type: str = "", after: str = "", before: str = "", limit: int = 20,
    ):
        if not q:
            return {"results": [], "query": ""}
        from ..db import QuorumDB
        from ..search import SearchEngine

        with QuorumDB(settings.db_path) as db:
            engine = SearchEngine(settings, db)
            try:
                results = engine.search(
                    q,
                    media_type=type or None,
                    after=after or None,
                    before=before or None,
                    limit=limit,
                )
            finally:
                engine.close()
        return {"results": results, "query": q}

    @app.get("/events", response_class=HTMLResponse)
    async def events_page(request: Request, year: str = ""):
        from ..db import QuorumDB
        try:
            with QuorumDB(settings.db_path) as db:
                events = db.list_events()
                if year:
                    events = [e for e in events if e.get("start_time", "").startswith(year)]
                for event in events:
                    event["media_count"] = len(db.get_event_media(event["id"]))
                    event["media"] = db.get_event_media(event["id"])[:6]  # thumbnails
        except Exception:
            events = []
        return templates.TemplateResponse("events.html", {
            "request": request,
            "events": events,
            "year_filter": year,
        })

    @app.get("/dedup", response_class=HTMLResponse)
    async def dedup_page(request: Request):
        from ..dedup import load_report
        report = None
        report_path = Path("dedup-report.json")
        if report_path.exists():
            try:
                report = load_report(report_path)
            except Exception:
                pass
        return templates.TemplateResponse("dedup.html", {
            "request": request,
            "report": report,
        })

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request):
        return templates.TemplateResponse("logs.html", {
            "request": request,
        })

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "settings": settings,
        })

    # ── API routes ──────────────────────────────────────────────────────

    @app.get("/api/status")
    async def api_status():
        ollama = _ollama_ok(settings.ollama_url)
        return {
            "ollama": ollama,
            "ollama_url": settings.ollama_url,
            "watch_inboxes": len(settings.watch.inboxes),
            "cpu_only": settings.cpu_only,
        }

    @app.get("/api/dashboard/stats")
    async def api_dashboard_stats():
        from ..db import QuorumDB
        try:
            with QuorumDB(settings.db_path) as db:
                return db.dashboard_stats()
        except Exception as e:
            return {"error": str(e)}

    @app.post("/api/commands/run")
    async def api_run_command(
        mode: str = Form(...),
        src: str = Form(""),
        dest: str = Form(""),
        dry_run: bool = Form(False),
    ):
        src_path = Path(src) if src else None
        dest_path = Path(dest) if dest else None

        if mode == "auto":
            if not src_path or not dest_path:
                raise HTTPException(400, "auto requires src and dest")
            from ..organize import run_auto
            job_id = jobs.submit(
                f"auto: {src_path.name}",
                run_auto, settings, src_path, dest_path,
                dest_path / "_quarantine", dry_run=dry_run,
            )
        elif mode == "home-videos":
            if not src_path or not dest_path:
                raise HTTPException(400, "home-videos requires src and dest")
            from ..home_videos import run_home_videos
            job_id = jobs.submit(
                f"home-videos: {src_path.name}",
                run_home_videos, settings, src_path, dest_path,
                dest_path / "_quarantine", dry_run=dry_run,
            )
        elif mode == "photos":
            if not src_path or not dest_path:
                raise HTTPException(400, "photos requires src and dest")
            from ..photos import run_photos
            job_id = jobs.submit(
                f"photos: {src_path.name}",
                run_photos, settings, src_path, dest_path,
                dest_path / "_quarantine", dry_run=dry_run,
            )
        elif mode == "enrich":
            if not src_path:
                raise HTTPException(400, "enrich requires src (root)")
            from ..enrich import run_enrich
            job_id = jobs.submit(
                f"enrich: {src_path.name}",
                run_enrich, settings, src_path, force=False,
                use_whisper=True, no_rename=False,
            )
        elif mode == "enrich-photos":
            if not src_path:
                raise HTTPException(400, "enrich-photos requires src (root)")
            from ..enrich_photos import run_enrich_photos
            job_id = jobs.submit(
                f"enrich-photos: {src_path.name}",
                run_enrich_photos, settings, src_path, force=False, do_faces=True,
            )
        elif mode == "triage":
            if not src_path:
                raise HTTPException(400, "triage requires src")
            from ..triage import run_triage
            job_id = jobs.submit(
                f"triage: {src_path.name}",
                run_triage, settings, src_path,
            )
        elif mode == "scan":
            if not src_path:
                raise HTTPException(400, "scan requires src (root)")
            from ..pipeline import Pipeline, write_queue
            def _scan_job():
                pipe = Pipeline(settings)
                try:
                    proposals = pipe.scan(src_path)
                finally:
                    pipe.close()
                n = write_queue(proposals, settings.paths.review_queue, settings.thresholds.review_floor)
                return {"scanned": len(proposals), "queued": n}
            job_id = jobs.submit(f"scan: {src_path.name}", _scan_job)
        elif mode == "rename-folders":
            if not src_path:
                raise HTTPException(400, "rename-folders requires src (root)")
            from ..rename_folders import run_rename_folders
            job_id = jobs.submit(
                f"rename-folders: {src_path.name}",
                run_rename_folders, settings, src_path, dry_run=dry_run,
            )
        else:
            raise HTTPException(400, f"Unknown mode: {mode}")

        return {"job_id": job_id, "status": "submitted"}

    @app.get("/api/jobs/{job_id}")
    async def api_job_status(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return _job_dict(job)

    @app.get("/api/jobs/{job_id}/stream")
    async def api_job_stream(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")

        async def _generate():
            sent = 0
            while True:
                new_msgs = job.messages[sent:]
                for msg in new_msgs:
                    yield f"data: {json.dumps({'message': msg})}\n\n"
                    sent += 1
                if job.status in (JobStatus.DONE, JobStatus.FAILED):
                    yield f"data: {json.dumps({'status': job.status.value, 'error': job.error})}\n\n"
                    break
                await asyncio.sleep(1)

        return StreamingResponse(_generate(), media_type="text/event-stream")

    @app.post("/api/review/apply")
    async def api_review_apply(dry_run: bool = Form(False)):
        queue_path = settings.paths.review_queue
        if not queue_path.exists():
            raise HTTPException(404, "No review queue found")
        from ..pipeline import apply_queue
        applied, skipped, failed = apply_queue(
            queue_path, settings.thresholds.auto_apply, dry_run=dry_run,
        )
        return {"applied": applied, "skipped": skipped, "failed": failed}

    @app.get("/api/library/browse")
    async def api_library_browse(path: str = ""):
        """List files and folders under a given path."""
        base = Path(path) if path else Path(".")
        if not base.exists():
            raise HTTPException(404, "Path not found")
        items: list[dict] = []
        try:
            for entry in sorted(base.iterdir()):
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else None,
                    "path": str(entry),
                })
        except PermissionError:
            raise HTTPException(403, "Permission denied")
        return {"path": str(base), "items": items}

    @app.post("/api/faces/rename")
    async def api_faces_rename(
        cluster_id: int = Form(...),
        name: str = Form(...),
        root: str = Form(...),
    ):
        import sqlite3
        db_path = Path(root) / "faces.db"
        if not db_path.exists():
            raise HTTPException(404, "faces.db not found")
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "UPDATE faces SET label = ?, label_source = 'manual', confidence = 1.0 "
                "WHERE cluster_id = ?",
                (name, cluster_id),
            )
            conn.commit()
            updated = conn.total_changes
        finally:
            conn.close()
        return {"status": "ok", "updated": updated}

    @app.post("/api/settings/save")
    async def api_settings_save(request: Request):
        import tomllib

        form = await request.form()
        updates: dict[str, Any] = {k: str(form[k]) for k in form}

        config_path = Path("config.toml")
        existing: dict[str, Any] = {}
        if config_path.exists():
            existing = tomllib.loads(config_path.read_text(encoding="utf-8"))

        # Apply updates — form fields use dot notation (e.g. "models.vision")
        for key, raw in updates.items():
            parts = key.split(".")
            target = existing
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            # Coerce to appropriate Python/TOML type
            if raw.lower() in ("true", "false"):
                target[parts[-1]] = raw.lower() == "true"
            elif raw.isdigit():
                target[parts[-1]] = int(raw)
            else:
                try:
                    target[parts[-1]] = float(raw)
                except ValueError:
                    target[parts[-1]] = raw

        # Serialise dict to TOML text
        def _to_toml(d: dict[str, Any], prefix: str = "") -> list[str]:
            lines: list[str] = []
            scalars = {k: v for k, v in d.items() if not isinstance(v, dict)}
            sections = {k: v for k, v in d.items() if isinstance(v, dict)}
            for k, v in scalars.items():
                if isinstance(v, bool):
                    lines.append(f"{k} = {'true' if v else 'false'}")
                elif isinstance(v, str):
                    lines.append(f'{k} = "{v}"')
                else:
                    lines.append(f"{k} = {v}")
            for k, sub in sections.items():
                section = f"{prefix}.{k}" if prefix else k
                lines.append(f"\n[{section}]")
                lines.extend(_to_toml(sub, section))
            return lines

        config_path.write_text(
            "\n".join(_to_toml(existing)) + "\n", encoding="utf-8",
        )

        return {"status": "ok", "updated_keys": list(updates.keys())}

    return app
