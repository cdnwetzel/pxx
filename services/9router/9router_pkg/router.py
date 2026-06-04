import httpx
import os
import json
from typing import Optional


class EndpointRouter:
    """Route requests to primary endpoint with fallback chains."""

    def __init__(self):
        # Primary endpoint from env var or default
        self.primary = os.getenv(
            "PXX_ROUTER_PRIMARY", "http://workstation.splawoffice.local:11434"
        )

        # Fallback endpoints (comma-separated)
        fallbacks = os.getenv("PXX_ROUTER_FALLBACKS", "")
        self.fallbacks = [f.strip() for f in fallbacks.split(",") if f.strip()]

        self.timeout = 30.0

    async def get_endpoint(self) -> Optional[str]:
        """Find first reachable endpoint."""
        endpoints = [self.primary] + self.fallbacks

        async with httpx.AsyncClient(timeout=5.0) as client:
            for endpoint in endpoints:
                try:
                    response = await client.get(
                        f"{endpoint}/api/tags", follow_redirects=True
                    )
                    if response.status_code == 200:
                        return endpoint
                except Exception:
                    pass

        return None

    async def proxy_request(
        self,
        method: str,
        path: str,
        headers: dict,
        body: Optional[bytes] = None,
    ) -> tuple[int, dict, bytes]:
        """Proxy a request to the available endpoint."""
        endpoint = await self.get_endpoint()
        if not endpoint:
            return 503, {}, json.dumps({"error": "No endpoints available"}).encode()

        url = f"{endpoint}{path}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                elif method == "POST":
                    resp = await client.post(url, headers=headers, content=body)
                else:
                    return 405, {}, json.dumps({"error": "Method not allowed"}).encode()

                return resp.status_code, dict(resp.headers), resp.content
        except Exception as e:
            return 502, {}, json.dumps({"error": str(e)}).encode()

    async def list_models(self) -> dict:
        """Get available models from primary endpoint."""
        endpoint = await self.get_endpoint()
        if not endpoint:
            return {"models": []}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{endpoint}/api/tags")
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass

        return {"models": []}
