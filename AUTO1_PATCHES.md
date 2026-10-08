# AUTO1 Patch Ledger

This file records all local changes carried on top of the upstream PR-Agent codebase.
Keep this list minimal to ease upstream rebases.

## Upstream baseline

- Upstream repo: The-PR-Agent/pr-agent
- Upstream tag: main
- Upstream commit: 0fe355ac46d3ed39cb6c35457818c4f79f053d01
- Synced on: 2026-10-08

## Local patches

| Patch ID | Ticket | Type | Files | Why | Upstream status | Removal criteria |
| --- | --- | --- | --- | --- | --- | --- |
| improve-score-rubric | OPS-24090 | Enhancement | pr_agent/settings/code_suggestions/pr_code_suggestions_reflect_prompts.toml | Clarify /improve scoring so merge-blocking issues score 9-10 and map to High severity. | Not upstreamed yet. | Remove once upstream clarifies the scoring rubric for merge-blocking issues. |
| review-severity-emoji | OPS-24090 | Enhancement | pr_agent/algo/utils.py | Add a header emoji for findings severity summary in PR review output. | Not upstreamed yet. | Remove once upstream adds a default emoji for findings severity summary. |
| jira-cloud-ticket-context | OPS-24091 | Enhancement | pr_agent/algo/utils.py, pr_agent/tickets/jira_cloud.py, pr_agent/tools/ticket_pr_compliance_check.py, pr_agent/tools/pr_reviewer.py, pr_agent/tools/pr_description.py, pr_agent/tools/pr_config.py, tests/unittest/_settings_helpers.py, tests/unittest/test_convert_to_markdown.py, tests/unittest/test_jira_cloud_ticket_context.py, tests/unittest/test_markdown_ticket_output_core.py, tests/unittest/test_pr_reviewer_ticket_note.py, tests/unittest/test_ticket_extraction_async.py, tests/unittest/test_ticket_pr_compliance_check.py | Fetch Jira Cloud ticket context using PR title first and branch name only as fallback, support both basic auth and scoped-token Jira Cloud API access, ignore placeholder compliance bullets and placeholder text like `None.`, show review notes when the Jira ticket cannot be fetched, and normalize empty discovered tickets before prompt rendering. | Not upstreamed yet. | Remove once upstream supports Jira Cloud ticket context from PR metadata with title-first precedence, scoped-token auth, placeholder-safe compliance rendering, comment-level fetch-failure notes, and prompt-safe normalization for empty ticket payloads. |
| code-suggestions-guardrails | OPS-24224 | Fix | pr_agent/algo/context_enrichment.py, pr_agent/algo/suggestion_output_filter.py, pr_agent/algo/utils.py, pr_agent/settings/code_suggestions/pr_code_suggestions_prompts.toml, pr_agent/settings/code_suggestions/pr_code_suggestions_prompts_not_decoupled.toml, pr_agent/settings/code_suggestions/pr_code_suggestions_reflect_prompts.toml, pr_agent/settings/configuration.toml, pr_agent/tools/pr_code_suggestions.py, tests/unittest/test_context_enrichment.py, tests/unittest/test_pr_code_suggestions_filtering.py, tests/unittest/test_pr_code_suggestions_guardrails.py, tests/unittest/test_pr_code_suggestions_rendering.py, tests/unittest/test_suggestion_output_filter.py | Bound inline code suggestions to high-signal findings, filter malformed suggestion output, and keep code-suggestion prompting aligned with downstream reviewer metadata settings. | Not upstreamed yet. | Remove once upstream enforces equivalent code-suggestion guardrails across prompt generation, parsed suggestion filtering, and metadata-aware rendering. |
| review-metadata-context | OPS-24224 | Enhancement | pr_agent/algo/context_enrichment.py, pr_agent/algo/review_output_filter.py, pr_agent/algo/utils.py, pr_agent/git_providers/gitlab_provider.py, pr_agent/settings/configuration.toml, pr_agent/settings/pr_reviewer_prompts.toml, pr_agent/tools/pr_code_suggestions.py, pr_agent/tools/pr_reviewer.py, tests/unittest/test_context_enrichment.py, tests/unittest/test_convert_to_markdown.py, tests/unittest/test_parse_code_suggestion.py, tests/unittest/test_review_output_filter.py | Add optional review findings metadata (`confidence`, `evidence_type`), normalize parsed findings and enforce the configured finding cap at runtime, allow bounded full-file context for small touched files to improve review precision, let metadata badges be toggled independently from metadata generation, suppress inline code-suggestion label/importance suffixes when badges are disabled, rename the downstream config keys to `findings_metadata`, `findings_metadata_badges`, and `small_file_context`. | Not upstreamed yet. | Remove once upstream supports structured review findings metadata, runtime normalization, bounded small-file context enrichment for `/review`, independent metadata-badge rendering control across review output and inline suggestions, the renamed downstream config surface. |
| github-review-resilience | OPS-24224 | Fix | pr_agent/git_providers/github_provider.py, pr_agent/tools/pr_reviewer.py, tests/unittest/test_github_review_resilience.py | Keep `/review` initialization from rendering stale cached ticket payloads and make transient GitHub language lookups non-fatal. Upstream now handles omitted hunk sizes and inline-publication diagnostics. | Not upstreamed yet. | Remove once upstream avoids prompt rendering against stale cached ticket data during reviewer initialization and degrades gracefully on transient GitHub language API failures. |
| gpt5-resolved-model-log | OPS-25323 | Enhancement | pr_agent/algo/ai_handlers/litellm_ai_handler.py, tests/unittest/test_litellm_reasoning_effort.py | Log the exact routed GPT-5/GPT-6 model alongside the existing reasoning-effort diagnostic. | Not upstreamed yet. | Remove once upstream logs the resolved GPT-5 model used for requests. |
| anthropic-api-base-routing | OPS-25323 | Fix | pr_agent/algo/ai_handlers/litellm_ai_handler.py, tests/unittest/test_litellm_provider_api_base.py | Register `ANTHROPIC.API_BASE` in upstream's request-local provider settings map; protect it from repository/comment overrides. Mixed-provider fallback keeps each gateway endpoint and key separate. | Not upstreamed yet. | Remove once upstream supports provider-specific custom API bases for mixed-provider fallback and reasoning calls. |
| repo-suggestion-apply-opt-out | OPS-25114 | Enhancement | pr_agent/git_providers/utils.py, pr_agent/settings/configuration.toml, pr_agent/tools/pr_code_suggestions.py, tests/unittest/test_apply_repo_settings_security.py, tests/unittest/test_pr_code_suggestions_rendering.py | Allow a deployment-controlled whitelist of repository settings and let repositories keep inline suggestions while disabling GitHub's apply action. | Not upstreamed yet. | Remove once upstream supports a strict repository-settings whitelist and independently configurable inline suggestion application. |
| repo-context-load-log | OPS-25565 | Enhancement | pr_agent/algo/repo_context.py, tests/unittest/test_repo_context.py | Log the successfully loaded repository context files and whether the rendered context was truncated. | Not upstreamed yet. | Remove once upstream emits equivalent repository-context loading and truncation diagnostics. |
| manual-command-title-ignore | OPS-25323 | Compatibility | pr_agent/agent/request_policy.py, tests/unittest/test_request_policy.py | Preserve explicit manual `/review`, `/improve`, and `/ask` commands on PRs whose titles match `config.ignore_pr_title`, such as `Autoscaling: ...`. Title rules still suppress automatic runs; other ignore rules continue to apply to manual commands. | Upstream applies title ignores to every command. | Remove once upstream permits manual commands to override title-based ignores while retaining automatic filtering. |

## Rebase checklist

1) Fetch upstream and merge it on a dedicated ticket branch.
2) Verify the upstream commit is an ancestor, audit the remaining fork diff, and run compatibility tests.

## October 2026 reconciliation

- Mermaid label sanitization, omitted hunk sizes, and the local-provider diff cache are now supplied by upstream; their old implementations are no longer carried.
- Reflection failure suppression now uses upstream's `pr_code_suggestions.score_on_reflection_failure = 0` in `wkda/pr-agent-settings`, instead of hard-coded scoring.
- Retain Jira's AUTO1 environment-variable authentication and title-first lookup when configured. Native upstream ticket integrations remain available; cached prompt data uses upstream's parent-before-sub-issue order and token budgets.
- Extend upstream's output models with optional `confidence` and `evidence_type` fields so AUTO1 prompting, validation, and filtering agree.
- Include findings removed by AUTO1 filtering in the persistent-state dropped-finding guard, so suppression cannot mark unfixed findings as resolved.
- Bound small-file context with upstream's per-attempt input/output token budgets, including chunked reviews.
- Preserve lazy repository-context loading and plain-text loaded-file/truncation diagnostics.
- `pr-agent-settings` must be updated together with this sync: its image installs logging into `/app/.venv` as root during the build, then restores UID/GID 10001; its ECS logging overlay includes upstream's structured-message handling, lazy settings import, and credential-process redaction.

See [AUTO1_UPSTREAM_SYNC.md](AUTO1_UPSTREAM_SYNC.md) for the impact assessment and validation limitations.

## Notes

- Keep patches isolated in small, focused commits.
- If a patch is upstreamed, delete its row and drop the commit on the next rebase.
