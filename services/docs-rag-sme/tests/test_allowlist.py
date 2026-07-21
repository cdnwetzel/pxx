import pytest

from docs_rag_sme.ingest.allowlist import (
    DisallowedURL,
    ensure_allowed,
    is_allowed,
    package_of,
    python_version_of,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://docs.python.org/3.12/library/asyncio-task.html",
        "https://docs.python.org/3/library/asyncio.html",
        "https://peps.python.org/pep-0008/",
        "https://pypi.org/pypi/httpx/json",
        "https://pypi.org/pypi/httpx/0.27.0/json",
    ],
)
def test_allowed(url):
    assert is_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.python.org/3/library/asyncio.html",  # not https
        "https://evil.com/docs.python.org",
        "https://docs.python.org.evil.com/3/",
        "https://pypi.org/project/httpx/",  # project page, not json api
        "https://pypi.org/simple/httpx/",
        "https://github.com/anything",
        "file:///etc/passwd",
        "not a url",
    ],
)
def test_disallowed(url):
    assert not is_allowed(url)
    with pytest.raises(DisallowedURL):
        ensure_allowed(url)


def test_python_version_extraction():
    assert python_version_of("https://docs.python.org/3.12/library/x.html") == "3.12"
    assert python_version_of("https://docs.python.org/3/library/x.html") == "3"
    assert python_version_of("https://peps.python.org/pep-0008/") is None
    assert python_version_of("https://pypi.org/pypi/httpx/json") is None


def test_package_extraction():
    assert package_of("https://pypi.org/pypi/httpx/json") == "httpx"
    assert package_of("https://pypi.org/pypi/httpx/0.27.0/json") == "httpx"
    assert package_of("https://docs.python.org/3/x.html") is None
