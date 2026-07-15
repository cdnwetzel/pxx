import pytest
from agentmemory_pkg.storage import ObservationStore

@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_memory.db"
    return ObservationStore(db_path=str(db_path))

def test_store_metadata(store: ObservationStore):
    metadata = {
        "functions": [{"name": "test_func", "line_range": (1, 10), "change": "add"}],
        "classes": [{"name": "TestClass", "line_range": (20, 30), "change": "modify"}],
        "tests": {"passed": ["test_success"], "failed": ["test_fail"]},
        "files_changed": [{"path": "test.py", "lines_added": 5, "lines_removed": 2}]
    }
    obs = store.store("test_project", "Test observation", metadata=metadata)
    assert obs.metadata == metadata

def test_get_by_function(store: ObservationStore):
    # Store observations with functions
    store.store(
        "test_project",
        "Observation 1",
        metadata={
            "functions": [{"name": "func_a", "line_range": (1, 10), "change": "add"}]
        }
    )
    store.store(
        "test_project",
        "Observation 2",
        metadata={
            "functions": [{"name": "func_b", "line_range": (1, 10), "change": "modify"}]
        }
    )

    # Retrieve by function name
    func_obs = store.get_by_function("func_a")
    assert len(func_obs) == 1
    assert func_obs[0].content == "Observation 1"

def test_get_by_file(store: ObservationStore):
    # Store observations with file changes
    store.store(
        "test_project",
        "Observation 1",
        metadata={
            "files_changed": [{"path": "file_a.py", "lines_added": 5, "lines_removed": 2}]
        }
    )
    store.store(
        "test_project",
        "Observation 2",
        metadata={
            "files_changed": [{"path": "file_b.py", "lines_added": 3, "lines_removed": 1}]
        }
    )

    # Retrieve by file path
    file_obs = store.get_by_file("file_a.py")
    assert len(file_obs) == 1
    assert file_obs[0].content == "Observation 1"

def test_metadata_persistence(store: ObservationStore):
    metadata = {
        "functions": [{"name": "persist_test", "line_range": (1, 10), "change": "add"}],
        "classes": [],
        "tests": {"passed": [], "failed": []},
        "files_changed": []
    }
    obs = store.store("test_project", "Persistence test", metadata=metadata)

    # Retrieve and verify
    retrieved = store._get_by_id(obs.id)
    assert retrieved.metadata == metadata

def test_search_with_metadata(store: ObservationStore):
    # Store observations with different metadata
    store.store(
        "test_project",
        "Observation with function",
        metadata={
            "functions": [{"name": "search_test", "line_range": (1, 10), "change": "add"}]
        }
    )
    store.store(
        "test_project",
        "Observation without function",
        metadata={}
    )

    # Search should find the first observation
    results = store.search("test_project", "function")
    assert len(results) == 1
    assert "search_test" in results[0].content

def test_metadata_schema_validation(store: ObservationStore):
    # Test with invalid metadata schema
    try:
        store.store(
            "test_project",
            "Invalid metadata",
            metadata={
                "invalid_field": "should fail validation"
            }
        )
        assert False, "Should have raised an exception for invalid metadata"
    except (ValueError, TypeError):
        pass

def test_empty_metadata(store: ObservationStore):
    # Test with empty metadata
    obs = store.store("test_project", "No metadata")
    assert obs.metadata is None

def test_large_metadata(store: ObservationStore):
    # Test with large metadata payload
    functions = [{"name": f"func_{i}", "line_range": (i, i+10), "change": "add"} for i in range(100)]
    metadata = {
        "functions": functions,
        "classes": [],
        "tests": {"passed": [], "failed": []},
        "files_changed": []
    }
    obs = store.store("test_project", "Large metadata test", metadata=metadata)
    assert len(obs.metadata["functions"]) == 100

def test_metadata_search_precision(store: ObservationStore):
    # Store observations with similar but distinct functions
    store.store(
        "test_project",
        "Similar function A",
        metadata={
            "functions": [{"name": "similar_func_a", "line_range": (1, 10), "change": "add"}]
        }
    )
    store.store(
        "test_project",
        "Similar function B",
        metadata={
            "functions": [{"name": "similar_func_b", "line_range": (1, 10), "change": "modify"}]
        }
    )

    # Search should return only the exact match
    results_a = store.get_by_function("similar_func_a")
    assert len(results_a) == 1
    assert results_a[0].content == "Similar function A"

def test_metadata_update(store: ObservationStore):
    # Store an observation, then update its metadata
    obs = store.store(
        "test_project",
        "Update test",
        metadata={
            "functions": [{"name": "update_func", "line_range": (1, 10), "change": "add"}]
        }
    )

    # Update the observation's metadata
    new_metadata = {
        "functions": [{"name": "update_func", "line_range": (1, 20), "change": "modify"}],
        "tests": {"passed": ["test_update"]}
    }

    store.store("test_project", obs.content, ttl_days=None, metadata=new_metadata)

    # Verify update
    updated = store._get_by_id(obs.id)
    assert updated.metadata["functions"][0]["change"] == "modify"
    assert updated.metadata["tests"]["passed"] == ["test_update"]
