# Discovery And Onboarding

This repo is discoverable in three layers.

## Normal Codex Sessions

The skill can be linked into a normal Codex skill home and this repo's local skill directory:

```text
$CODEX_HOME/skills/runpod-symphony
  -> $RUNPOD_BRIDGE_HOME/skills/runpod-symphony

$RUNPOD_BRIDGE_HOME/.codex/skills/runpod-symphony
  -> ../../skills/runpod-symphony
```

Prompts that mention RunPod, Symphony + Linear remote execution, launch manifests, artifact closeout, or pod cleanup should trigger `$runpod-symphony`.

## Symphony Workers

Symphony workers often use a separate `CODEX_HOME`:

```text
$RUNPOD_BRIDGE_SYMPHONY_HOME
```

For workers to discover the bridge automatically, the worker profile needs:

- a skill link at `codex-home-symphony/skills/runpod-symphony`
- a `[[skills.config]]` entry pointing at `skills/runpod-symphony/SKILL.md`
- a CLI wrapper such as `runpod-bridge` on `PATH`

Without those, a worker can still use the bridge only if the Linear issue or repo `AGENTS.md` names this repo path explicitly.

Check the current installation with:

```bash
runpod-bridge doctor
```

## Target Repos

Repo-local `AGENTS.md` files should not duplicate the full bridge instructions. They should say:

````markdown
For RunPod-backed remote execution, use `$runpod-symphony` and validate the workload contract with:

```bash
runpod-bridge validate-manifest path/to/launch_manifest.json
runpod-bridge validate-linear-issue path/to/linear_issue.md
runpod-bridge contract-self-check path/to/launch_manifest.json
runpod-bridge preflight path/to/launch_manifest.json
runpod-bridge egress-plan path/to/launch_manifest.json
runpod-bridge plan path/to/launch_manifest.json
runpod-bridge prepare path/to/launch_manifest.json --out-dir runpod-execution
runpod-bridge validate-handoff runpod-execution/provider_handoff.json
```
````

Keep domain science and expected artifacts in the domain repo. Keep RunPod lifecycle, monitoring, artifact hashing, cleanup, and `symphony-outcome` closeout in this bridge.
