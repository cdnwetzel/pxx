"""Governance scanner tests: secrets, private IPs, home paths, denylist."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pxx.governance import (
    LOCKFILE_NAMES,
    SECRET_PATTERNS,
    Finding,
    load_denylist,
    scan_content,
    scan_staged,
    scan_text,
)

AWS_KEY = "AKIA" + "A" * 16
LONG_VALUE = "abcdef0123456789ABCDEF"
GH_TOKEN = "ghp_" + "a" * 36
GH_PAT = "github_pat_" + "A1_" * 12
SLACK_TOKEN = "xoxb-" + "1234567890-abcdef"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"  # pxx: allow jwt


def rules(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings}


# --- secret patterns ------------------------------------------------------------


def test_aws_access_key_id():
    findings = scan_text(f"key = {AWS_KEY}")
    assert rules(findings) == {"aws-access-key-id"}
    assert findings[0].line == 1
    assert findings[0].preview.startswith("AKIA")


def test_generic_secret_assignment_16_plus_chars():
    for line in (
        f'api_key = "{LONG_VALUE}"',
        f"token: {LONG_VALUE}",
        f"SECRET={LONG_VALUE}",
        f'password = "{LONG_VALUE}"',
    ):
        assert "secret-assignment" in rules(scan_text(line)), line


def test_short_values_not_flagged():
    assert scan_text('token = "short"') == []
    assert scan_text("api_key = abc123") == []


def test_private_key_block():
    rsa_fixture = "-----BEGIN RSA PRIVATE " + "KEY-----\nMII...\n-----END"
    findings = scan_text(rsa_fixture)
    assert "private-key" in rules(findings)
    findings = scan_text("-----BEGIN OPENSSH PRIVATE KEY-----")  # pxx: allow private-key
    assert "private-key" in rules(findings)


def test_github_tokens():
    assert "github-token" in rules(scan_text(f"token: {GH_TOKEN}"))
    assert "github-token" in rules(scan_text(f"auth {GH_PAT}"))


def test_slack_token():
    assert "slack-token" in rules(scan_text(f"bot: {SLACK_TOKEN}"))


def test_jwt():
    assert "jwt" in rules(scan_text(f"Bearer {JWT}"))


def test_all_secret_rules_have_patterns():
    names = {name for name, _ in SECRET_PATTERNS}
    assert names == {
        "aws-access-key-id",
        "secret-assignment",
        "private-key",
        "github-token",
        "slack-token",
        "jwt",
    }


# --- private IPs ------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",  # pxx: allow private-ip
        "10.255.255.254",  # pxx: allow private-ip
        "192.168.1.1",  # pxx: allow private-ip
        "172.16.0.1",  # pxx: allow private-ip
        "172.31.255.1",  # pxx: allow private-ip
        "169.254.1.1",  # pxx: allow private-ip
    ],
)
def test_private_ipv4_flagged(ip):
    assert "private-ip" in rules(scan_text(f"dial tcp {ip}:443"))


@pytest.mark.parametrize("ip", ["8.8.8.8", "172.15.0.1", "172.32.0.1", "1.1.1.1"])
def test_public_ipv4_not_flagged(ip):
    assert "private-ip" not in rules(scan_text(f"dial tcp {ip}:443"))


# --- home paths -------------------------------------------------------------------


def test_home_paths_flagged():
    assert "home-path" in rules(scan_text("cwd: /Users/alice/project"))  # pxx: allow home-path
    assert "home-path" in rules(scan_text("cwd: /home/bob/src"))  # pxx: allow home-path


def test_relative_and_non_home_paths_clean():
    assert "home-path" not in rules(scan_text("see docs/home/notes.md"))
    assert "home-path" not in rules(scan_text("path ./src/main.py"))


# --- denylist ----------------------------------------------------------------------


def test_denylist_exact_word_match():
    text = "host corp.internal\nhost notcorp.internal\nhost corp.internal.evil\n"
    findings = scan_text(text, denylist=("corp.internal",))
    deny = [f for f in findings if f.rule == "denylist-host"]
    assert [f.line for f in deny] == [1]


def test_denylist_empty_entries_ignored():
    assert scan_text("anything", denylist=("",)) == []


# --- pragma -------------------------------------------------------------------------


def test_pragma_suppresses_rule_on_that_line_only():
    text = f'api_key = "{LONG_VALUE}"  # pxx: allow secret-assignment\napi_key = "{LONG_VALUE}"\n'
    findings = scan_text(text)
    assert [f.line for f in findings] == [2]


def test_pragma_for_other_rule_does_not_suppress():
    text = f'api_key = "{LONG_VALUE}"  # pxx: allow private-ip\n'
    assert "secret-assignment" in rules(scan_text(text))


def test_pragma_unknown_rule_is_noop():
    text = f'api_key = "{LONG_VALUE}"  # pxx: allow nope\n'
    assert "secret-assignment" in rules(scan_text(text))


# --- scan_content: files, lockfiles, binary ----------------------------------------


def test_scan_content_reads_files(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text(f"key {AWS_KEY}\n", encoding="utf-8")
    findings = scan_content([f])
    assert findings[0].rule == "aws-access-key-id"
    assert findings[0].path == str(f)


def test_scan_content_skips_lockfiles(tmp_path):
    for name in LOCKFILE_NAMES:
        f = tmp_path / name
        f.write_text(f"key {AWS_KEY}\n", encoding="utf-8")
    assert scan_content(list(tmp_path.iterdir())) == []


def test_scan_content_skips_binaryish(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01" + AWS_KEY.encode() + b"\x00")
    assert scan_content([f]) == []


def test_scan_content_skips_non_utf8(tmp_path):
    f = tmp_path / "latin.txt"
    f.write_bytes(b"caf\xe9 " + AWS_KEY.encode())
    assert scan_content([f]) == []


def test_scan_content_skips_missing_file(tmp_path):
    assert scan_content([tmp_path / "nope.txt"]) == []


# --- load_denylist ------------------------------------------------------------------


def test_load_denylist_parses_and_graceful(tmp_path):
    assert load_denylist(tmp_path / "missing") == ()
    f = tmp_path / "public-denylist"
    f.write_text("# comment\ncorp.internal\n\n  vpn.example.com  \n", encoding="utf-8")
    assert load_denylist(f) == ("corp.internal", "vpn.example.com")


# --- scan_staged (git edge) ---------------------------------------------------------


def test_scan_staged_outside_repo_fails_closed(tmp_path):
    """F4: a scan that cannot run must NOT read as a clean scan."""
    from pxx.errors import PxxError

    with pytest.raises(PxxError, match="cannot scan staged files"):
        scan_staged(cwd=tmp_path)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_scan_staged_scans_staged_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    secret = tmp_path / "secret.txt"
    secret.write_text(f"key {AWS_KEY}\n", encoding="utf-8")
    unstaged = tmp_path / "unstaged.txt"
    unstaged.write_text(f"key {AWS_KEY}\n", encoding="utf-8")
    subprocess.run(["git", "add", "secret.txt"], cwd=tmp_path, check=True)
    findings = scan_staged(cwd=tmp_path)
    assert [f.path for f in findings] == [str(secret)]


def _staged_repo(tmp_path):
    import shutil as _shutil
    import subprocess as _sp

    if _shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args, **kw):
        return _sp.run(["git", *args], cwd=repo, check=True, capture_output=True, **kw)

    git("init", "-q")
    git("config", "user.name", "t")
    git("config", "user.email", "t@e.c")
    return repo, git


# Assembled so no source line matches the CI governance gate; same bytes.
_SECRET = "api_key = '" + "sk-" + "live-" + "0123456789abcdef" * 2 + "'\n"


@pytest.mark.parametrize("name", ["café.txt", 'notes"x.txt', " ", "a\nb.txt", "plain.txt"])
def test_scan_staged_reads_names_git_would_quote(tmp_path, name):
    # Grok review (2.9.1 r5, P0): names git C-quotes in plain output were
    # "unreadable" and skipped, so the secret was committed.
    from pxx.governance import scan_staged

    repo, git = _staged_repo(tmp_path)
    (repo / name).write_text(_SECRET)
    git("add", "--", name)
    findings = scan_staged(cwd=repo)
    assert findings and findings[0].rule == "secret-assignment"


def test_scan_staged_reads_the_staged_blob_not_the_worktree(tmp_path):
    # Grok review (2.9.1 r5): a clean filter put a secret into the commit that
    # the working-tree read never saw.
    from pxx.governance import scan_staged

    repo, git = _staged_repo(tmp_path)
    git("config", "filter.evil.clean", f'printf "{_SECRET.strip()}\\n"')
    (repo / ".gitattributes").write_text("*.py filter=evil\n")
    (repo / "ok.py").write_text("x = 'innocent'\n")
    git("add", ".gitattributes", "ok.py")
    assert (repo / "ok.py").read_text() == "x = 'innocent'\n"
    findings = scan_staged(cwd=repo)
    assert any(f.rule == "secret-assignment" and f.path.endswith("ok.py") for f in findings)


def test_scan_staged_fails_closed_on_an_unreadable_blob(tmp_path, monkeypatch):
    import pxx.governance as gov

    repo, git = _staged_repo(tmp_path)
    (repo / "a.txt").write_text("fine\n")
    git("add", "a.txt")
    real = gov._git_bytes

    def broken(root, *args):
        if args[:1] == ("cat-file",):
            raise gov.PxxError("governance: cannot scan staged files: boom")
        return real(root, *args)

    monkeypatch.setattr(gov, "_git_bytes", broken)
    with pytest.raises(gov.PxxError):
        gov.scan_staged(cwd=repo)
