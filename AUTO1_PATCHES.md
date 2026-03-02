# AUTO1 Patch Ledger

This file records all local changes carried on top of the upstream PR-Agent codebase.
Keep this list minimal to ease upstream rebases.

## Upstream baseline

- Upstream repo: qodo-ai/pr-agent
- Upstream tag: main
- Upstream commit: 1b0609a013f53694c36d457149bde70abf50c048
- Synced on: 2026-02-23

## Local patches

| Patch ID | Ticket | Type | Files | Why | Upstream status | Removal criteria |
| --- | --- | --- | --- | --- | --- | --- |
| improve-score-rubric | OPS-24090 | Enhancement | pr_agent/settings/code_suggestions/pr_code_suggestions_reflect_prompts.toml | Clarify /improve scoring so merge-blocking issues score 9-10 and map to High severity. | Not upstreamed yet. | Remove once upstream clarifies the scoring rubric for merge-blocking issues. |
| review-severity-emoji | OPS-24090 | Enhancement | pr_agent/algo/utils.py | Add a header emoji for findings severity summary in PR review output. | Not upstreamed yet. | Remove once upstream adds a default emoji for findings severity summary. |
| improve-reflect-fallback-score | OPS-24090 | Fix | pr_agent/tools/pr_code_suggestions.py | Prevent inline suggestion spam when self-reflection fails by defaulting all suggestion scores to 0. | Not upstreamed yet. | Remove once upstream handles reflect failure without inflating scores. |
| describe-mermaid-sanitize | OPS-24090 | Fix | pr_agent/tools/pr_description.py | Strip backticks from Mermaid diagrams to avoid GitHub render failures. | Not upstreamed yet. | Remove once upstream sanitizes Mermaid diagrams or fixes prompt output. |
| jira-cloud-ticket-context | OPS-24091 | Enhancement | pr_agent/algo/utils.py, pr_agent/tickets/jira_cloud.py, pr_agent/tools/ticket_pr_compliance_check.py, pr_agent/tools/pr_reviewer.py, pr_agent/tools/pr_description.py, pr_agent/tools/pr_config.py, tests/unittest/test_convert_to_markdown.py, tests/unittest/test_jira_cloud_ticket_context.py, tests/unittest/test_pr_reviewer_ticket_note.py, tests/unittest/test_ticket_pr_compliance_check.py | Fetch Jira Cloud ticket context using PR title first and branch name only as fallback, support both basic auth and scoped-token Jira Cloud API access, ignore placeholder compliance bullets, and show review notes when the Jira ticket cannot be fetched. | Not upstreamed yet. | Remove once upstream supports Jira Cloud ticket context from PR metadata with title-first precedence, scoped-token auth, placeholder-safe compliance rendering, and comment-level fetch-failure notes. |

## Rebase checklist

1) Fetch upstream and reset local baseline.
2) Verify fork still matches upstream after the sync.

## Notes

- Keep patches isolated in small, focused commits.
- If a patch is upstreamed, delete its row and drop the commit on the next rebase.
