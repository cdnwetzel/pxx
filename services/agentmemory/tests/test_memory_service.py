import pytest
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from agentmemory_pkg.main import app
from agentmemory_pkg.storage import ObservationStore
from agentmemory_pkg.search import BM25Ranker, SearchEngine
from agentmemory_pkg.commands import CommandHandler


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        yield db_path


@pytest.fixture
def store(temp_db):
    """Create a test store."""
    return ObservationStore(db_path=temp_db)


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestStorage:
    def test_store_observation(self, store):
        """Test storing an observation."""
        obs = store.store("project1", "Some content")
        assert obs.id is not None
        assert obs.project == "project1"
        assert obs.content == "Some content"
        assert obs.access_count == 0

    def test_store_duplicate(self, store):
        """Test storing duplicate observation."""
        obs1 = store.store("project1", "Content A")
        obs2 = store.store("project1", "Content A")

        # Should be same observation
        assert obs1.id == obs2.id
        assert obs2.access_count == 1

    def test_get_by_project(self, store):
        """Test retrieving observations by project."""
        store.store("proj1", "Content 1")
        store.store("proj1", "Content 2")
        store.store("proj2", "Content 3")

        proj1_obs = store.get_by_project("proj1")
        assert len(proj1_obs) == 2

        proj2_obs = store.get_by_project("proj2")
        assert len(proj2_obs) == 1

    def test_search(self, store):
        """Test searching observations."""
        store.store("proj1", "Python is great")
        store.store("proj1", "JavaScript rocks")
        store.store("proj1", "Python and JS")

        results = store.search("proj1", "python")
        assert len(results) >= 2

    def test_delete(self, store):
        """Test deleting an observation."""
        obs = store.store("proj1", "To delete")
        assert store.delete(obs.id)
        assert not store.delete(obs.id)

    def test_delete_project(self, store):
        """Test deleting all observations in a project."""
        store.store("proj1", "Content 1")
        store.store("proj1", "Content 2")
        store.store("proj2", "Content 3")

        deleted = store.delete_project("proj1")
        assert deleted == 2

        proj1_obs = store.get_by_project("proj1")
        assert len(proj1_obs) == 0

    def test_project_stats(self, store):
        """Test project statistics."""
        store.store("proj1", "Short")
        store.store("proj1", "Much longer content here")

        stats = store.get_project_stats("proj1")
        assert stats["observation_count"] == 2
        assert stats["size_mb"] > 0


class TestBM25:
    def test_basic_scoring(self):
        """Test basic BM25 scoring."""
        ranker = BM25Ranker()
        docs = [
            "Python is a programming language",
            "JavaScript is for web development",
            "Python and JavaScript are popular",
        ]
        ranker.index_documents(docs)

        score1 = ranker.score("Python", docs[0])
        score2 = ranker.score("JavaScript", docs[1])

        assert score1 > 0
        assert score2 > 0

    def test_ranking(self):
        """Test document ranking."""
        ranker = BM25Ranker()
        docs = [
            "cat dog pet animal",
            "cat sat on mat",
            "dog barked loudly",
        ]
        ranker.index_documents(docs)

        query = "cat pet"
        ranked = [(i, ranker.score(query, doc)) for i, doc in enumerate(docs)]
        ranked.sort(key=lambda x: x[1], reverse=True)

        # Document 0 should rank highest (has both "cat" and "pet")
        assert ranked[0][0] == 0


class TestSearchEngine:
    def test_search_with_ranking(self, store):
        """Test search engine ranking."""
        engine = SearchEngine()

        obs1 = store.store("proj1", "Python programming language")
        obs2 = store.store("proj1", "JavaScript for web")
        obs3 = store.store("proj1", "Python and JavaScript comparison")

        obs_list = [obs1, obs2, obs3]
        ranked = engine.search("Python", obs_list, limit=3)

        assert len(ranked) > 0
        # First result should be highly relevant
        assert ranked[0][1] > 0


class TestCommandHandler:
    def test_remember_command(self, store):
        """Test /remember command."""
        handler = CommandHandler(store)
        result = handler.remember("proj1", "My Title", "My content here")

        assert result["created"] is True
        assert result["id"] is not None

    def test_recall_command(self, store):
        """Test /recall command."""
        handler = CommandHandler(store)

        # Store some observations
        handler.remember("proj1", "Title 1", "Content about Python")
        handler.remember("proj1", "Title 2", "Content about JavaScript")

        # Recall
        result = handler.recall("proj1", "Python", limit=10)
        assert result["query"] == "Python"
        assert len(result["results"]) > 0

    def test_forget_command(self, store):
        """Test /forget command."""
        handler = CommandHandler(store)

        result = handler.remember("proj1", "To forget", "This will be deleted")
        obs_id = result["id"]

        forget_result = handler.forget("proj1", obs_id)
        assert forget_result["deleted"] is True

        # Verify it's gone
        recall = handler.recall("proj1", "forget")
        assert len(recall["results"]) == 0


class TestAPI:
    def test_health(self, client):
        """Test health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_store_observation_api(self, client):
        """Test storing observation via API."""
        response = client.post(
            "/observations", json={"project": "proj1", "content": "Test content"}
        )
        assert response.status_code == 200
        assert response.json()["id"] is not None

    def test_search_api(self, client):
        """Test search via API."""
        # Store first
        client.post(
            "/observations", json={"project": "proj1", "content": "Python programming"}
        )

        # Search
        response = client.post("/search", json={"project": "proj1", "query": "Python"})
        assert response.status_code == 200
        assert len(response.json()["results"]) > 0

    def test_execute_command_api(self, client):
        """Test command execution via API."""
        response = client.post(
            "/command",
            json={
                "project": "proj1",
                "command": "remember",
                "args": {"title": "Test", "content": "Content"},
            },
        )
        assert response.status_code == 200
        assert response.json()["id"] is not None

    def test_project_stats_api(self, client):
        """Test project stats endpoint."""
        # Store something first
        client.post(
            "/observations", json={"project": "proj1", "content": "Some content"}
        )

        response = client.get("/project/proj1/stats")
        assert response.status_code == 200
        assert response.json()["observation_count"] >= 1

    def test_delete_project_api(self, client):
        """Test project deletion via API."""
        # Store something
        client.post("/observations", json={"project": "proj1", "content": "To delete"})

        response = client.delete("/project/proj1")
        assert response.status_code == 200
        assert response.json()["deleted"] > 0
