## What changed

<!-- Describe the API, safety, example, or validation change. -->

## Upstream evidence

- [ ] `UPSTREAM_VERSION` checked
- [ ] Upstream docs/release notes checked for changed APIs
- [ ] `python scripts/verify_upstream.py`

## Skill quality

- [ ] `python scripts/validate_markdown.py`
- [ ] `python scripts/validate_skill.py`
- [ ] Relevant eval case added/updated
- [ ] Security boundary reviewed for URL handling, MCP exposure, secrets and untrusted web content

## Proof boundary

<!-- State browser/target-site behavior that remains PARTIAL or NOT_PROVEN. -->

## Data / authorization

- [ ] No private URLs, credentials, cookies, proxy secrets or customer data added
- [ ] Example does not encourage unauthorized access or bypass
