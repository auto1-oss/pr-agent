# OPS-25323 upstream sync — 2026-10-08

The fork merges `The-PR-Agent/pr-agent` main at `0fe355ac46d3ed39cb6c35457818c4f79f053d01` into `auto1-oss/pr-agent`, based on fork main `139a11a7`. This includes 798 upstream commits since `4a26c38d`, moving the package from 0.41.0 to 0.47.0 plus subsequent main-branch fixes. Both repositories use `OPS-25323-pr-agent-upstream-sync`.

## Incoming changes and AUTO1 impact

| Area | Incoming behavior | AUTO1 effect and integration |
| --- | --- | --- |
| Packaging and images | Dependencies move from requirements files to `pyproject.toml`/`uv.lock`; provider SDKs become extras. Service images use Python 3.14, `/app/.venv`, and UID/GID 10001. | The existing Jenkins `github_app` target still installs all provider extras. The companion settings image now installs `python-logstash-async` into that virtual environment during a root build step, then restores the non-root runtime identity. The two repository changes must travel together. |
| Model calls | LiteLLM 1.104.0, expanded model support, request-local provider credentials/endpoints, revised reasoning and output reserves, more selective fallback/retries. | Keep `gpt-5.6-terra` primary and `anthropic/claude-sonnet-5` reasoning/fallback. Preserve both AUTO1 gateway endpoints by adding Anthropic's API base to upstream's provider map. No live model calls were made. |
| Review output | Structured schema checks, review history and resolution handling, optional inline findings, chunked reviews, coverage/progress reporting. | Preserve the five-finding cap, confidence/evidence metadata, low-confidence inferred-finding suppression, hidden metadata badges, and database-migration guidance. Add optional metadata fields to the schema. Inline findings and large-review chunking retain their upstream disabled defaults. |
| Code suggestions | Stronger anchor/syntax checks, failed-chunk recovery, safer publication reporting, configurable reflection-failure scoring. | Keep small, high-signal suggestions and the score threshold of 7. Set `score_on_reflection_failure = 0` so unverified suggestions remain suppressed. Rename the deprecated `commitable_code_suggestions` setting to `committable_code_suggestions`. Repository apply-button opt-out still publishes a diff comment. |
| Ticket context | Native Jira/Asana/provider tickets, title issue references, repository authorization, bounded sub-issues and prompt budgets. | Keep AUTO1's scoped-token Jira Cloud authentication, PR-title precedence over branch, and visible mismatch/fetch-failure notes. When AUTO1 Jira environment variables are present, skip upstream's separate Jira lookup. Main tickets now precede child tickets when the context budget clips results. Numeric branch issue extraction remains disabled. |
| Repository context and configuration | Lazy instruction-file reads, revision-aware caches, optional sibling/per-directory context, stricter host-only settings. Namespace settings now require an explicit repository name. | Keep the three-key repository allowlist and default-branch instruction files. Protect the allowlist itself and Anthropic gateway routing from untrusted overrides. Preserve loaded-file diagnostics without eagerly fetching files beyond the line budget. AUTO1's global settings are baked into the image, so namespace lookup being opt-in does not remove them. Sibling and per-directory features stay disabled by default. |
| Small-file context | Token accounting and fallback budgets now belong to individual model attempts. | Retain bounded full-file context for touched small files, but reserve model output capacity before appending it. Apply the same rule to review chunks and suggestion chunks. |
| Logging and operations | Safe structured logging, credential-process error redaction, webhook authentication/body limits, optional OpenTelemetry and output sinks. | Update the ECS logging overlay so it preserves these logging fixes and avoids a configuration import cycle. Existing ECS field names and repository-context metadata remain available. Telemetry and new output sinks remain disabled. Terraform and deployed resources are unchanged. |
| Other providers and documentation | Extensive GitLab, Bitbucket, Gitea, Azure, local/CLI and Mosaico improvements; documentation migrates to Docusaurus. | Imported with upstream. AUTO1 continues to use its GitHub App deployment. Package documentation tests validate the new shipped help files. |

## Validation

Validation results are recorded in the ticket workspace handoff. The checks include the full upstream/fork unit suite, package distribution tests, source lint, and companion settings/runtime compatibility tests.

Docker image builds and live GitHub/Jira/model-gateway behavior require separate verification: the local Docker daemon is unavailable, and this task does not deploy or exercise production integrations. Passing local tests is not a guarantee against every production regression.

The documentation build is blocked at dependency installation: AUTO1's npm registry rejects `shell-quote@1.12.0` with HTTP 403, "version in cooldown". Built-site URL checks are consequently unverified. The generated npm lockfile is included in the pre-commit large-file exemption alongside `uv.lock`.

## Local patch reconciliation

Upstream now supplies Mermaid label sanitization, omitted hunk-size parsing, and the local diff cache. Reflection-failure suppression uses configuration instead of a fork-only implementation. Remaining customizations are tracked in [AUTO1_PATCHES.md](AUTO1_PATCHES.md).
