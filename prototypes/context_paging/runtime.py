"""The paging loop — build capsule -> model -> one typed action -> host executes/verifies ->
update ledger -> repeat until COMPLETE / BLOCKED / budget. No transcript replay: every capsule
is built fresh from the ledger + paged sources, and a restart resumes from the ledger alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .actions import ActionError, Blocked, NeedContext, Patch, RunTest, parse_action
from .artifacts import ArtifactStore
from .capsule import CapsuleBuilder, CapsuleOverflow, Diagnostic, TokenCounter, approx_token_counter
from .executor import ExecResult, Executor, TestRunner, subprocess_test_runner
from .ledger import Ledger
from .model import ModelClient
from .pages import PageStore
from .receipt import Receipt

DEFAULT_KERNEL = (
    "You are a coding agent working one action at a time. You see a task contract, the exact "
    "target source (verbatim, authoritative), a diagnostic, and a tool list. Reply with EXACTLY "
    "ONE JSON action and nothing else. To finish you must PATCH the fix, then RUN_TEST, then "
    "COMPLETE — COMPLETE is only accepted after the host's own test passes. If you cannot "
    "proceed, reply BLOCKED with a reason. Never claim success you have not verified."
)

DEFAULT_TOOLS = (
    "Actions (one JSON object): "
    '{"type":"NEED_CONTEXT","path":"..."} | '
    '{"type":"PATCH","path":"...","expected_sha":"...","old_string":"...","new_string":"..."} | '
    '{"type":"RUN_TEST"} | {"type":"SEARCH","query":"..."} | {"type":"INSPECT","ref":"..."} | '
    '{"type":"COMPLETE"} | {"type":"BLOCKED","reason":"..."}'
)

_HISTORY_MAX = 6


@dataclass(frozen=True)
class Terminal:
    code: str  # COMPLETED | BLOCKED
    reason: str = ""

    @property
    def completed(self) -> bool:
        return self.code == "COMPLETED"


class Runtime:
    def __init__(
        self,
        *,
        root: Path,
        state_dir: Path,
        model: ModelClient,
        ledger: Ledger | None = None,
        kernel: str = DEFAULT_KERNEL,
        tools: str = DEFAULT_TOOLS,
        cap_tokens: int = 5500,
        count_tokens: TokenCounter = approx_token_counter,
        test_runner: TestRunner = subprocess_test_runner,
        test_timeout: float = 120.0,
        max_actions: int = 40,
        model_id: str = "",
        tokenizer_id: str = "",
    ) -> None:
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.pages = PageStore(root)
        self.ledger = ledger if ledger is not None else Ledger.load(self.state_dir)
        if ledger is not None and not Ledger.exists(self.state_dir):
            self.ledger.save(self.state_dir)
        self.artifacts = ArtifactStore(self.state_dir)
        self.builder = CapsuleBuilder(cap_tokens=cap_tokens, count_tokens=count_tokens)
        self.executor = Executor(
            root=root,
            state_dir=self.state_dir,
            pages=self.pages,
            ledger=self.ledger,
            artifacts=self.artifacts,
            test_runner=test_runner,
            test_timeout=test_timeout,
        )
        self.kernel = kernel
        self.tools = tools
        self.max_actions = max_actions
        self.model_id = model_id
        self.tokenizer_id = tokenizer_id

    def run(self) -> tuple[Terminal, Receipt]:
        receipt = Receipt(
            model_id=self.model_id, tokenizer_id=self.tokenizer_id, task=self.ledger.objective
        )
        nc = {
            "stale_sha_rejected": False,
            "restart_resumed_no_replay": False,
            "blocked_not_completed": False,
            "overflow_never_dropped_target": True,  # invariant; flips to a hard fail if violated
        }

        resume = self.executor.reconcile()
        if resume in ("applied", "discarded"):
            nc["restart_resumed_no_replay"] = True

        deps: dict[str, object] = {}  # path -> Page, insertion-ordered
        history: list[str] = []
        diag = Diagnostic()

        seq = 0
        terminal: Terminal | None = None
        while seq < self.max_actions:
            seq += 1
            if not self.pages.exists(self.ledger.target_path):
                terminal = Terminal("BLOCKED", f"target_missing:{self.ledger.target_path}")
                break
            try:
                target = self.pages.read(self.ledger.target_path)
            except ValueError:  # non-UTF-8 target — v0 handles text files only, fail closed
                terminal = Terminal("BLOCKED", "target_not_utf8")
                break
            except OSError:  # removed/unreadable between exists() and read() — fail closed
                terminal = Terminal("BLOCKED", "target_unreadable")
                break
            try:
                capsule = self.builder.build(
                    kernel=self.kernel,
                    contract=self._contract(),
                    target=target,
                    tools=self.tools,
                    diagnostic=diag.render(),
                    dependency_pages=list(deps.values()),
                    history=history,
                )
            except CapsuleOverflow:
                # preflight BLOCKED — the target source is never evicted/summarized to fit
                terminal = Terminal("BLOCKED", "target_source_exceeds_capsule")
                break

            if f"target:{target.path}" not in capsule.included:
                # the target source must ALWAYS be present — if it somehow isn't, fail closed
                # rather than let the model act without authoritative source.
                nc["overflow_never_dropped_target"] = False
                terminal = Terminal("BLOCKED", "target_dropped_from_capsule")
                break
            receipt.record_capsule(
                seq, capsule.input_tokens, capsule.cap, capsule.under_cap, capsule.evicted
            )

            try:
                action = parse_action(self.model.act(capsule.prompt))
            except ActionError as exc:
                receipt.record_action(seq, "INVALID", {"error": str(exc)})
                history = self._push(history, f"seq {seq}: INVALID action ({exc})")
                diag.summary = f"ill-formed action: {exc}"
                continue

            result = self.executor.execute(action)
            receipt.record_action(seq, type(action).__name__, result.detail)
            self._absorb(action, result, deps, diag)
            history = self._push(
                history, f"seq {seq}: {type(action).__name__} -> {result.observation[:160]}"
            )

            if result.detail and result.detail.get("rejected") == "stale_sha":
                nc["stale_sha_rejected"] = True

            if result.terminal is not None:
                if result.terminal == "COMPLETED":
                    terminal = Terminal("COMPLETED")
                else:  # "BLOCKED:<reason>"
                    _, _, reason = result.terminal.partition(":")
                    terminal = Terminal("BLOCKED", reason)
                break

        if terminal is None:
            terminal = Terminal("BLOCKED", "action_budget_exhausted")
        if terminal.code == "BLOCKED":
            nc["blocked_not_completed"] = True  # an honest stop was NOT recorded as COMPLETED

        receipt.terminal = terminal.code if terminal.completed else f"BLOCKED:{terminal.reason}"
        receipt.negative_controls = nc
        receipt.verification = {
            "command": " ".join(self.ledger.acceptance_cmd),
            "host_run": any(a.get("run_test") for a in receipt.actions),
            "passed": bool(self.ledger.verified),
        }
        receipt.save(self.state_dir)
        return terminal, receipt

    # --- helpers ----------------------------------------------------------------------
    def _contract(self) -> str:
        lines = [
            f"objective: {self.ledger.objective}",
            f"acceptance: {' '.join(self.ledger.acceptance_cmd)}",
            f"target file: {self.ledger.target_path}",
            f"ledger revision: {self.ledger.revision}",
            f"host-verified: {self.ledger.verified}",
        ]
        if self.ledger.invariants:
            lines.append("invariants: " + "; ".join(self.ledger.invariants))
        if self.ledger.failed_attempts:
            lines.append("recent failures: " + "; ".join(self.ledger.failed_attempts[-3:]))
        return "\n".join(lines)

    def _absorb(self, action, result: ExecResult, deps: dict, diag: Diagnostic) -> None:
        detail = result.detail or {}
        if isinstance(action, NeedContext) and "paged" in detail:
            # re-requesting a page must REFRESH its recency (move to newest) so a page the model
            # keeps using is not the first evicted under cap pressure. dict preserves insertion
            # order, so pop-then-set moves it to the end.
            deps.pop(detail["paged"], None)
            deps[detail["paged"]] = self.pages.read(detail["paged"])
        if isinstance(action, Patch) and detail.get("applied"):
            deps.pop(action.path, None)  # stale now; re-page fresh if needed
            diag.summary = ""
        if isinstance(action, RunTest):
            if detail.get("passed"):
                diag.summary = ""
            else:
                diag.summary = f"RUN_TEST failed (exit {detail.get('exit')})"
                diag.ref_id = detail.get("artifact", "")
        if isinstance(action, Blocked):
            diag.summary = f"blocked: {action.reason}"

    @staticmethod
    def _push(history: list[str], line: str) -> list[str]:
        return [*history, line][-_HISTORY_MAX:]


# convenience for callers building a run from a ledger dict
def new_task_runtime(
    *,
    root: Path,
    state_dir: Path,
    model: ModelClient,
    objective: str,
    acceptance_cmd: list[str],
    target_path: str,
    **kwargs,
) -> Runtime:
    ledger = Ledger(objective=objective, acceptance_cmd=acceptance_cmd, target_path=target_path)
    return Runtime(root=root, state_dir=state_dir, model=model, ledger=ledger, **kwargs)
