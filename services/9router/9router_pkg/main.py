"""9router: Simple OpenAI-compatible proxy."""

import json
import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import requests

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

OLLAMA_BASE = os.getenv("PXX_OLLAMA_BASE", "http://workstation.splawoffice.local:11434")

app = FastAPI(title="9router", version="0.1.0")
print(f"[STARTUP] 9router app created, forwarding to {OLLAMA_BASE}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
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
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
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
    """Proxy chat completions to Ollama."""
    try:
        body_bytes = await request.body()
        request_body = json.loads(body_bytes)
        logger.debug(f"Request received: model={request_body.get('model')}")

        # Forward to Ollama (disable streaming to get complete responses)
        logger.debug(f"Forwarding to {OLLAMA_BASE}/api/chat")
        request_body["stream"] = False  # Ensure we get complete response
        resp = requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json=request_body,
            timeout=300,
        )
        logger.debug(f"Ollama responded with status {resp.status_code}")

        if resp.status_code != 200:
            return JSONResponse({"error": "Ollama error"}, status_code=resp.status_code)

        # Parse single JSON response from Ollama
        try:
            last_response = resp.json()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Ollama response: {e}")
            return JSONResponse({"error": "Invalid JSON from Ollama"}, status_code=502)

        # Convert Ollama response to OpenAI format
        message_content = last_response.get("message", {}).get("content", "")
        openai_resp = {
            "id": "chatcmpl-9router",
            "object": "chat.completion",
            "created": int(__import__("time").time()),
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
        logger.debug(f"Returning response with content: {message_content[:50]}...")
        return JSONResponse(openai_resp)

    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {e}")
        return JSONResponse({"error": f"Ollama unreachable: {e}"}, status_code=502)
    except json.JSONDecodeError as e:
        logger.error(f"JSON error: {e}")
        return JSONResponse({"error": f"Invalid request JSON: {e}"}, status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=20128)
