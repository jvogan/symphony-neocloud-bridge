# Self-Learning and Escalation

The bridge keeps a durable, append-only memory of provider issues so the next agent
does not re-learn the same lesson. Two behaviours make it "self-learning":

1. **Save learnings as you hit issues.** When a provider step fails or surprises you,
   record it (and its fix) immediately, instead of only fixing it in your head.
2. **Launch a research sub-agent when stuck.** When you cannot resolve a provider issue
   from existing knowledge, render a research brief and hand it to a sub-agent to search
   the provider's official docs and the recent web, then record what it finds.

Over time, scrub-clean resolved learnings are promoted into the public provider entries
(`known_patterns`), so the knowledge compounds where every future run can see it.

## Where the ledger lives

The runtime ledger is append-only JSONL at `internal/private/learnings/ledger.jsonl`
(override with `CLOUD_BRIDGE_LEARNINGS_DIR`; `record` warns if you point it at a tracked,
non-gitignored subtree). That default path is **gitignored**, so raw run context, paths, and
any pasted error text never reach the public-readiness scan. Every `record` runs a scrub
check using the **same secret detectors as the public-readiness audit** (secret assignments,
presigned URLs, connection codes, bearer/private keys) plus the internal-token list — across
each field and a joined view, so a secret split across fields is still caught. An entry that
trips any detector is flagged `scrub_warning` and is **excluded from promotion** until cleaned.
Promotion is the only path from the private ledger into public docs, so the public repo stays
clean by construction.

## The loop

1. **Search before you escalate or ask.** A provider error is usually already known.
   ```
   cloud-bridge learnings search --provider <name> --query "<symptom>"
   cloud-bridge provider-capabilities <name>      # read its known_patterns
   ```
   Also read the provider's `learnings_doc` (`docs/providers/<name>.md`).

2. **Record the issue the moment you hit it** (even before you have a fix):
   ```
   cloud-bridge learnings record --provider <name> --category <cat> --severity warn \
     --symptom "<what went wrong, one line>" --context "<where/when>"
   ```
   When you find the fix, record a resolved entry (one line each side of the `->`):
   ```
   cloud-bridge learnings record --provider <name> --category <cat> --status resolved \
     --symptom "<symptom>" --resolution "<the exact fix/command>" --evidence "<log or doc link>"
   ```
   Categories (free-form, but prefer): auth, launch, capacity, billing, egress, cleanup,
   monitoring, source-ingress, image, quota, other.

3. **When stuck after a couple of bounded attempts, launch a research sub-agent.**
   Render the brief and hand it to the agent (Claude Agent tool, or a Symphony research worker):
   ```
   cloud-bridge learnings brief --provider <name> --symptom "<symptom>" --failing-invocation "<cmd that failed>" --json
   ```
   This **auto-records an `open` learning** for the symptom (so an escalation always leaves a
   trace — pass `--no-record` to skip). The brief bundles: prior learnings for this provider
   (with their context + evidence), the failing invocation, the provider entry's `known_patterns`, the
   `learnings_doc`, doc links, suggested search queries, and a ready `agent_instruction` that
   ends with the exact `learnings record` command to log the fix.
   The sub-agent must: read the bundled knowledge first, search the provider's **official**
   docs and the recent (2025-2026) web, and return root cause + exact fix + citation +
   cost/cleanup implications. It must not run paid or mutating actions.

4. **Record the sub-agent's result** as a resolved learning (step 2) with the citation in
   `--evidence`. The escalation is only "done" once the lesson is in the ledger.

5. **Promote periodically.** At closeout, or when the ledger accrues resolved lessons:
   ```
   cloud-bridge learnings promote                 # lists scrub-clean resolved learnings + target provider entry file + bullet text
   # edit the provider entry's known_patterns (or docs/providers/<name>.md), then:
   cloud-bridge learnings promote --mark <id>
   ```
   Only `status: resolved`, scrub-clean, not-yet-promoted learnings are offered. Promotion is
   render-only (it never edits the provider entry for you) so a human/agent always reviews the wording
   before it becomes public knowledge.

## When to launch a research sub-agent

- A **novel** failure not covered by the provider entry's `known_patterns`, `learnings_doc`, or the ledger.
- A provider error that survives two bounded local attempts (e.g. a retry and one alternative).
- Any **critical** money/cleanup ambiguity ("is this resource still billing?") — escalate
  immediately rather than guess.

Do NOT launch a research agent for issues already answered by `learnings search` or the provider-entry
knowledge — apply the known fix and move on. Record a fresh learning only if the known fix
failed or the situation differs.

## Commands

| Command | Use |
| --- | --- |
| `learnings record` | Append a learning (open or resolved); auto scrub-checks |
| `learnings search` | Find prior learnings before escalating (`--query`, `--provider`, `--status`, `--tag`) |
| `learnings list` | Recent learnings (same filters, no `--query`) |
| `learnings brief` | Render the research-agent payload for a stuck issue |
| `learnings promote` | List scrub-clean resolved learnings ready for a provider entry; `--mark <id>` to flag promoted |
| `learnings stats` | Counts by provider/category/severity/status |

All `learnings` commands are local and need no provider credentials.
