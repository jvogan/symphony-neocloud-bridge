## Summary

-

## Validation

- [ ] `bin/runpod-bridge public-audit`
- [ ] `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- [ ] Relevant manifest, handoff, or docs checks are listed below.

## Safety

- [ ] No API keys, credentials, presigned URLs, private pod IDs, private repo names, unpublished data, or raw generated run packets are included.
- [ ] Remote RunPod mutation was not performed, or it was explicitly authorized and closeout/cleanup proof is documented.
- [ ] Examples use synthetic or public-safe data.
