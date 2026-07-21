"""Tests for skill discovery and management (Phase 5 Tier 3b)."""

from __future__ import annotations

from pathlib import Path

from pxx.skills import Skill, SkillRegistry


class TestSkillParsing:
    """Tests for parsing skill markdown files."""

    def test_parse_valid_skill(self, tmp_path: Path) -> None:
        """Test parsing a valid skill file."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()
        skill_file = skill_dir / "test.md"
        skill_file.write_text(
            "# /test — Write parametrized pytest tests\n\n"
            "Write pytest tests for the target function(s).\n"
        )

        registry = SkillRegistry(skill_dir)
        skills = registry.discover()

        assert len(skills) == 1
        assert skills[0].name == "/test"
        assert skills[0].title == "Write parametrized pytest tests"

    def test_parse_skill_with_longer_description(self, tmp_path: Path) -> None:
        """Test parsing skill with multi-paragraph description."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()
        skill_file = skill_dir / "build.md"
        skill_file.write_text(
            "# /build — Implementation and coding\n\n"
            "Write the code following the plan.\n\n"
            "## What to do\n\n"
            "- Follow the architecture\n"
        )

        registry = SkillRegistry(skill_dir)
        skill = registry.get_skill("build")

        assert skill is not None
        assert skill.name == "/build"
        assert skill.title == "Implementation and coding"

    def test_parse_skill_missing_header(self, tmp_path: Path) -> None:
        """Test that file without proper header is skipped."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()
        skill_file = skill_dir / "invalid.md"
        skill_file.write_text("No header here\n")

        registry = SkillRegistry(skill_dir)
        skills = registry.discover()

        assert len(skills) == 0

    def test_parse_skill_with_unicode(self, tmp_path: Path) -> None:
        """Test parsing skill with unicode characters."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()
        skill_file = skill_dir / "spec.md"
        skill_file.write_text(
            "# /spec — Gather requirements (e.g., user stories → acceptance criteria)\n\n"
            "Identify what needs to be built.\n"
        )

        registry = SkillRegistry(skill_dir)
        skill = registry.get_skill("spec")

        assert skill is not None
        assert "→" in skill.title


class TestSkillDiscovery:
    """Tests for discovering available skills."""

    def test_discover_empty_directory(self, tmp_path: Path) -> None:
        """Test discovery in empty directory."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        registry = SkillRegistry(skill_dir)
        skills = registry.discover()

        assert len(skills) == 0

    def test_discover_multiple_skills(self, tmp_path: Path) -> None:
        """Test discovering multiple skill files."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        for name in ["spec", "plan", "build", "test"]:
            skill_file = skill_dir / f"{name}.md"
            skill_file.write_text(f"# /{name} — Test skill\n\nDescription.\n")

        registry = SkillRegistry(skill_dir)
        skills = registry.discover()

        assert len(skills) == 4
        names = [s.name for s in skills]
        assert "/build" in names
        assert "/plan" in names
        assert "/spec" in names
        assert "/test" in names

    def test_discover_skips_template(self, tmp_path: Path) -> None:
        """Test that SKILL_TEMPLATE.md is skipped."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        template = skill_dir / "SKILL_TEMPLATE.md"
        template.write_text("# SKILL_TEMPLATE — Template\n\nTemplate content.\n")

        skill_file = skill_dir / "spec.md"
        skill_file.write_text("# /spec — Test\n\nDescription.\n")

        registry = SkillRegistry(skill_dir)
        skills = registry.discover()

        assert len(skills) == 1
        assert skills[0].name == "/spec"

    def test_discover_sorted_by_name(self, tmp_path: Path) -> None:
        """Test that discovered skills are sorted by name."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        names = ["test", "spec", "ship", "plan", "build", "review"]
        for name in names:
            skill_file = skill_dir / f"{name}.md"
            skill_file.write_text(f"# /{name} — Test\n\nDescription.\n")

        registry = SkillRegistry(skill_dir)
        skills = registry.discover()

        skill_names = [s.name for s in skills]
        expected = sorted(skill_names)
        assert skill_names == expected

    def test_discover_handles_missing_directory(self, tmp_path: Path) -> None:
        """Test discovery when directory doesn't exist."""
        skill_dir = tmp_path / "nonexistent"

        registry = SkillRegistry(skill_dir)
        skills = registry.discover()

        assert len(skills) == 0


class TestSkillLookup:
    """Tests for looking up skills."""

    def test_get_skill_by_name(self, tmp_path: Path) -> None:
        """Test looking up a skill by name."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()
        skill_file = skill_dir / "spec.md"
        skill_file.write_text("# /spec — Specification\n\nGather requirements.\n")

        registry = SkillRegistry(skill_dir)
        skill = registry.get_skill("spec")

        assert skill is not None
        assert skill.name == "/spec"

    def test_get_skill_with_leading_slash(self, tmp_path: Path) -> None:
        """Test looking up a skill with leading slash."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()
        skill_file = skill_dir / "plan.md"
        skill_file.write_text("# /plan — Planning\n\nDesign architecture.\n")

        registry = SkillRegistry(skill_dir)
        skill = registry.get_skill("/plan")

        assert skill is not None
        assert skill.name == "/plan"

    def test_get_skill_not_found(self, tmp_path: Path) -> None:
        """Test looking up a skill that doesn't exist."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        registry = SkillRegistry(skill_dir)
        skill = registry.get_skill("nonexistent")

        assert skill is None

    def test_get_skill_returns_none_on_empty(self, tmp_path: Path) -> None:
        """Test get_skill returns None when no skills available."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        registry = SkillRegistry(skill_dir)
        skill = registry.get_skill("build")

        assert skill is None


class TestSkillFormatting:
    """Tests for skill list formatting."""

    def test_format_empty_list(self, tmp_path: Path) -> None:
        """Test formatting empty skill list."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        registry = SkillRegistry(skill_dir)
        output = registry.format_list()

        assert output == "No skills available."

    def test_format_multiple_skills(self, tmp_path: Path) -> None:
        """Test formatting multiple skills."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()

        for name in ["spec", "plan"]:
            skill_file = skill_dir / f"{name}.md"
            skill_file.write_text(f"# /{name} — {name.title()}\n\nDescription.\n")

        registry = SkillRegistry(skill_dir)
        output = registry.format_list()

        assert "Available skills:" in output
        assert "/plan" in output
        assert "/spec" in output

    def test_skill_repr(self, tmp_path: Path) -> None:
        """Test skill __repr__ formatting."""
        skill_dir = tmp_path / "commands"
        skill_dir.mkdir()
        skill_file = skill_dir / "build.md"
        skill_file.write_text("# /build — Implementation\n\nWrite code.\n")

        registry = SkillRegistry(skill_dir)
        skill = registry.get_skill("build")

        assert skill is not None
        output = repr(skill)
        assert "/build" in output
        assert "Write code" in output


class TestUserLocalSkills:
    """Tests for user-local skill discovery."""

    def test_discover_user_local_skills(self, tmp_path, monkeypatch) -> None:
        """Test discovering user-local skills from ~/.config/pxx/commands/."""
        # Create built-in skills dir
        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        builtin_file = builtin_dir / "spec.md"
        builtin_file.write_text("# /spec — Built-in spec\n\nBuilt-in.\n")

        # Create user-local skills dir
        user_dir = tmp_path / "user_local"
        user_dir.mkdir()
        user_file = user_dir / "myskill.md"
        user_file.write_text("# /myskill — Custom skill\n\nCustom.\n")

        # Mock Path.home() to return our temp directory
        def mock_home():
            home = tmp_path / "home"
            home.mkdir(exist_ok=True)
            config_dir = home / ".config" / "pxx" / "commands"
            config_dir.mkdir(parents=True, exist_ok=True)
            # Create the user skill in the mocked home
            user_skill = config_dir / "myskill.md"
            user_skill.write_text("# /myskill — Custom skill\n\nCustom.\n")
            return home

        monkeypatch.setattr(Path, "home", mock_home)

        # Create registry with built-in dir
        registry = SkillRegistry(builtin_dir)
        skills = registry.discover()

        # Should find both built-in and user-local skills
        skill_names = [s.name for s in skills]
        assert "/spec" in skill_names
        assert "/myskill" in skill_names

    def test_user_skills_take_precedence(self, tmp_path, monkeypatch) -> None:
        """Test that user-local skills override built-in skills."""
        # Create built-in skills dir with /spec
        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        builtin_file = builtin_dir / "spec.md"
        builtin_file.write_text("# /spec — Built-in version\n\nBuilt-in.\n")

        # Create user-local skills dir with same skill
        user_dir = tmp_path / "user_local"
        user_dir.mkdir()
        user_file = user_dir / "spec.md"
        user_file.write_text("# /spec — User version\n\nUser custom.\n")

        # Mock Path.home()
        def mock_home():
            home = tmp_path / "home"
            home.mkdir(exist_ok=True)
            config_dir = home / ".config" / "pxx" / "commands"
            config_dir.mkdir(parents=True, exist_ok=True)
            user_skill = config_dir / "spec.md"
            user_skill.write_text("# /spec — User version\n\nUser custom.\n")
            return home

        monkeypatch.setattr(Path, "home", mock_home)

        registry = SkillRegistry(builtin_dir)
        skills = registry.discover()

        # Should have exactly one /spec (the user version)
        spec_skills = [s for s in skills if s.name == "/spec"]
        assert len(spec_skills) == 1
        assert "User version" in spec_skills[0].title


class TestSkillObject:
    """Tests for Skill dataclass."""

    def test_skill_creation(self, tmp_path: Path) -> None:
        """Test creating a Skill object."""
        skill_path = tmp_path / "test.md"
        skill = Skill(
            name="/test",
            path=skill_path,
            title="Test skill",
            description="A test skill",
        )

        assert skill.name == "/test"
        assert skill.title == "Test skill"
        assert skill.description == "A test skill"

    def test_skill_repr_format(self, tmp_path: Path) -> None:
        """Test Skill repr formatting."""
        skill = Skill(
            name="/spec",
            path=tmp_path / "spec.md",
            title="Specification",
            description="Gather requirements",
        )

        output = repr(skill)
        # Should be formatted as: /spec (padded to 20 chars) — description
        assert "/spec" in output
        assert "Gather requirements" in output
