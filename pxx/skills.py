"""Agent skills discovery and management (Phase 5 Tier 3b).

Skills are markdown-based prompt files that guide aider workflows.
They are loaded via `/load <path>` at the start or during a session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    """Represents a single skill."""

    name: str
    path: Path
    title: str
    description: str

    def __repr__(self) -> str:
        """Format skill for display."""
        return f"{self.name:20} — {self.description}"


class SkillRegistry:
    """Discover and manage available skills."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        """Initialize skill registry.

        Args:
            skills_dir: Primary directory containing skill files. Defaults to pxx/commands/
                       User-local skills are automatically checked in ~/.config/pxx/commands/
        """
        if skills_dir is None:
            skills_dir = Path(__file__).parent / "commands"
        self.skills_dir = skills_dir
        self.user_skills_dir = Path.home() / ".config" / "pxx" / "commands"

    def discover(self) -> list[Skill]:
        """Discover all available skills.

        Scans both user-local and built-in skill directories. User skills take precedence.

        Returns:
            List of Skill objects, sorted by name.
        """
        skills = []
        seen_names: set[str] = set()

        # Check user-local skills first (~/.config/pxx/commands/)
        if self.user_skills_dir.exists():
            for skill_file in sorted(self.user_skills_dir.glob("*.md")):
                if skill_file.name == "SKILL_TEMPLATE.md":
                    continue
                skill = self._parse_skill_file(skill_file)
                if skill:
                    skills.append(skill)
                    seen_names.add(skill.name)

        # Then check built-in skills, skipping duplicates
        if self.skills_dir.exists():
            for skill_file in sorted(self.skills_dir.glob("*.md")):
                if skill_file.name == "SKILL_TEMPLATE.md":
                    continue
                skill = self._parse_skill_file(skill_file)
                if skill and skill.name not in seen_names:
                    skills.append(skill)

        return sorted(skills, key=lambda s: s.name)

    def _parse_skill_file(self, path: Path) -> Skill | None:
        """Parse a skill file to extract metadata.

        Expected format:
            # /skillname — Short description
            One-paragraph summary or longer description.

        Args:
            path: Path to skill markdown file.

        Returns:
            Skill object, or None if parsing failed.
        """
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        # Match header: # /name — description
        header_match = re.match(r"^#\s+(/[\w-]+)\s*—\s*(.+?)$", content, re.MULTILINE)
        if not header_match:
            return None

        name = header_match.group(1)
        title = header_match.group(2).strip()

        # Extract description: text after header until next section
        description = title
        # Try to get more context from the first paragraph
        paragraphs = content.split("\n\n")
        if len(paragraphs) > 1:
            # Second paragraph often has more detail
            para = paragraphs[1].strip()
            if para and not para.startswith("#"):
                description = para.split("\n")[0][:100]

        return Skill(
            name=name,
            path=path,
            title=title,
            description=description,
        )

    def get_skill(self, name: str) -> Skill | None:
        """Look up a skill by name.

        Args:
            name: Skill name (with or without leading /)

        Returns:
            Skill object, or None if not found.
        """
        if not name.startswith("/"):
            name = f"/{name}"

        skills = self.discover()
        for skill in skills:
            if skill.name == name:
                return skill

        return None

    def format_list(self) -> str:
        """Format skills list for display.

        Returns:
            Human-readable skills list.
        """
        skills = self.discover()
        if not skills:
            return "No skills available."

        lines = ["Available skills:"]
        for skill in skills:
            lines.append(f"  {skill}")

        return "\n".join(lines)
