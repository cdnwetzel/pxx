"""Tests for pxx.commands_index — slash-command discovery."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pxx.commands_index import (
    COMMANDS_DIR,
    NO_DESCRIPTION,
    CommandInfo,
    _extract_description,
    list_commands,
)


class TestExtractDescription:
    def test_canonical_format_em_dash(self):
        assert _extract_description("# /audit — Bug review\n") == "Bug review"

    def test_canonical_format_double_dash(self):
        assert _extract_description("# /test -- pytest tests\n") == "pytest tests"

    def test_canonical_format_single_dash(self):
        assert _extract_description("# /typecheck - mypy strict\n") == "mypy strict"

    def test_bare_slash_name_returns_placeholder(self):
        assert _extract_description("# /audit\n") == NO_DESCRIPTION

    def test_plain_heading_without_slash_returns_as_is(self):
        assert _extract_description("# Just some words\n") == "Just some words"

    def test_no_heading_returns_placeholder(self):
        assert _extract_description("Just prose, no heading.\n") == NO_DESCRIPTION

    def test_empty_text_returns_placeholder(self):
        assert _extract_description("") == NO_DESCRIPTION

    def test_heading_after_blank_lines(self):
        assert _extract_description("\n\n# /foo — bar\n") == "bar"

    def test_first_heading_wins(self):
        text = "# /first — one\n\n# /second — two\n"
        assert _extract_description(text) == "one"

    def test_h2_not_matched_as_h1(self):
        # `##` is not an H1; only the H1 below should match.
        text = "## /audit -- decoy\n\n# /audit -- real\n"
        assert _extract_description(text) == "real"

    def test_h3_not_matched_as_h1(self):
        text = "### Subheading\n\nSome prose.\n"
        assert _extract_description(text) == NO_DESCRIPTION

    def test_heading_with_trailing_whitespace(self):
        assert _extract_description("# /foo — bar baz   \n") == "bar baz"


class TestListCommands:
    def test_lists_all_existing_commands(self):
        # Smoke test against the real `pxx/commands/` directory. Derive the
        # expected set from the filesystem so this stays correct as commands
        # are added or removed (no hardcoded list to drift).
        expected = {p.stem for p in COMMANDS_DIR.glob("*.md")}
        names = {c.name for c in list_commands()}
        assert names == expected
        assert names, "pxx/commands/ should contain at least one command"

    def test_returns_sorted_by_name(self):
        commands = list_commands()
        names = [c.name for c in commands]
        assert names == sorted(names)

    def test_paths_are_absolute_and_exist(self):
        commands = list_commands()
        for c in commands:
            assert c.path.is_absolute(), f"{c.name} path not absolute"
            assert c.path.exists(), f"{c.name} path missing"
            assert c.path.suffix == ".md"

    def test_each_existing_command_has_a_description_field(self):
        # Note: descriptions for the shipped six are currently
        # NO_DESCRIPTION because the headings are bare (`# /audit` with
        # no separator). Updating the headings is a follow-up commit.
        # This test asserts the field is populated either way.
        commands = list_commands()
        for c in commands:
            assert c.description is not None
            assert isinstance(c.description, str)
            assert len(c.description) > 0

    def test_empty_directory(self, tmp_path):
        empty = tmp_path / "no-commands"
        empty.mkdir()
        assert list_commands(empty) == []

    def test_missing_directory(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        assert list_commands(missing) == []

    def test_custom_directory_picks_up_new_file(self, tmp_path):
        d = tmp_path / "cmds"
        d.mkdir()
        (d / "foo.md").write_text("# /foo — bar baz\n\nbody\n")
        result = list_commands(d)
        assert len(result) == 1
        assert result[0].name == "foo"
        assert result[0].description == "bar baz"
        assert result[0].path == (d / "foo.md").resolve()

    def test_custom_directory_with_multiple_files_sorted(self, tmp_path):
        d = tmp_path / "cmds"
        d.mkdir()
        (d / "zeta.md").write_text("# /zeta — z\n")
        (d / "alpha.md").write_text("# /alpha — a\n")
        (d / "mike.md").write_text("# /mike — m\n")
        names = [c.name for c in list_commands(d)]
        assert names == ["alpha", "mike", "zeta"]

    def test_non_md_files_ignored(self, tmp_path):
        d = tmp_path / "cmds"
        d.mkdir()
        (d / "real.md").write_text("# /real — yes\n")
        (d / "readme.txt").write_text("# /fake — no\n")
        (d / "notes.org").write_text("# /fake — no\n")
        result = list_commands(d)
        assert [c.name for c in result] == ["real"]

    def test_unreadable_file_skipped_silently(self, tmp_path):
        # A file with no read permissions should be skipped, not crash.
        d = tmp_path / "cmds"
        d.mkdir()
        ok = d / "ok.md"
        ok.write_text("# /ok — fine\n")
        locked = d / "locked.md"
        locked.write_text("# /locked — should not appear\n")
        locked.chmod(0o000)
        try:
            result = list_commands(d)
            names = [c.name for c in result]
            # ok.md must be present; locked.md may or may not be (depends
            # on whether read perms are enforced for this user).
            assert "ok" in names
        finally:
            locked.chmod(0o644)  # restore so tmp_path cleanup works


class TestCommandInfo:
    def test_is_frozen(self):
        commands = list_commands()
        assert commands, "expected at least one command in pxx/commands/"
        with pytest.raises(FrozenInstanceError):
            commands[0].name = "changed"  # type: ignore[misc]

    def test_equal_when_fields_equal(self, tmp_path):
        p = tmp_path / "x.md"
        p.write_text("ignored")
        a = CommandInfo(name="x", path=p.resolve(), description="d")
        b = CommandInfo(name="x", path=p.resolve(), description="d")
        assert a == b
