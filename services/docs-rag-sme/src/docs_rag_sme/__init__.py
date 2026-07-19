"""docs-rag-sme: version-aware docs-RAG SME proxy for local Aider/pxx.

T0 (this milestone) is a *transparent* OpenAI-API forwarder: it relays every
request verbatim to a downstream OpenAI-compatible server (the gpu-node-1 vLLM /
audit-proxy) and streams the response back unchanged. No retrieval, no cloud.

The point of T0 is to prove the seam: pxx points `PXX_VLLM_URL` at this proxy
and nothing else changes. Retrieval (T2+) plugs in at the marked hook in
`app.chat_completions` without touching the forwarding machinery.
"""

__version__ = "0.0.1"
