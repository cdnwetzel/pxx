# Reviewer Prompts for pxx v1.0.0+

Parallel-perspective code reviews from independent agents. Each agent focuses on a different dimension to inform Phase 8+ planning.

---

## Claude Code: Strategic/Architectural Review

**Focus:** Code quality, performance, technical debt, design patterns

```
You are reviewing pxx v1.0.0 — an offline-capable aider orchestrator 
with persistent observation memory, just completed through Phase 7.

Task: Produce a STRATEGIC code review examining:

1. Architecture Assessment
   - Read: pxx/cli.py, storage.py, search.py, tool_capture.py, vector_index.py
   - Evaluate: Design patterns, separation of concerns, clean abstractions

2. Technical Debt & Improvements
   - Identify: Shortcuts, deprecated patterns, maintainability issues
   - Prioritize: Which items block scaling? Which are nice-to-fix?

3. Testing & Coverage
   - Assess: Well-tested areas, gaps, risky patterns
   - Identify: Concurrency issues, edge cases, performance baselines

4. Performance Opportunities
   - Find: Hot paths, bottlenecks, scaling concerns
   - Estimate: Impact of optimizations (startup, search latency, memory)

5. Next-Gen Ideas (Phase 8+)
   - Synthesize: What features unlock new capabilities?
   - Propose: Architectural changes for sustainability
   - Prioritize: Top 3 post-1.0 improvements

Output format: Strategic findings with specific file references and impact estimates.
Focus: Code correctness, performance, and long-term maintainability.
```

---

## Gemini: Product/UX/Integration Review

**Focus:** User experience, feature completeness, ecosystem fit

```
You are reviewing pxx v1.0.0 from a PRODUCT perspective.

Context: pxx is an offline aider orchestrator with memory. It's v1.0 and 
production-ready for single-user local use. Now planning Phase 8+.

Task: Produce a PRODUCT review examining:

1. User Experience
   - Is the workflow intuitive? (3-line quick start exists, but...)
   - Discovery: Are safety features discoverable (trusted paths, ask-mode)?
   - Onboarding: What's missing for new users?
   - Pain points: What makes users frustrated?

2. Feature Completeness
   - Is observation memory compelling? Or too limited?
   - Memory interaction: Can users control/tune memory (TTL, archival)?
   - Auto-injection: Does aider use memory intelligently, or just see it?
   - Missing killers: What would make pxx essential vs. nice-to-have?

3. Integration Opportunities
   - How does pxx fit into broader workflows (IDE, CI/CD, GitHub)?
   - Natural extensions: GitHub integration? PR-aware memory? Team sync?
   - Ecosystem: Can others build on pxx (plugins, extensions)?
   - Compatibility: VS Code integration? Cursor? Copilot+?

4. Competitive Positioning
   - vs. Cursor, GitHub Copilot, other AI coding tools
   - Unique value: Offline + persistent memory (strong). Enough?
   - Sustainable business: Open-source forever? Freemium tier?

5. Vision for Phase 8+
   - What's the strategic product direction?
   - What would make pxx indispensable (not just useful)?
   - What's the multi-user story (teams of engineers)?

Output format: Strategic product insights with user research / competitive analysis.
Focus: User delight, market fit, and defensible differentiation.
```

---

## Codex: Operations/Security/Reliability Review

**Focus:** Deployment readiness, security posture, operational concerns

```
You are reviewing pxx v1.0.0 from an OPERATIONS perspective.

Context: pxx is production-ready for local single-user use. Organization 
wants to deploy at team/organization scale. What's required?

Task: Produce an OPERATIONS review examining:

1. Deployment & Scaling
   - Single machine: ready ✓. Multi-machine: issues?
   - agentmemory: unauthenticated by design. Acceptable for LAN. At scale?
   - Failure modes: If Ollama dies? If agentmemory crashes? Silent failures?
   - Data locality: Where does observation data live? On disk? In memory?

2. Observability & Monitoring
   - Can ops teams see what's happening (metrics, logs, health)?
   - pxx/agentmemory: what should we alert on?
   - Debugging: if a user says "aider behaved weird", how do we investigate?
   - Performance: how do we detect search slowdowns, memory bloat?

3. Operational Costs
   - Disk: How much space for observations? Archive retention?
   - Memory: How much RAM does agentmemory need for 100k observations?
   - CPU: Search + vector embeddings — what's the load profile?
   - Cleanup: Background thread — thread-safe? Can it block?

4. Reliability & Safety
   - Concurrent writes: SQLite under multi-session load. Safe?
   - Recovery: If memory.db corrupts, how do we recover?
   - Data durability: Can we lose observations mid-write?
   - Archive integrity: Are old archives accessible? Restorable?

5. Security Posture
   - Observation storage: plaintext? Encrypted? On disk where?
   - Credential leakage: Can users accidentally record secrets in observations?
   - Access control: Can user A see user B's observations?
   - Ollama integration: How are inference requests authenticated?
   - Supply chain: Dependencies (sentence-transformers, hnswlib) — audited?

6. Compliance & Audit
   - Observation archival: compliant with regulations (GDPR, HIPAA)?
   - Audit trail: can we prove who did what?
   - Data retention: can we delete observations on request?

Output format: Operational readiness checklist with risk assessment.
Focus: Deployment safety, reliability, and regulatory compliance.
```

---

## Copilot (Optional): Pragmatism/Adoption Review

**Focus:** Real-world usability, adoption friction, community feedback

```
You are reviewing pxx v1.0.0 from a PRAGMATISM perspective.

Context: pxx launched v1.0. It works well for the intended use case 
(local offline aider with memory). But will developers actually use it?

Task: Produce a PRAGMATISM review examining:

1. Adoption Friction
   - Install: "pip install pxx-orchestrator" works. How many steps to get memory?
   - Onboarding: Do users understand what pxx does? (vs. just aider)
   - First session: Can a user be productive without reading 6 docs files?
   - Community: Will others contribute, or is it a one-person tool?

2. Real-World Workflows
   - Single machine: observation memory works. Multi-machine?
   - Team use: How do teammates share observations?
   - Offline mode: Does "offline capability" match user expectations?
   - Fallback: If agentmemory breaks, does pxx still work? (Yes, but users know?)

3. Common Complaints (Predicted)
   - "It's just a wrapper around aider" — is that a problem?
   - "Memory doesn't help me" — is the observation quality too low?
   - "Another config file to manage" — is setup friction real?
   - "Can't use in production without a server" — blocker?

4. Monetization & Sustainability
   - Free forever: can one person maintain this long-term?
   - Commercial tier: would users pay for hosted memory? Team features?
   - Integration: should pxx focus on IDE integration (where adoption happens)?

5. Quick Wins for Adoption
   - Docs: Do they answer the obvious questions?
   - Examples: Do they inspire real usage, or are they contrived?
   - Community: How easy is it to report issues? Get help?
   - Social proof: Where will early adopters come from?

Output format: Blunt assessment of adoption likelihood with concrete friction points.
Focus: Product-market fit from the user's perspective.
```

---

## Usage

Run these reviews periodically (quarterly suggested):

```bash
# Claude Code review (strategic/architectural)
claude-code --edit <<EOF
<content of Claude Code prompt above>
EOF

# Gemini review (product/UX)
gemini --edit <<EOF
<content of Gemini prompt above>
EOF

# Codex review (operations/security)
codex --edit <<EOF
<content of Codex prompt above>
EOF

# Copilot review (pragmatism/adoption)
copilot --edit <<EOF
<content of Copilot prompt above>
EOF
```

Store results in `../review/<agent>/` following the naming convention:
- Claude: `../review/claude/claude-*.md`
- Gemini: `../review/gemini/gemini-*.md`
- Codex: `../review/codex/codex-*.md`
- Copilot: `../review/copilot/copilot-*.md`

Update `../review/inventory.md` with new review pointers.

---

## Review Cadence

| Phase | Timing | Purpose |
|---|---|---|
| v1.0.0 (current) | Now | Baseline for next phase |
| Phase 8 (Phase 2-3 months) | Post-Phase 7 docs | Inform design decisions |
| v1.1.0 (Phase 8 release) | Before release | Validate completeness |
| Quarterly | Every 3 months | Track drift, catch issues |

---

## Previous Reviews

- **v1.0.0 Claude Code Review:** `code-review-v1-0-0.md` (architectural focus)
  - Key finding: HNSW deletion + persistence, BM25 indexing, concurrency tests
  - Top 3 Phase 8 priorities identified
