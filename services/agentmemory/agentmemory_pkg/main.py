import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from .storage import ObservationStore
from .commands import CommandHandler
from .search import SearchEngine


# Global instances
store = ObservationStore()
handler = CommandHandler(store)
search_engine = SearchEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    print("agentmemory starting...")
    yield
    print("agentmemory shutting down...")


app = FastAPI(title="agentmemory", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}


@app.post("/observations")
async def store_observation(request: Request):
    """Store a new observation."""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    project = data.get("project", "default")  # Use "default" if not specified
    content = data.get("content")

    if not content:
        raise HTTPException(status_code=400, detail="Missing content")

    obs = store.store(project, content)

    return {
        "id": obs.id,
        "project": obs.project,
        "created_at": obs.created_at,
        "message": "Observation stored",
    }


@app.post("/search")
async def search_observations(request: Request):
    """Search observations in a project."""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    project = data.get("project", "default")  # Use "default" if not specified
    query = data.get("query", "")
    limit = data.get("limit", 10)

    observations = store.get_by_project(project)
    ranked = search_engine.search(query, observations, limit=limit, min_score=0.0)

    return {
        "query": query,
        "project": project,
        "results": [
            {
                "id": obs.id,
                "content": obs.content,
                "score": score,
                "created_at": obs.created_at,
                "last_accessed": obs.last_accessed,
                "access_count": obs.access_count,
            }
            for obs, score in ranked
        ],
        "count": len(ranked),
    }


@app.post("/inject")
async def inject_observations(request: Request):
    """Get observations for context injection."""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    project = data.get("project", "default")  # Use "default" if not specified
    query = data.get("query", "")
    limit = data.get("limit", 5)
    max_chars = data.get("max_chars", 8000)

    observations = store.get_by_project(project)
    ranked = search_engine.search(query, observations, limit=limit)

    # Build context, respecting char limit
    context = []
    total_chars = 0
    for obs, score in ranked:
        obs_text = f"[{obs.id}] {obs.content} (score: {score:.2f})"
        if total_chars + len(obs_text) > max_chars:
            break
        context.append(obs_text)
        total_chars += len(obs_text)

    return {
        "project": project,
        "query": query,
        "observations": context,
        "count": len(context),
        "size_chars": total_chars,
    }


@app.get("/project/{project}/stats")
async def project_stats(project: str):
    """Get statistics for a project."""
    stats = store.get_project_stats(project)
    return stats


@app.delete("/project/{project}")
async def delete_project(project: str):
    """Delete all observations for a project."""
    count = store.delete_project(project)
    return {
        "project": project,
        "deleted": count,
        "message": f"Deleted {count} observations",
    }


@app.post("/command")
async def execute_command(request: Request):
    """Execute a slash command."""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    project = data.get("project")
    command = data.get("command")
    args = data.get("args", {})

    if not project or not command:
        raise HTTPException(status_code=400, detail="Missing project or command")

    result = handler.execute(project, command, args)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@app.get("/status")
async def status():
    """Get service status."""
    return {
        "service": "agentmemory",
        "version": "0.1.0",
        "status": "healthy",
    }


def main():
    """Run the agentmemory service."""
    import uvicorn

    host = os.getenv("PXX_MEMORY_HOST", "127.0.0.1")
    port = int(os.getenv("PXX_MEMORY_PORT", "3111"))

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
