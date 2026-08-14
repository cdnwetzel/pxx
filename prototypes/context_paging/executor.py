"""Host executor + verifier (crash-safe).

Executes one typed action against the sandbox. A ``PATCH`` mutates source under a crash-safe
protocol: persist an **in-flight record** (idempotency key) BEFORE mutating, then apply the
patch, then bump+save the ledger, then clear the record — ordered so that any crash point is
recoverable. On startup :meth:`reconcile` inspects the record:

- **already-applied** (source == planned hash) → finish the ledger bump if needed, clear record;
- **not-applied** (source == expected hash) → discard the record (safe to retry);
- **ambiguous** (neither) → **fail closed** (something external changed the file; never guess).

So an interrupted action never double-applies or leaves source / page-index / ledger
inconsistent — and resume needs the ledger only, never a transcript.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .actions import Action, Blocked, Complete, Inspect, NeedContext, Patch, RunTest, Search
from .artifacts import ArtifactStore
from .ledger import Ledger
from .pages import PageStore, _write_durably, page_hash

# directories never worth searching (large, irrelevant, or our own state)
_SEARCH_SKIP = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pxx-paging",
    ".mypy_cache",
}

#: (argv, cwd, timeout) -> (exit_code, combined_output). Injectable so tests stay hermetic and
#: the live Neo run uses subprocess under a host timeout + resource bound.
TestRunner = Callable[[Sequence[str], Path, float], tuple[int, str]]


def subprocess_test_runner(argv: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str]:
    """Run the acceptance command under a host timeout. A runaway test is killed, not left to
    hang the loop (v1 adds cgroup/ulimit resource bounds; v0 bounds wall-clock)."""
    try:
        proc = subprocess.run(
            list(argv), cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        return 124, f"TIMEOUT after {timeout}s: {exc}"


@dataclass(frozen=True)
class ExecResult:
    """The outcome of executing one action: an observation for the next capsule, and — only for
    a terminal action — a terminal code (COMPLETED / BLOCKED:<reason>)."""

    observation: str
    terminal: str | None = None
    # side-channel provenance for the receipt (applied patch, test verdict, etc.)
    detail: dict | None = None


class Executor:
    def __init__(
        self,
        *,
        root: Path,
        state_dir: Path,
        pages: PageStore,
        ledger: Ledger,
        artifacts: ArtifactStore,
        test_runner: TestRunner = subprocess_test_runner,
        test_timeout: float = 120.0,
        search_limit: int = 20,
    ) -> None:
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.pages = pages
        self.ledger = ledger
        self.artifacts = artifacts
        self.run_test = test_runner
        self.test_timeout = test_timeout
        self.search_limit = search_limit

    # --- crash-safe in-flight record --------------------------------------------------
    @property
    def _inflight_path(self) -> Path:
        return self.state_dir / "inflight.json"

    def _write_inflight(self, record: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)  # the record must land even standalone
        tmp = self._inflight_path.with_suffix(".json.tmp")
        # fsync BEFORE the patch is applied: on power loss the record must be durable so reconcile
        # can recover, never a patched file with no record of the in-flight action.
        _write_durably(tmp, json.dumps(record, sort_keys=True).encode("utf-8"))
        tmp.replace(self._inflight_path)

    def _clear_inflight(self) -> None:
        self._inflight_path.unlink(missing_ok=True)

    def reconcile(self) -> str:
        """Recover an interrupted action at startup. Fail-closed on ambiguity.

        Returns ``"none"`` (nothing to recover), ``"applied"`` (the patch had landed; ledger
        finished + record cleared), or ``"discarded"`` (nothing landed; record cleared)."""
        if not self._inflight_path.is_file():
            return "none"
        rec = json.loads(self._inflight_path.read_text())
        current = self.pages.current_hash(rec["path"])
        if current == rec["planned_sha"]:
            # applied: the source changed, so verification is stale — invalidate it (a crash in
            # _patch's apply→invalidate→save window could otherwise leave verified=True on disk,
            # letting COMPLETE ride a test that ran on the OLD source). Finish the ledger bump if
            # the crash was between apply and save; persist both under one save.
            if self.ledger.revision == rec["pre_revision"]:
                self.ledger.verified = False
                self.ledger.bump()
                self.ledger.save(self.state_dir)
            elif self.ledger.verified:
                # bump already happened pre-crash but verification wasn't cleared — clear it now
                self.ledger.verified = False
                self.ledger.save(self.state_dir)
            self._clear_inflight()
            return "applied"
        elif current == rec["expected_sha"]:
            # not applied: nothing landed; discard so a retry is clean
            self._clear_inflight()
            return "discarded"
        else:
            raise RuntimeError(
                f"reconcile: ambiguous state for {rec['path']} "
                f"(current {current}, expected {rec['expected_sha']}, planned {rec['planned_sha']}) "
                "— refusing to guess (fail-closed)"
            )

    # --- dispatch ---------------------------------------------------------------------
    def execute(self, action: Action) -> ExecResult:
        if isinstance(action, NeedContext):
            return self._need_context(action)
        if isinstance(action, Patch):
            return self._patch(action)
        if isinstance(action, RunTest):
            return self._run_test()
        if isinstance(action, Search):
            return self._search(action)
        if isinstance(action, Inspect):
            return self._inspect(action)
        if isinstance(action, Complete):
            return self._complete()
        if isinstance(action, Blocked):
            return ExecResult(
                observation=f"BLOCKED: {action.reason}", terminal=f"BLOCKED:{action.reason}"
            )
        raise RuntimeError(f"unhandled action: {action!r}")  # pragma: no cover

    def _need_context(self, a: NeedContext) -> ExecResult:
        if not self.pages.exists(a.path):
            return ExecResult(observation=f"NEED_CONTEXT rejected: no such file {a.path!r}")
        try:
            page = self.pages.read(a.path)
        except (ValueError, OSError) as exc:  # non-UTF-8, or removed/unreadable after the exists()
            return ExecResult(observation=f"NEED_CONTEXT rejected: {exc}")
        return ExecResult(
            observation=f"# path: {page.path}\n# sha256: {page.sha}\n{page.text}",
            detail={"paged": a.path, "sha": page.sha},
        )

    def _patch(self, a: Patch) -> ExecResult:
        current = self.pages.current_hash(a.path)
        if current is None:
            return ExecResult(observation=f"PATCH rejected: no such file {a.path!r}")
        try:
            page = self.pages.read(a.path)  # read once; reused for stale-source + edit
        except (ValueError, OSError) as exc:  # non-UTF-8, or removed/unreadable after current_hash
            return ExecResult(
                observation=f"PATCH rejected: {exc}", detail={"rejected": "unreadable"}
            )
        if current != a.expected_sha:
            # stale: the file moved under the model. Never apply blind — page fresh source.
            self.ledger.record_failure(f"stale PATCH on {a.path} (expected {a.expected_sha[:12]})")
            return ExecResult(
                observation=(
                    f"PATCH REJECTED (stale expected_sha). Fresh source follows:\n"
                    f"# path: {page.path}\n# sha256: {page.sha}\n{page.text}"
                ),
                detail={"rejected": "stale_sha", "path": a.path},
            )
        text = page.text
        occurrences = text.count(a.old_string)
        if occurrences != 1:
            self.ledger.record_failure(f"PATCH old_string x{occurrences} on {a.path}")
            return ExecResult(
                observation=(
                    f"PATCH REJECTED: old_string matched {occurrences} times (need exactly 1, "
                    "no fuzz). Re-quote a unique anchor."
                ),
                detail={"rejected": "non_unique_match", "path": a.path},
            )
        new_text = text.replace(a.old_string, a.new_string, 1)
        planned_sha = page_hash(new_text.encode("utf-8"))  # the ONE hashing authority

        # crash-safe commit: record -> apply -> invalidate verification + bump+save -> clear.
        # verified MUST drop here: a passing RUN_TEST is only valid for the source it ran on, so
        # any source change invalidates it (else the model could PATCH after a green test and
        # COMPLETE on stale verification). It is persisted in the SAME ledger.save as the bump.
        pre_revision = self.ledger.revision
        self._write_inflight(
            {
                "key": self._idempotency_key(a, pre_revision),
                "path": a.path,
                "expected_sha": a.expected_sha,
                "planned_sha": planned_sha,
                "pre_revision": pre_revision,
            }
        )
        applied_sha = self.pages.write(a.path, new_text)  # atomic file replace
        self.ledger.verified = False
        self.ledger.bump()
        self.ledger.save(self.state_dir)
        self._clear_inflight()
        return ExecResult(
            observation=f"PATCH applied to {a.path}; new sha256 {applied_sha}",
            detail={
                "patched": a.path,
                "expected_sha": a.expected_sha,
                "new_sha": applied_sha,
                "applied": True,
            },
        )

    def _run_test(self) -> ExecResult:
        # host-owned: runs the LEDGER's acceptance command only. A model-selected command is
        # never honored (else the model could grade its own work).
        code, output = self.run_test(self.ledger.acceptance_cmd, self.root, self.test_timeout)
        ref = self.artifacts.put("runtest", output)
        passed = code == 0
        if passed:
            self.ledger.verified = True
        else:
            self.ledger.verified = False
            self.ledger.record_failure(f"RUN_TEST exit {code} (see {ref.ref_id})")
        self.ledger.save(
            self.state_dir
        )  # persist the verdict on BOTH branches (never in-memory only)
        verdict = "PASSED" if passed else f"FAILED (exit {code})"
        return ExecResult(
            observation=f"RUN_TEST {verdict}. artifact {ref.ref_id}:\n{ref.summary}",
            detail={"run_test": True, "passed": passed, "exit": code, "artifact": ref.ref_id},
        )

    def _complete(self) -> ExecResult:
        # COMPLETE is a request; the host records COMPLETED only after its OWN passing RUN_TEST.
        if not self.ledger.verified:
            return ExecResult(
                observation="COMPLETE rejected: no passing host RUN_TEST on record. RUN_TEST first."
            )
        return ExecResult(observation="COMPLETED (host-verified).", terminal="COMPLETED")

    def _search(self, a: Search) -> ExecResult:
        hits: list[str] = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root)
            if any(part in _SEARCH_SKIP for part in rel.parts):  # prune large/irrelevant dirs
                continue
            try:
                if a.query in p.read_text(errors="ignore"):
                    hits.append(str(rel))
            except OSError:
                continue
            if len(hits) >= self.search_limit:
                break
        listing = "\n".join(hits) if hits else "(no matches)"
        return ExecResult(
            observation=f"SEARCH {a.query!r} -> {len(hits)} file(s) (by reference):\n{listing}"
        )

    def _inspect(self, a: Inspect) -> ExecResult:
        # a ref may be an artifact id or a repo path
        art = self.artifacts.get(a.ref)
        if art is not None:
            return ExecResult(observation=f"artifact {a.ref}:\n{art[:4000]}")
        if self.pages.exists(a.ref):
            try:
                page = self.pages.read(a.ref)
            except (ValueError, OSError) as exc:
                return ExecResult(observation=f"INSPECT rejected: {exc}")
            return ExecResult(observation=f"# path: {page.path}\n# sha256: {page.sha}\n{page.text}")
        return ExecResult(observation=f"INSPECT: no artifact or file for ref {a.ref!r}")

    @staticmethod
    def _idempotency_key(a: Patch, revision: int) -> str:
        raw = f"{a.path}\0{a.expected_sha}\0{a.old_string}\0{a.new_string}\0{revision}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
