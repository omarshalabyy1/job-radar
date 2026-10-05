# Contributing

Contributions should make the skill more accurate against the pinned Scrapling API, safer to apply, or easier to verify.

## Rules

1. Check `UPSTREAM_VERSION` before changing an API example.
2. Prefer current upstream Scrapling documentation/release notes as the source of truth.
3. Add or update an eval for behavior changes.
4. Add/update API introspection when a symbol or signature is relied on.
5. Keep `SKILL.md` concise and move detailed material into one-level references.
6. Preserve useful superseded material in a clearly labelled historical reference rather than silently deleting it.
7. Never add real proxy credentials, cookies, bot tokens, customer URLs/data or private infrastructure.

## Validation

```bash
python -m pip install -r requirements-dev.txt
python scripts/validate_markdown.py
python scripts/validate_skill.py
python scripts/verify_upstream.py
```

## Claim discipline

Use these labels when evidence is incomplete:

- `VERIFIED` — authoritative upstream evidence for the exact scoped claim.
- `VERIFIED_IN_CI` — deterministic installed-API check passes.
- `PARTIAL` — some evidence exists, material runtime behavior is untested.
- `NOT_PROVEN` — do not state as fact for the target environment.

Do not describe a target-site/browser/anti-bot scenario as proven based only on imports or documentation.
