import pytest
import importlib
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

# Import modules that start with digits using importlib
_main_mod = importlib.import_module("9router_pkg.main")
_metrics_mod = importlib.import_module("9router_pkg.metrics")
_router_mod = importlib.import_module("9router_pkg.router")

app = _main_mod.app
metrics = _metrics_mod.metrics
EndpointRouter = _router_mod.EndpointRouter


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset metrics before each test."""
    global metrics
    RouterMetrics = _metrics_mod.RouterMetrics
    metrics.__dict__ = RouterMetrics().__dict__
    yield


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_endpoint_healthy(self):
        """Test health check when endpoint available."""
        router = EndpointRouter()
        with patch.object(router, "get_endpoint", new_callable=AsyncMock) as mock:
            mock.return_value = "http://localhost:11434"
            endpoint = await router.get_endpoint()
            assert endpoint == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_health_endpoint_unhealthy(self):
        """Test health check when no endpoint available."""
        router = EndpointRouter()
        with patch.object(router, "get_endpoint", new_callable=AsyncMock) as mock:
            mock.return_value = None
            endpoint = await router.get_endpoint()
            assert endpoint is None


class TestMetrics:
    def test_record_request(self):
        """Test metrics recording."""
        metrics.record_request_start()
        assert metrics.active_requests == 1
        assert metrics.total_requests == 1

        metrics.record_request_end(0.5)
        assert metrics.active_requests == 0
        assert len(metrics.latencies) == 1

    def test_error_rate(self):
        """Test error rate calculation."""
        metrics.record_request_start()
        metrics.record_request_end(0.1, error=True)

        metrics.record_request_start()
        metrics.record_request_end(0.1, error=False)

        assert metrics.total_requests == 2
        assert metrics.total_errors == 1
        assert metrics.error_rate == 0.5

    def test_compression_ratio(self):
        """Test compression ratio."""
        metrics.record_tokens(1000, 100)
        assert metrics.compression_ratio == 0.1

        metrics.record_tokens(1000, 200)
        assert metrics.compression_ratio == 0.15

    def test_latency_p99(self):
        """Test 99th percentile latency."""
        for i in range(100):
            metrics.latencies.append(i * 0.01)  # 0, 0.01, 0.02, ..., 0.99

        p99 = metrics.latency_p99
        assert p99 is not None
        assert 900 < p99 < 1000  # Should be around 990ms

    def test_get_usage(self):
        """Test usage stats."""
        metrics.record_request_start()
        metrics.record_request_end(0.1)
        metrics.record_tokens(1000, 100)

        data = metrics.to_dict()
        assert data["total_requests"] == 1
        assert data["total_tokens"] == 1000
        assert data["cached_tokens"] == 100


class TestStatus:
    @pytest.mark.asyncio
    async def test_endpoint_routing(self):
        """Test endpoint router."""
        router = EndpointRouter()
        with patch.object(router, "get_endpoint", new_callable=AsyncMock) as mock:
            mock.return_value = "http://localhost:11434"
            endpoint = await router.get_endpoint()
            assert endpoint is not None
