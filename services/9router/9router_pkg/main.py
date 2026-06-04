import json
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from .router import EndpointRouter
from .metrics import metrics
from .memory_middleware import MemoryMiddleware


router = EndpointRouter()
memory_middleware: MemoryMiddleware | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    global memory_middleware
    print("9router starting...")
    # Initialize memory middleware (enabled by default, disable via env var)
    if os.getenv("PXX_MEMORY_ENABLED", "1") == "1":
        memory_middleware = MemoryMiddleware()
        print("9router: memory middleware enabled")
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
    """Proxy chat completion requests with memory middleware."""
    try:
        body_bytes = await request.body()
        request_body = json.loads(body_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    metrics.record_request_start()
    start = time.time()

    try:
        # Apply memory middleware: inject context and detect commands
        if memory_middleware:
            request_body = await memory_middleware.on_request(request_body)

        # Check for slash commands (handled by middleware)
        cmd_result = request_body.pop("_pxx_slash_command", None)
        if cmd_result:
            cmd_name, cmd_args = cmd_result
            # Execute slash command and return synthetic response
            result = await memory_middleware.handle_slash_command(cmd_name, cmd_args)
            elapsed = time.time() - start
            metrics.record_request_end(elapsed, error=False)

            # Return as LLM response format
            return JSONResponse(
                {
                    "id": f"pxx-cmd-{cmd_name}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": request_body.get("model", "unknown"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": result["message"],
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            )

        # Re-serialize modified request for proxy
        body_bytes = json.dumps(request_body).encode()

        # Forward to LLM endpoint
        status, headers, content = await router.proxy_request(
            method="POST",
            path="/v1/chat/completions",
            headers=dict(request.headers),
            body=body_bytes,
        )

        elapsed = time.time() - start
        metrics.record_request_end(elapsed, error=status >= 400)

        # Parse response for middleware processing
        response_body: dict = {}
        try:
            if status == 200 and content:
                response_body = json.loads(content)
        except Exception:
            pass

        # Apply memory middleware: capture observations from response
        if memory_middleware and status == 200:
            await memory_middleware.on_response(request_body, response_body)

        # Track tokens if present in response
        try:
            if status == 200 and response_body:
                if "usage" in response_body:
                    total = response_body["usage"].get("total_tokens", 0)
                    # Estimate cached tokens (would need actual cache tracking)
                    cached = int(total * 0.1)  # placeholder
                    metrics.record_tokens(total, cached)
        except Exception:
            pass

        filtered_headers = {
            k: v for k, v in headers.items() if k.lower() != "content-encoding"
        }
        return JSONResponse(
            response_body if response_body else {},
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
