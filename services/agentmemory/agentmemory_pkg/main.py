import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from .storage import ObservationStore
from .commands import CommandHandler
from .search import SearchEngine
from .cache import SearchCache
from .cleanup import CleanupManager
from .archive import ArchiveManager
from .vector_index import VectorIndex


# Global instances
store = ObservationStore(
    default_ttl_days=int(os.environ.get("AGENTMEMORY_RETENTION_DAYS", "90"))
)
handler = CommandHandler(store)

# Create vector index with persistence support
vector_index = VectorIndex()
search_engine = SearchEngine(store=store)
search_engine.vector_index = vector_index  # Share the vector index instance

search_cache = SearchCache(maxsize=128)
archive_manager = ArchiveManager()
cleanup_manager = CleanupManager(
    store,
    interval_seconds=int(os.environ.get("AGENTMEMORY_CLEANUP_INTERVAL", "3600")),
    enabled=os.environ.get("AGENTMEMORY_CLEANUP_ENABLED", "true").lower() == "true",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    print("agentmemory starting...")

    # Load persisted vector index
    index_dir = Path.home() / ".pxx" / "vector_index"
    if index_dir.exists():
        if vector_index.load(str(index_dir)):
            print(f"Loaded vector index from {index_dir}")
        else:
            print("Failed to load vector index, will rebuild")

    # Load persisted BM25 index
    if search_engine.load_bm25_index_from_store():
        print("Loaded BM25 index from database")

    cleanup_manager.start()
    yield
    print("agentmemory shutting down...")
    cleanup_manager.stop()

    # Save indexes on shutdown
    index_dir = Path.home() / ".pxx" / "vector_index"
    if vector_index.save(str(index_dir)):
        print(f"Saved vector index to {index_dir}")

    if search_engine.save_bm25_index_to_store():
        print("Saved BM25 index to database")


app = FastAPI(title="agentmemory", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "1.0.0"}


@app.post("/observations")
async def store_observation(request: Request):
    """Store a new observation."""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    project = data.get("project", "default")
    content = data.get("content")

    if not content:
        raise HTTPException(status_code=400, detail="Missing content")

    obs = store.store(project, content)
    search_cache.invalidate_project(project)
    search_engine.invalidate_bm25_index()  # Invalidate on new observation

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

    project = data.get("project", "default")
    query = data.get("query", "")
    limit = data.get("limit", 10)

    # Check cache first
    cached = search_cache.get(project, query, limit, min_score=0.0)
    if cached:
        return cached

    observations = store.get_by_project(project)
    ranked = search_engine.search(query, observations, limit=limit, min_score=0.0)

    result = {
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

    # Cache the result
    search_cache.set(project, query, result, limit)

    return result


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
    search_cache.invalidate_project(project)
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


@app.delete("/forget/{observation_id}")
async def forget_observation(observation_id: str):
    """Delete a specific observation (forget it)."""
    # Get observation first to find its project for cache invalidation
    obs = store._get_by_id(observation_id)
    deleted = store.delete(observation_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Observation {observation_id} not found"
        )

    # Invalidate cache and indexes if deletion succeeded
    if obs:
        search_cache.invalidate_project(obs.project)
        search_engine.invalidate_bm25_index()

    return {
        "id": observation_id,
        "message": "Observation deleted",
    }


@app.get("/metrics")
async def metrics():
    """Get service metrics and performance statistics."""
    return {
        "service": "agentmemory",
        "version": "1.0.0",
        "cache": {
            "size": len(search_cache._cache),
            "maxsize": search_cache.maxsize,
            "utilization": f"{len(search_cache._cache) / search_cache.maxsize * 100:.1f}%",
        },
        "status": "healthy",
    }


@app.get("/status")
async def status():
    """Get service status."""
    return {
        "service": "agentmemory",
        "version": "1.0.0",
        "status": "healthy",
    }


@app.get("/cleanup")
async def cleanup_status(dry_run: bool = True):
    """Get cleanup status or perform cleanup.

    Args:
        dry_run: If True, only report what would be deleted
    """
    result = store.cleanup_expired(dry_run=dry_run, archive_manager=archive_manager)
    return result


@app.post("/cleanup")
async def trigger_cleanup(request: Request):
    """Trigger immediate cleanup of expired observations."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    dry_run = data.get("dry_run", False)
    result = store.cleanup_expired(dry_run=dry_run, archive_manager=archive_manager)

    # Invalidate BM25 index on cleanup if not dry run
    if not dry_run and result.get("deleted_count", 0) > 0:
        search_engine.invalidate_bm25_index()
        search_cache._cache.clear()  # Clear search cache too

    return {
        "cleanup_triggered": True,
        "dry_run": dry_run,
        "result": result,
    }


@app.get("/retention/config")
async def get_retention_config():
    """Get retention configuration."""
    return {
        "default_ttl_days": store.default_ttl_days,
        "project_overrides": store.project_ttls.copy(),
        "cleanup_enabled": cleanup_manager.enabled,
        "cleanup_interval_seconds": cleanup_manager.interval_seconds,
        "cleanup_stats": cleanup_manager.get_stats(),
    }


@app.post("/retention/config")
async def set_retention_config(request: Request):
    """Set retention configuration for a project."""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    project = data.get("project")
    ttl_days = data.get("ttl_days")

    if not project or ttl_days is None:
        raise HTTPException(
            status_code=400,
            detail="Missing project or ttl_days",
        )

    store.set_project_ttl(project, ttl_days)

    return {
        "project": project,
        "ttl_days": ttl_days if ttl_days > 0 else "default",
        "message": "Retention config updated",
    }


@app.get("/archive/list")
async def list_archives():
    """List all archives."""
    archives = archive_manager.list_archives()
    return {
        "archives": archives,
        "count": len(archives),
    }


@app.get("/archive/stats")
async def archive_stats():
    """Get archive statistics."""
    stats = archive_manager.get_archive_stats()
    return stats


@app.get("/archive/search")
async def search_archive(query: str, limit: int = 10):
    """Search archived observations.

    Args:
        query: Search query (substring)
        limit: Max results to return
    """
    results = archive_manager.search_archive(query, limit=limit)
    return {
        "query": query,
        "results": results,
        "count": len(results),
    }


def main():
    """Run the agentmemory service."""
    import uvicorn

    host = os.getenv("PXX_MEMORY_HOST", "127.0.0.1")
    port = int(os.getenv("PXX_MEMORY_PORT", "3111"))

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
