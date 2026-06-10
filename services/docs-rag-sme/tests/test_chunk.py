import json
from pathlib import Path

from docs_rag_sme.ingest.chunk import chunk, chunk_pypi, chunk_sphinx

FIX = Path(__file__).parent / "fixtures"


def test_sphinx_welds_signature_to_description():
    body = (FIX / "asyncio_sample.html").read_text()
    url = "https://docs.python.org/3.12/library/asyncio-task.html"
    chunks = chunk_sphinx(url, body, content_hash="abc")

    api = {c.title: c for c in chunks if c.anchor and c.anchor.startswith("asyncio.")}
    assert "asyncio.TaskGroup" in api
    assert "asyncio.TaskGroup.create_task" in api

    # The signature and its description must live in the SAME chunk.
    create = api["asyncio.TaskGroup.create_task"]
    assert "create_task" in create.text
    assert "Create a task in this task group" in create.text
    # Version provenance carried through.
    assert create.python_version == "3.12"
    assert create.content_hash == "abc"


def test_sphinx_emits_prose_section():
    body = (FIX / "asyncio_sample.html").read_text()
    url = "https://docs.python.org/3.12/library/asyncio-task.html"
    titles = [c.title for c in chunk_sphinx(url, body)]
    assert "Task Groups" in titles


def test_chunk_ids_are_stable_and_distinct():
    body = (FIX / "asyncio_sample.html").read_text()
    url = "https://docs.python.org/3.12/library/asyncio-task.html"
    a = chunk_sphinx(url, body)
    b = chunk_sphinx(url, body)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert len({c.chunk_id for c in a}) == len(a)


def test_pypi_chunk():
    payload = json.dumps(
        {
            "info": {
                "name": "httpx",
                "version": "0.27.0",
                "summary": "The next generation HTTP client.",
                "requires_python": ">=3.8",
                "project_urls": {"Homepage": "https://www.python-httpx.org/"},
            }
        }
    )
    url = "https://pypi.org/pypi/httpx/json"
    chunks = chunk_pypi(url, payload, content_hash="h")
    assert len(chunks) == 1
    c = chunks[0]
    assert c.package == "httpx"
    assert c.package_version == "0.27.0"
    assert "next generation HTTP client" in c.text
    assert "Requires-Python: >=3.8" in c.text


def test_dispatch_by_host():
    payload = json.dumps({"info": {"name": "x", "version": "1.0", "summary": "s"}})
    assert chunk("https://pypi.org/pypi/x/json", payload)[0].package == "x"
