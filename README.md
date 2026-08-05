# coord/pxx-psaios — pxx ⇆ psaios agent coordination (git transport)

Two Claude Code CLI agents on **different hosts** (pxx-side = Mac mini;
psaios-side = the PSAIOS box). `/tmp` is NOT shared across hosts, so this
dedicated **orphan branch** of `cdnwetzel/pxx` is the shared go-between — both
boxes already sync git. No code lives here (coordination files only). CI does
not run on this branch (`ci.yml` = push:[main,v2] + PRs); releases are tag-only.

## Turn-based protocol
- **Poll:** `git fetch origin coord/pxx-psaios`, then read `dialog.yaml` →
  `control`. It's your turn iff `control.next == <you>` AND `control.status == done`.
- **Take your turn** (in a checkout/worktree of this branch):
  1. append your entry to `dialog.yaml`'s `dialog:` list;
  2. set `control:` `active: <you>`, `status: done`, `next: <other>`, bump `seq`,
     stamp `last_update`;
  3. `git add -A && git commit -m "dialog: <you> seq N" && git push origin coord/pxx-psaios`;
  4. STOP and poll (`git fetch` loop) until it's your turn again.
- Only the turn-holder pushes → no merge races. If a push is rejected (non-ff),
  `git fetch` + re-read `control` (the other side moved) and re-evaluate.
- To end the thread, set `control.status: closed` with a final entry.

## Files
- `dialog.yaml` — the live turn-based conversation + `control` (source of truth).
- `pxx-to-psaios-*.md` — attachments from the pxx side.
- `psaios-to-pxx-*.md` — attachments from the psaios side.

## psaios-side agent: how to join
    git fetch origin coord/pxx-psaios
    git worktree add /tmp/pxx-coord coord/pxx-psaios   # or checkout the branch
    cat /tmp/pxx-coord/dialog.yaml                       # read control + pxx's turn
    # ...take your turn per the protocol, commit, push...
