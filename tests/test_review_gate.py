"""Tests for pxx.review_gate — code review framework integration."""

from __future__ import annotations

from pathlib import Path

from pxx.review_gate import (
    Finding,
    build_healing_prompt,
    collect_active_findings,
    compute_verdict,
    framework_path,
    has_review_evidence,
    parse_findings,
    run_review_pass,
)


class TestParseFinding:
    def test_parses_finding_with_p0_severity(self):
        md = "### F-001 — critical bug in src/api.py:L42 (P0, state: open)"
        findings = parse_findings(md)
        assert len(findings) == 1
        assert findings[0].id == "F-001"
        assert findings[0].severity == "P0"
        assert findings[0].state == "open"

    def test_parses_finding_with_p1_severity(self):
        md = "### F-042 — docs-drift in src/cli.py:L30 (P1, state: proposed)"
        findings = parse_findings(md)
        assert findings[0].severity == "P1"
        assert findings[0].state == "proposed"

    def test_parses_finding_with_p2_severity(self):
        md = "### F-999 — style issue in test.py (P2, state: in-progress)"
        findings = parse_findings(md)
        assert findings[0].severity == "P2"
        assert findings[0].state == "in-progress"

    def test_ignores_resolved_findings(self):
        md = "### F-001 — old issue (P1, state: resolved)"
        findings = parse_findings(md)
        # parse_findings returns all, even resolved; collect_active_findings filters
        assert len(findings) == 1
        assert findings[0].state == "resolved"

    def test_parses_multiple_findings(self):
        md = """### F-001 — first issue (P0, state: open)
### F-002 — second issue (P1, state: proposed)
### F-003 — third issue (P2, state: open)"""
        findings = parse_findings(md)
        assert len(findings) == 3
        assert findings[0].id == "F-001"
        assert findings[2].id == "F-003"

    def test_handles_optional_state_prefix(self):
        # Regex makes "state:" optional for flexibility
        md = "### F-001 — issue (P0, open)"
        findings = parse_findings(md)
        # Should match even without "state:" prefix
        assert len(findings) == 1
        assert findings[0].state == "open"


class TestComputeVerdict:
    def test_p0_findings_returns_reject(self):
        findings = [
            Finding("F-001", "P0", "open", "file.py", "critical bug"),
        ]
        assert compute_verdict(findings) == "REJECT"

    def test_p1_findings_no_p0_returns_revise(self):
        findings = [
            Finding("F-001", "P1", "open", "file.py", "minor issue"),
        ]
        assert compute_verdict(findings) == "REVISE"

    def test_p2_findings_only_returns_approve(self):
        findings = [
            Finding("F-001", "P2", "open", "file.py", "style"),
        ]
        assert compute_verdict(findings) == "APPROVE"

    def test_p1_with_p2_returns_revise(self):
        findings = [
            Finding("F-001", "P1", "open", "file.py", "important"),
            Finding("F-002", "P2", "open", "file.py", "nice to have"),
        ]
        assert compute_verdict(findings) == "REVISE"

    def test_p0_with_p1_and_p2_returns_reject(self):
        findings = [
            Finding("F-001", "P0", "open", "file.py", "critical"),
            Finding("F-002", "P1", "open", "file.py", "important"),
            Finding("F-003", "P2", "open", "file.py", "nice"),
        ]
        assert compute_verdict(findings) == "REJECT"

    def test_empty_findings_returns_approve(self):
        assert compute_verdict([]) == "APPROVE"


class TestFailClosedVerdict:
    """The verdict must never launder a glitch into approval (9.1)."""

    def test_unknown_severity_returns_revise_not_approve(self):
        findings = [Finding("F-001", "P3", "open", "file.py", "odd label")]
        assert compute_verdict(findings) == "REVISE"

    def test_garbage_severity_returns_revise(self):
        findings = [Finding("F-001", "URGENT", "open", "file.py", "free-text sev")]
        assert compute_verdict(findings) == "REVISE"

    def test_lowercase_p0_still_rejects(self):
        findings = [Finding("F-001", "p0", "open", "file.py", "casing glitch")]
        assert compute_verdict(findings) == "REJECT"

    def test_unknown_severity_with_only_p2_still_revises(self):
        findings = [
            Finding("F-001", "P2", "open", "file.py", "style"),
            Finding("F-002", "P9", "open", "file.py", "unknown"),
        ]
        assert compute_verdict(findings) == "REVISE"

    def test_parse_keeps_unknown_severity_visible(self):
        # The old regex silently DROPPED non-P0/P1/P2 severities — the finding
        # vanished and the verdict approved on silence.
        md = "### F-001 — weird label in x.py (P3, state: open)"
        findings = parse_findings(md)
        assert len(findings) == 1
        assert findings[0].severity == "P3"

    def test_parse_normalizes_severity_case(self):
        md = "### F-001 — cased severity in x.py (p1, state: open)"
        findings = parse_findings(md)
        assert findings[0].severity == "P1"

    def test_revise_always_has_nonempty_healing_prompt(self):
        # Invariant: any findings-set that yields REVISE must yield a healing
        # prompt — otherwise the loop would spin on an empty message.
        for findings in (
            [Finding("F-001", "P1", "open", "a.py", "p1 issue")],
            [Finding("F-002", "P3", "open", "b.py", "unknown sev")],
            [
                Finding("F-003", "P2", "open", "c.py", "style"),
                Finding("F-004", "WAT", "open", "d.py", "garbage sev"),
            ],
        ):
            assert compute_verdict(findings) == "REVISE"
            assert build_healing_prompt(findings) != ""


class TestHasReviewEvidence:
    """Reviewer silence is absence of information, not approval (9.1)."""

    def test_no_review_dir_is_no_evidence(self, tmp_path):
        assert has_review_evidence(tmp_path) is False

    def test_empty_review_dir_is_no_evidence(self, tmp_path):
        (tmp_path / "review" / "claude").mkdir(parents=True)
        assert has_review_evidence(tmp_path) is False

    def test_non_matching_files_are_no_evidence(self, tmp_path):
        d = tmp_path / "review" / "claude"
        d.mkdir(parents=True)
        (d / "notes.txt").write_text("not a review artifact")
        assert has_review_evidence(tmp_path) is False

    def test_claude_md_file_is_evidence(self, tmp_path):
        d = tmp_path / "review" / "claude"
        d.mkdir(parents=True)
        (d / "claude-findings.md").write_text("clean pass, no findings")
        assert has_review_evidence(tmp_path) is True


class TestRunReviewPass:
    def test_missing_claude_binary_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pxx.review_gate._get_claude_bin", lambda: None)
        assert run_review_pass(tmp_path) == 1

    def test_successful_pass_returns_0(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pxx.review_gate._get_claude_bin", lambda: "/x/claude")

        class R:
            returncode = 0

        monkeypatch.setattr("pxx.review_gate.subprocess.run", lambda *a, **k: R())
        assert run_review_pass(tmp_path) == 0

    def test_failing_pass_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pxx.review_gate._get_claude_bin", lambda: "/x/claude")

        class R:
            returncode = 2

        monkeypatch.setattr("pxx.review_gate.subprocess.run", lambda *a, **k: R())
        assert run_review_pass(tmp_path) == 1


class TestBuildHealingPrompt:
    def test_builds_prompt_from_p1_findings(self):
        findings = [
            Finding("F-001", "P1", "open", "src/cli.py:L42", "docs-drift"),
            Finding("F-002", "P2", "open", "test.py:L10", "style"),
        ]
        prompt = build_healing_prompt(findings)
        assert "F-001" in prompt
        assert "docs-drift" in prompt
        assert "F-002" not in prompt  # P2 excluded

    def test_returns_empty_string_for_no_p1_findings(self):
        findings = [
            Finding("F-001", "P0", "open", "file.py", "critical"),
            Finding("F-002", "P2", "open", "file.py", "style"),
        ]
        prompt = build_healing_prompt(findings)
        assert prompt == ""

    def test_includes_location_when_present(self):
        findings = [
            Finding("F-001", "P1", "open", "src/cli.py:L42", "issue"),
        ]
        prompt = build_healing_prompt(findings)
        assert "Location: src/cli.py:L42" in prompt


class TestFrameworkPath:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("PXX_CODE_REVIEW_PATH", raising=False)
        path = framework_path()
        assert path.name == "code_review"
        assert path.parent.name == "ai"

    def test_respects_env_override(self, monkeypatch):
        monkeypatch.setenv("PXX_CODE_REVIEW_PATH", "/custom/path")
        path = framework_path()
        assert path == Path("/custom/path")


class TestCollectActiveFindings:
    def test_returns_empty_when_review_dir_absent(self, tmp_path):
        result = collect_active_findings(tmp_path)
        assert result == []

    def test_filters_to_active_states(self, tmp_path):
        # Create review/claude directory
        review_dir = tmp_path / "review" / "claude"
        review_dir.mkdir(parents=True)

        # Write a file with mixed states
        md_content = """### F-001 — issue (P1, state: open)
### F-002 — issue (P1, state: resolved)
### F-003 — issue (P1, state: wontfix)
### F-004 — issue (P1, state: in-progress)"""

        (review_dir / "claude-findings.md").write_text(md_content)

        findings = collect_active_findings(tmp_path)
        # Should only include open and in-progress
        assert len(findings) == 2
        active_ids = {f.id for f in findings}
        assert "F-001" in active_ids
        assert "F-004" in active_ids
        assert "F-002" not in active_ids  # resolved
        assert "F-003" not in active_ids  # wontfix

    def test_reads_multiple_markdown_files(self, tmp_path):
        review_dir = tmp_path / "review" / "claude"
        review_dir.mkdir(parents=True)

        (review_dir / "claude-findings-1.md").write_text(
            "### F-001 — issue (P1, state: open)"
        )
        (review_dir / "claude-findings-2.md").write_text(
            "### F-002 — issue (P1, state: open)"
        )

        findings = collect_active_findings(tmp_path)
        assert len(findings) == 2
        assert {f.id for f in findings} == {"F-001", "F-002"}

    def test_handles_malformed_markdown(self, tmp_path):
        review_dir = tmp_path / "review" / "claude"
        review_dir.mkdir(parents=True)

        # Valid finding
        (review_dir / "claude-valid.md").write_text(
            "### F-001 — issue (P1, state: open)"
        )
        # Malformed file that will raise an error
        (review_dir / "claude-broken.md").write_bytes(b"\x80\x81\x82")  # invalid UTF-8

        # Should not raise, just skip malformed file
        findings = collect_active_findings(tmp_path)
        assert len(findings) == 1
        assert findings[0].id == "F-001"
