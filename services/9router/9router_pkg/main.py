import json
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from .router import EndpointRouter
from .metrics import metrics


router = EndpointRouter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    print("9router starting...")
    yield
    print("9router shutting down...")


app = FastAPI(title="9router", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    endpoint = await router.get_endpoint()
    if endpoint:
        return {"status": "healthy", "endpoint": endpoint}
    return JSONResponse({"status": "unhealthy", "endpoint": None}, status_code=503)


@app.get("/v1/models")
async def list_models():
    """List available models."""
    models = await router.list_models()
    return models


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy chat completion requests."""
    try:
        body = await request.body()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    metrics.record_request_start()
    start = time.time()

    try:
        status, headers, content = await router.proxy_request(
            method="POST",
            path="/v1/chat/completions",
            headers=dict(request.headers),
            body=body,
        )

        elapsed = time.time() - start
        metrics.record_request_end(elapsed, error=status >= 400)

        # Track tokens if present in response
        try:
            if status == 200:
                resp_json = json.loads(content)
                if "usage" in resp_json:
                    total = resp_json["usage"].get("total_tokens", 0)
                    # Estimate cached tokens (would need actual cache tracking)
                    cached = int(total * 0.1)  # placeholder
                    metrics.record_tokens(total, cached)
        except Exception:
            pass

        filtered_headers = {
            k: v for k, v in headers.items() if k.lower() != "content-encoding"
        }
        return JSONResponse(
            json.loads(content) if content else {},
            status_code=status,
            headers=filtered_headers,
        )

    except Exception as e:
        elapsed = time.time() - start
        metrics.record_request_end(elapsed, error=True)
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/v1/models/{model_name}")
async def get_model(model_name: str):
    """Get model info."""
    endpoint = await router.get_endpoint()
    if not endpoint:
        raise HTTPException(status_code=503, detail="No endpoints available")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{endpoint}/api/show/{model_name}")
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="Model not found")


@app.get("/v1/usage")
async def get_usage():
    """Get router usage stats."""
    return metrics.to_dict()


@app.get("/status")
async def status():
    """Get router status and metrics."""
    endpoint = await router.get_endpoint()
    return {
        "available": endpoint is not None,
        "endpoint": endpoint,
        "primary": router.primary,
        "fallbacks": router.fallbacks,
        "metrics": metrics.to_dict(),
    }


def main():
    """Run the 9router service."""
    import uvicorn

    host = os.getenv("PXX_ROUTER_HOST", "127.0.0.1")
    port = int(os.getenv("PXX_ROUTER_PORT", "20128"))

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
