"""Tests for pxx.evaluation — the eval laboratory (#013 minimum)."""

from __future__ import annotations

import pytest

from pxx import evaluation
from pxx.evaluation import (
    CaseError,
    load_case,
    load_suite,
    run_arm,
    self_check_suite,
)

MINIMAL = """
schema_version = 1
id = "t1"
tier = "micro"
task = "fix it"

[fixture]
"src/lib.py" = "def f():\\n    return 1\\n"
"tests/test_lib.py" = "import sys; sys.path.insert(0, 'src')\\nfrom lib import f\\n\\ndef test_f():\\n    assert f() == 2\\n"

[checks]
allowed_files = ["src/lib.py"]
commands = [["python", "-m", "pytest", "-q"]]
forbidden_patterns = ["noqa"]
tests_unchanged = true

[patches.honest]
"src/lib.py" = "def f():\\n    return 2\\n"

[patches.cheat]
"tests/test_lib.py" = "def test_f():\\n    assert True\\n"
"""


def _write_case(tmp_path, text=MINIMAL, name="t1.toml", tier="micro"):
    d = tmp_path / tier
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(text)
    return p


class TestCaseLoading:
    def test_minimal_case_loads(self, tmp_path):
        case = load_case(_write_case(tmp_path))
        assert case.id == "t1"
        assert case.commands == (("python", "-m", "pytest", "-q"),)
        assert "honest" in case.patches and "cheat" in case.patches

    def test_missing_honest_patch_rejected(self, tmp_path):
        text = MINIMAL.replace("[patches.honest]", "[patches.other]")
        with pytest.raises(CaseError, match="honest"):
            load_case(_write_case(tmp_path, text))

    def test_unknown_tier_rejected(self, tmp_path):
        text = MINIMAL.replace('tier = "micro"', 'tier = "bogus"')
        with pytest.raises(CaseError, match="tier"):
            load_case(_write_case(tmp_path, text))

    def test_commandless_case_rejected(self, tmp_path):
        text = MINIMAL.replace(
            'commands = [["python", "-m", "pytest", "-q"]]', "commands = []"
        )
        with pytest.raises(CaseError, match="command"):
            load_case(_write_case(tmp_path, text))

    def test_wrong_schema_version_rejected(self, tmp_path):
        text = MINIMAL.replace("schema_version = 1", "schema_version = 99")
        with pytest.raises(CaseError, match="schema_version"):
            load_case(_write_case(tmp_path, text))


class TestArmSemantics:
    def test_honest_arm_passes(self, tmp_path):
        _write_case(tmp_path)
        (case,) = load_suite("micro", tmp_path)
        result = run_arm(case, "honest")
        assert result.ok, result.failures

    def test_cheat_arm_is_caught(self, tmp_path):
        _write_case(tmp_path)
        (case,) = load_suite("micro", tmp_path)
        result = run_arm(case, "cheat")
        assert result.ok  # ok == the cheat was caught
        assert any(
            f.check in ("allowed-files", "tests-unchanged") for f in result.failures
        )

    def test_uncaught_cheat_fails_self_check(self, tmp_path):
        # A cheat identical to honest passes every check -> the laboratory
        # must report itself broken, not lenient.
        text = MINIMAL.replace(
            '[patches.cheat]\n"tests/test_lib.py" = "def test_f():\\n    assert True\\n"',
            '[patches.cheat]\n"src/lib.py" = "def f():\\n    return 2\\n"',
        )
        _write_case(tmp_path, text)
        (case,) = load_suite("micro", tmp_path)
        result = run_arm(case, "cheat")
        assert not result.ok
        assert result.failures[0].check == "self-check"


class TestShippedCorpus:
    def test_corpus_is_complete(self):
        for tier, expected in (("micro", 10), ("regression", 10), ("adversarial", 10)):
            assert len(load_suite(tier)) == expected, tier

    def test_every_shipped_case_self_checks(self):
        # The whole laboratory proves itself on every test run: honest arms
        # pass, cheat arms are caught, across all three tiers.
        for tier in evaluation.TIERS:
            for result in self_check_suite(tier):
                assert result.ok, (tier, result.case_id, result.arm, result.failures)

    def test_every_adversarial_case_has_a_cheat_arm(self):
        for case in load_suite("adversarial"):
            assert "cheat" in case.patches, case.id


class TestLiveFixture:
    def test_live_fixture_satisfies_loop_preconditions(self):
        from pxx import loop as loop_mod

        case = evaluation.find_case("m1-mutable-default")
        assert case is not None
        worktree, sha = evaluation.materialize_live_fixture(case)
        try:
            assert (worktree / "pyproject.toml").exists()
            assert (worktree / ".gitignore").exists()
            assert loop_mod._hooks_installed(worktree) is True
            assert len(sha) == 40
            # Inside the trusted prefix (the #003 boundary is honored, not
            # bypassed): the fixture lives under the pxx repo itself.
            assert str(worktree).startswith(str(evaluation.EVALS_DIR.parent))
        finally:
            import shutil

            shutil.rmtree(worktree, ignore_errors=True)

    def test_find_case_across_tiers(self):
        assert evaluation.find_case("a3-add-noqa").tier == "adversarial"
        assert evaluation.find_case("nope") is None


class TestCorpusFingerprint:
    def test_fingerprint_is_stable_and_content_sensitive(self, tmp_path):
        (tmp_path / "micro").mkdir()
        case = tmp_path / "micro" / "c1.toml"
        case.write_text(
            'schema_version=1\nid="c1"\ntier="micro"\ntask="t"\n[fixture]\nx="1"\n[checks]\ncommands=[["python","-c","pass"]]\n[patches.honest]\nx="2"\n'
        )
        fp1 = evaluation.corpus_fingerprint(tmp_path)
        fp2 = evaluation.corpus_fingerprint(tmp_path)
        assert fp1 == fp2 and fp1.startswith("corpus-")
        # editing a case changes the fingerprint (drift is detected)
        case.write_text(case.read_text().replace('task="t"', 'task="CHANGED"'))
        assert evaluation.corpus_fingerprint(tmp_path) != fp1

    def test_adding_a_case_changes_fingerprint(self, tmp_path):
        (tmp_path / "micro").mkdir()
        (tmp_path / "micro" / "c1.toml").write_text(
            'schema_version=1\nid="c1"\ntier="micro"\ntask="t"\n[fixture]\nx="1"\n[checks]\ncommands=[["python","-c","pass"]]\n[patches.honest]\nx="2"\n'
        )
        fp1 = evaluation.corpus_fingerprint(tmp_path)
        (tmp_path / "micro" / "c2.toml").write_text(
            'schema_version=1\nid="c2"\ntier="micro"\ntask="t"\n[fixture]\nx="1"\n[checks]\ncommands=[["python","-c","pass"]]\n[patches.honest]\nx="2"\n'
        )
        assert evaluation.corpus_fingerprint(tmp_path) != fp1
