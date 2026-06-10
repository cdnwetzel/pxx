"""9router: OpenAI-compatible proxy with memory middleware."""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .memory_middleware import MemoryMiddleware

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

OLLAMA_BASE = os.getenv(
    "PXX_OLLAMA_BASE", "http://localhost:11434"
)

# Memory middleware (optional, disabled by default via env var)
memory_middleware: Optional[MemoryMiddleware] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global memory_middleware
    logger.info(f"9router starting, forwarding to {OLLAMA_BASE}")

    # Initialize memory middleware if enabled
    if os.getenv("PXX_MEMORY_ENABLED", "1") == "1":
        memory_middleware = MemoryMiddleware()
        logger.info("9router: memory middleware enabled")

    yield
    logger.info("9router shutting down")


app = FastAPI(title="9router", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            if resp.status_code == 200:
                return {"status": "healthy", "endpoint": OLLAMA_BASE}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
    return JSONResponse({"status": "unhealthy"}, status_code=503)


@app.get("/test")
async def test_endpoint():
    """Test endpoint."""
    return {"status": "ok", "service": "9router"}


@app.get("/v1/models")
async def list_models():
    """List available models."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                return {
                    "object": "list",
                    "data": [{"id": m["name"], "object": "model"} for m in models],
                }
    except Exception as e:
        logger.error(f"list_models error: {e}")
    return {"object": "list", "data": []}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy chat completions to Ollama with optional memory middleware."""
    try:
        body_bytes = await request.body()
        request_body = json.loads(body_bytes)
        logger.debug(f"Request received: model={request_body.get('model')}")

        # Apply memory middleware: inject context before sending to LLM
        if memory_middleware:
            request_body = await memory_middleware.on_request(request_body)

        # Check for slash commands that were marked by middleware
        cmd_result = request_body.pop("_pxx_slash_command", None)
        if cmd_result:
            cmd_name, cmd_args = cmd_result
            # Execute slash command and return synthetic response
            result = await memory_middleware.handle_slash_command(cmd_name, cmd_args)
            return JSONResponse(
                {
                    "id": "pxx-cmd",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": request_body.get("model", "unknown"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": result.get(
                                    "message", result.get("error", "")
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                }
            )

        # Forward to Ollama (disable streaming to get complete responses)
        logger.debug(f"Forwarding to {OLLAMA_BASE}/api/chat")
        request_body["stream"] = False  # Ensure we get complete response

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE}/api/chat",
                json=request_body,
            )

        logger.debug(f"Ollama responded with status {resp.status_code}")

        if resp.status_code != 200:
            logger.error(f"Ollama error: {resp.text}")
            return JSONResponse({"error": "Ollama error"}, status_code=resp.status_code)

        # Parse response from Ollama
        try:
            ollama_response = resp.json()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Ollama response: {e}")
            return JSONResponse({"error": "Invalid JSON from Ollama"}, status_code=502)

        # Convert Ollama response to OpenAI format
        message_content = ollama_response.get("message", {}).get("content", "")
        openai_resp = {
            "id": "chatcmpl-9router",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request_body.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": message_content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

        # Apply memory middleware: capture observations from response
        if memory_middleware:
            await memory_middleware.on_response(request_body, openai_resp)

        logger.debug(f"Returning response with content: {message_content[:50]}...")
        return JSONResponse(openai_resp)

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


def main():
    """Entry point for 9router console script."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=20128)


if __name__ == "__main__":
    main()
