"""Slash command handlers for memory operations."""

from .storage import ObservationStore


class CommandHandler:
    """Handle /recall, /remember, /forget commands."""

    def __init__(self, store: ObservationStore):
        self.store = store

    def recall(self, project: str, query: str, limit: int = 5) -> dict:
        """
        /recall <query> — search saved observations.

        Returns:
            {
                "query": query,
                "results": [
                    {
                        "id": "obs-xxx",
                        "content": "...",
                        "score": 0.75,
                        "created_at": "...",
                        "access_count": 3
                    },
                    ...
                ],
                "count": 3
            }
        """
        observations = self.store.get_by_project(project)
        from .search import SearchEngine

        engine = SearchEngine()
        ranked = engine.search(query, observations, limit=limit)

        return {
            "query": query,
            "results": [
                {
                    "id": obs.id,
                    "content": obs.content,
                    "score": score,
                    "created_at": obs.created_at,
                    "access_count": obs.access_count,
                }
                for obs, score in ranked
            ],
            "count": len(ranked),
        }

    def remember(self, project: str, title: str, content: str) -> dict:
        """
        /remember "title" "content" — manually save an observation.

        Returns:
            {
                "id": "obs-xxx",
                "created": true,
                "message": "Observation saved"
            }
        """
        full_content = f"{title}\n\n{content}"
        obs = self.store.store(project, full_content)

        return {
            "id": obs.id,
            "created": True,
            "message": "Observation saved",
        }

    def forget(self, project: str, obs_id: str) -> dict:
        """
        /forget <id> — delete an observation.

        Returns:
            {
                "id": "obs-xxx",
                "deleted": true,
                "message": "Observation deleted"
            }
        """
        deleted = self.store.delete(obs_id)

        return {
            "id": obs_id,
            "deleted": deleted,
            "message": "Observation deleted" if deleted else "Observation not found",
        }

    def execute(self, project: str, command: str, args: dict) -> dict:
        """Execute a slash command."""
        if command == "recall":
            query = args.get("query", "")
            limit = args.get("limit", 5)
            return self.recall(project, query, limit)

        elif command == "remember":
            title = args.get("title", "")
            content = args.get("content", "")
            return self.remember(project, title, content)

        elif command == "forget":
            obs_id = args.get("id", "")
            return self.forget(project, obs_id)

        else:
            return {"error": f"Unknown command: {command}"}
