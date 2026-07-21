"""T1 ingestion: allowlist-enforced crawl → parse → structural chunk.

The serve path (the proxy) never touches the network. The *only* component
permitted to fetch is this ingestion pipeline, and it is hard-restricted to an
allowlist of official sources enforced in code (`allowlist.py`), not by
configuration convention. Embedding + vector storage (T1b) plug in behind the
`embed` / `store` interfaces once Postgres+pgvector and a local embedding model
are installed.
"""
