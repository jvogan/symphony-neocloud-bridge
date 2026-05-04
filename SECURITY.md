# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes are applied to the main branch until a versioned release policy is established.

## Reporting A Vulnerability

Do not open public issues that contain credentials, private datasets, customer records, or exploit details. Report privately through the repository owner's preferred security contact.

## Secrets And Data

- Do not commit API keys, registry credentials, tokens, private keys, raw customer data, or unpublished sensitive datasets.
- Use environment variables, vault references, or runtime secret injection.
- Launch manifests should contain references to secrets, never literal secret values.
- Remote runs should write only declared artifacts and sanitized logs into `runpod-execution/`.

## Paid Resource Safety

Remote RunPod mutation is intentionally gated. A paid launch requires:

- `remote_launch_allowed: true`
- explicit `launch_authorization`
- finite budget and runtime limits
- immutable source reference
- cleanup policy
- expected artifacts and validation commands
- `--execute` plus `--yes-create-paid-runpod`

Use `--max-spend-usd` for smoke tests and demos.
