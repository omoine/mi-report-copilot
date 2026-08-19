"""FastAPI application: routes and static hosting for the MI Report Copilot POC."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import data_access, llm_client, orchestrator, saved_views

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="MI Report Copilot (POC)", version="0.1.0")

_provider: llm_client.LLMProvider | None = None


def get_provider() -> llm_client.LLMProvider:
    """Build the provider on first use so the app still starts without a key."""
    global _provider
    if _provider is None:
        try:
            _provider = llm_client.build_provider()
        except llm_client.LLMError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _provider


class QueryRequest(BaseModel):
    session_id: str | None = None
    query: str


class SessionRequest(BaseModel):
    session_id: str


class RefineRequest(BaseModel):
    session_id: str
    instruction: str


@app.get("/api/health")
def health() -> dict:
    """Report what the app can see, including whether a key is configured."""
    import os

    views = {v: len(data_access.get_frame(v)) for v in data_access.VIEW_SHEETS}
    key = os.getenv("OPENAI_API_KEY")
    azure = bool(os.getenv("AZURE_OPENAI_ENDPOINT"))
    # A key that is neither an "sk-" key nor paired with an Azure endpoint will
    # fail on first use - say so on the status line rather than at query time.
    key_usable = bool(key) and (azure or key.startswith("sk-"))
    return {
        "status": "ok",
        "api_key_configured": key_usable,
        "api_key_present_but_unusable": bool(key) and not key_usable,
        "provider": "azure" if azure else "openai",
        "model": (os.getenv("AZURE_OPENAI_DEPLOYMENT") if azure else None) or llm_client.DEFAULT_MODEL,
        "data_file": data_access.DATA_FILE.name,
        "data_classification": data_access.get_metadata()["data_classification"],
        "views": views,
    }


@app.get("/api/schema")
def schema() -> dict:
    """Expose the schema so the UI can show users what they can ask about."""
    return {"views": data_access.schema_summary(),
            "controls": data_access.get_metadata()["view_controls"]}


@app.post("/api/query")
def query(req: QueryRequest) -> dict:
    session = orchestrator.get_session(req.session_id)
    try:
        result = orchestrator.interpret(session, req.query, get_provider())
    except orchestrator.OrchestratorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm_client.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"session_id": session.id, **result}


@app.post("/api/confirm")
def confirm(req: SessionRequest) -> dict:
    session = orchestrator.get_session(req.session_id)
    try:
        result = orchestrator.build_report(session, get_provider())
    except orchestrator.OrchestratorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm_client.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"session_id": session.id, **result}


@app.post("/api/refine")
def refine(req: RefineRequest) -> dict:
    session = orchestrator.get_session(req.session_id)
    try:
        result = orchestrator.refine(session, req.instruction, get_provider())
    except orchestrator.OrchestratorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm_client.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"session_id": session.id, **result}


@app.post("/api/export")
def export(req: SessionRequest) -> dict:
    session = orchestrator.get_session(req.session_id)
    try:
        return orchestrator.export(session)
    except orchestrator.OrchestratorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reset")
def reset(req: SessionRequest) -> dict:
    session = orchestrator.reset_session(req.session_id)
    return {"session_id": session.id, "state": session.state}


# --------------------------------------------------------------------------
# Saved views
# --------------------------------------------------------------------------

class SaveViewRequest(BaseModel):
    session_id: str
    name: str
    description: str = ""
    overwrite: bool = False


class LoadViewRequest(BaseModel):
    session_id: str | None = None
    view_id: str


@app.get("/api/views")
def list_views(search: str | None = None) -> dict:
    return {"views": saved_views.list_views(search)}


@app.post("/api/views/save")
def save_view(req: SaveViewRequest) -> dict:
    session = orchestrator.get_session(req.session_id)
    try:
        return orchestrator.save_current(session, req.name, req.description, req.overwrite)
    except (orchestrator.OrchestratorError, saved_views.SavedViewError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/views/load")
def load_view(req: LoadViewRequest) -> dict:
    session = orchestrator.get_session(req.session_id)
    try:
        result = orchestrator.load_saved(session, req.view_id, get_provider())
    except (orchestrator.OrchestratorError, saved_views.SavedViewError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm_client.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"session_id": session.id, **result}


@app.delete("/api/views/{view_id}")
def delete_view(view_id: str) -> dict:
    try:
        saved_views.delete_view(view_id)
    except saved_views.SavedViewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": view_id}


MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def _safe_export_path(filename: str) -> Path:
    """Resolve a filename inside the export directory, rejecting traversal."""
    export_dir = orchestrator.EXPORT_DIR.resolve()
    candidate = (export_dir / Path(filename).name).resolve()
    if candidate.parent != export_dir or not candidate.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return candidate


@app.get("/api/chart/{filename}")
def chart(filename: str) -> FileResponse:
    return FileResponse(_safe_export_path(filename), media_type="image/png")


@app.get("/api/download/{filename}")
def download(filename: str) -> FileResponse:
    path = _safe_export_path(filename)
    media = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
