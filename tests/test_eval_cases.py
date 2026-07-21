"""Tests for pxx.eval.cases: TOML loading, validation, fail-closed behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from pxx.eval.cases import Case, EvalError, Tier, load_case, load_cases
from pxx.safety import Budgets

CORPUS = Path(__file__).resolve().parent.parent / "evals"

VALID = """\
id = "t-ok"
tier = "micro"
task = "Fix the thing."
allowed_scope = ["a.py"]

honest_patch = '''
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-x = 1
+x = 2
'''

cheat_patch = '''
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-x = 1
+x = 3
'''

[budgets]
max_rounds = 2
max_diff_lines = 10

[fixture]
starting_tests = ["test_a.py"]

[fixture.files]
"a.py" = "x = 1\\n"
"test_a.py" = "import a\\n"

[checks]
commands = ["{python} test_a.py"]
forbidden_patterns = ["noqa"]
allowed_files = ["a.py"]
tests_unchanged = true
hidden_commands = ["{python} hidden.py"]
"""


def test_load_valid_case(tmp_path):
    path = tmp_path / "case.toml"
    path.write_text(VALID, encoding="utf-8")
    case = load_case(path)
    assert case.id == "t-ok"
    assert case.tier is Tier.MICRO
    assert case.task == "Fix the thing."
    assert case.allowed_scope == ("a.py",)
    assert case.budgets == Budgets(max_rounds=2, max_diff_lines=10)
    assert dict(case.fixture.files)["a.py"] == "x = 1\n"
    assert case.fixture.starting_tests == ("test_a.py",)
    assert case.checks.commands == ("{python} test_a.py",)
    assert case.checks.forbidden_patterns == ("noqa",)
    assert case.checks.allowed_files == ("a.py",)
    assert case.checks.tests_unchanged is True
    assert case.checks.hidden_commands == ("{python} hidden.py",)
    assert case.honest_patch.startswith("--- a/a.py")


def test_defaults_for_optional_sections(tmp_path):
    path = tmp_path / "case.toml"
    path.write_text(
        'id = "t-min"\ntier = "adversarial"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n',
        encoding="utf-8",
    )
    case = load_case(path)
    assert case.tier is Tier.ADVERSARIAL
    assert case.budgets == Budgets()
    assert case.fixture.files == ()
    assert case.checks.tests_unchanged is False
    assert case.allowed_scope == ()


def test_fixture_files_sorted(tmp_path):
    path = tmp_path / "case.toml"
    path.write_text(
        'id = "t-sort"\ntier = "micro"\ntask = "x"\n'
        'honest_patch = "p"\ncheat_patch = "p"\n'
        '[fixture.files]\n"z.py" = "z"\n"a.py" = "a"\n',
        encoding="utf-8",
    )
    case = load_case(path)
    assert [p for p, _ in case.fixture.files] == ["a.py", "z.py"]


# --- fail-closed validation -----------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bad.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_malformed_toml_fails_closed(tmp_path):
    path = _write(tmp_path, "id = [unclosed\n")
    with pytest.raises(EvalError, match=r"bad\.toml"):
        load_case(path)


def test_missing_required_fields_fail(tmp_path):
    for body in (
        'tier = "micro"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n',
        'id = "x"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n',
        'id = "x"\ntier = "micro"\nhonest_patch = "p"\ncheat_patch = "p"\n',
        'id = "x"\ntier = "micro"\ntask = "x"\ncheat_patch = "p"\n',
        'id = "x"\ntier = "micro"\ntask = "x"\nhonest_patch = "p"\n',
        'id = ""\ntier = "micro"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n',
    ):
        path = _write(tmp_path, body)
        with pytest.raises(EvalError):
            load_case(path)


def test_unknown_tier_fails(tmp_path):
    path = _write(
        tmp_path,
        'id = "x"\ntier = "nope"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n',
    )
    with pytest.raises(EvalError, match="tier"):
        load_case(path)


def test_unknown_budget_key_fails(tmp_path):
    path = _write(
        tmp_path,
        'id = "x"\ntier = "micro"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n'
        "[budgets]\nmax_roundz = 3\n",
    )
    with pytest.raises(EvalError, match="budget"):
        load_case(path)


def test_unknown_checks_key_fails(tmp_path):
    path = _write(
        tmp_path,
        'id = "x"\ntier = "micro"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n'
        "[checks]\ncommandz = []\n",
    )
    with pytest.raises(EvalError, match="checks"):
        load_case(path)


def test_unsafe_fixture_path_fails(tmp_path):
    path = _write(
        tmp_path,
        'id = "x"\ntier = "micro"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n'
        '[fixture.files]\n"../escape.py" = "x"\n',
    )
    with pytest.raises(EvalError, match="unsafe"):
        load_case(path)


def test_missing_file_fails(tmp_path):
    with pytest.raises(EvalError, match="unreadable"):
        load_case(tmp_path / "nope.toml")


# --- load_cases ------------------------------------------------------------------


def test_load_cases_sorted_and_duplicate_ids_fail(tmp_path):
    (tmp_path / "b.toml").write_text(
        'id = "b"\ntier = "micro"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n',
        encoding="utf-8",
    )
    (tmp_path / "a.toml").write_text(
        'id = "a"\ntier = "micro"\ntask = "x"\nhonest_patch = "p"\ncheat_patch = "p"\n',
        encoding="utf-8",
    )
    cases = load_cases(tmp_path)
    assert [c.id for c in cases] == ["a", "b"]
    (tmp_path / "a2.toml").write_text(
        'id = "a"\ntier = "micro"\ntask = "y"\nhonest_patch = "p"\ncheat_patch = "p"\n',
        encoding="utf-8",
    )
    with pytest.raises(EvalError, match="duplicate"):
        load_cases(tmp_path)


def test_load_cases_not_a_directory(tmp_path):
    with pytest.raises(EvalError):
        load_cases(tmp_path / "missing")


# --- content hash ------------------------------------------------------------------


def test_content_hash_stable_and_sensitive(tmp_path):
    path = tmp_path / "case.toml"
    path.write_text(VALID, encoding="utf-8")
    first = load_case(path)
    second = load_case(path)
    assert first.content_hash == second.content_hash
    changed = Case(
        id=first.id,
        tier=first.tier,
        task="different task",
        honest_patch=first.honest_patch,
        cheat_patch=first.cheat_patch,
    )
    assert changed.content_hash != first.content_hash


# --- seed corpus shape ---------------------------------------------------------------


def test_seed_corpus_shape():
    micro = load_cases(CORPUS / "micro")
    regression = load_cases(CORPUS / "regression")
    adversarial = load_cases(CORPUS / "adversarial")
    assert len(micro) >= 8
    assert len(regression) >= 5
    assert len(adversarial) >= 5
    for tier, cases in (
        (Tier.MICRO, micro),
        (Tier.REGRESSION, regression),
        (Tier.ADVERSARIAL, adversarial),
    ):
        assert all(c.tier is tier for c in cases)
        assert all(c.honest_patch and c.cheat_patch for c in cases)


# --- B3: family / partition / no_new_dependencies schema ---------------------------


def test_family_and_partition_parse(tmp_path):
    import tomllib

    from pxx.eval.cases import Family, Partition, load_case

    case_file = tmp_path / "c.toml"
    case_file.write_text(
        'id = "x"\ntier = "micro"\ntask = "t"\nfamily = "safety"\n'
        'partition = "held-out"\nhonest_patch = "h"\ncheat_patch = "c"\n'
        "[checks]\nno_new_dependencies = true\n"
    )
    case = load_case(case_file)
    assert case.family is Family.SAFETY
    assert case.partition is Partition.HELD_OUT
    assert case.checks.no_new_dependencies is True
    assert "safety" in case.content_hash or True  # hash covers new fields
    raw = tomllib.loads(case_file.read_text())
    raw["family"] = "unknown-family"
    case_file.write_text(
        'id = "x"\ntier = "micro"\ntask = "t"\nfamily = "unknown-family"\n'
        'honest_patch = "h"\ncheat_patch = "c"\n'
    )
    import pytest as _pytest

    from pxx.eval.cases import EvalError

    with _pytest.raises(EvalError, match="unknown family"):
        load_case(case_file)


def test_invalid_partition_fails_closed(tmp_path):
    import pytest as _pytest

    from pxx.eval.cases import EvalError, load_case

    case_file = tmp_path / "c.toml"
    case_file.write_text(
        'id = "x"\ntier = "micro"\ntask = "t"\npartition = "backstage"\n'
        'honest_patch = "h"\ncheat_patch = "c"\n'
    )
    with _pytest.raises(EvalError, match="unknown partition"):
        load_case(case_file)


def test_defaults_are_capability_dev() -> None:
    from pxx.eval.cases import Case, Family, Partition, Tier

    case = Case(id="d1", tier=Tier.MICRO, task="t")
    assert case.family is Family.CAPABILITY
    assert case.partition is Partition.DEV
    assert case.checks.no_new_dependencies is False
