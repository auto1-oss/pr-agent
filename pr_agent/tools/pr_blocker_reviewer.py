"""Blocker mode of /review: one persistent comment with the blockers and a hidden machine-readable block."""
import contextlib
import copy
import datetime
import tomllib
from functools import partial

from pr_agent.algo.ai_handlers.base_ai_handler import BaseAiHandler
from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
from pr_agent.algo.blocker_review import normalize_review, parse_hidden_block, render_comment
from pr_agent.algo.comment_identity import PRBlockerReviewIdentity, comment_matches_identity
from pr_agent.algo.pr_processing import (
    OUTPUT_BUFFER_TOKENS_HARD_THRESHOLD,
    OUTPUT_BUFFER_TOKENS_SOFT_THRESHOLD,
    FallbackEligibleError,
    get_pr_diff,
    retry_with_fallback_models,
)
from pr_agent.algo.run_details import init_run_details, record_command_failure
from pr_agent.algo.token_budget import AttemptTokenBudget
from pr_agent.algo.utils import ModelType, load_yaml
from pr_agent.config_loader import get_settings
from pr_agent.git_providers import get_git_provider_with_context
from pr_agent.git_providers.git_provider import DISCUSSION_CONTEXT_MAX_MESSAGE_CHARS
from pr_agent.log import get_logger
from pr_agent.tickets.jira_cloud import extract_jira_ticket_key
from pr_agent.tools.ticket_pr_compliance_check import extract_tickets

MAX_PEOPLE_COMMENTS_CHARS = 12000


def is_blocker_mode_enabled() -> bool:
    return str(get_settings().pr_reviewer.get("blocker_mode", False)).strip().lower() in ("1", "true", "yes", "on")


def load_blocker_prompt() -> tuple[str, str]:
    """Read the system and user templates from the TOML file the host configured."""
    path = str(get_settings().pr_reviewer.get("blocker_prompt_path", "") or "").strip()
    with open(path, "rb") as prompt_file:  # an unset path fails here, like a missing file
        prompt = tomllib.load(prompt_file).get("pr_blocker_review_prompt", {})
    if not prompt.get("system") or not prompt.get("user"):
        raise ValueError("The blocker prompt file needs [pr_blocker_review_prompt] system and user")
    return prompt["system"], prompt["user"]


def _author(comment) -> tuple[str, str]:
    user = comment.get("user") if isinstance(comment, dict) else getattr(comment, "user", None)
    get = user.get if isinstance(user, dict) else (lambda key: getattr(user, key, ""))
    return str(get("login") or ""), str(get("type") or "")


class PRBlockerReviewer:
    def __init__(self, pr_url: str, args: list = None, ai_handler: partial[BaseAiHandler,] = LiteLLMAIHandler):
        self.pr_url = pr_url
        self.git_provider = get_git_provider_with_context(pr_url)
        self.ai_handler = ai_handler()
        self.head_sha, self.previous, self.prediction, self.not_reviewed = "", {}, None, []
        self.vars = {
            "title": self.git_provider.pr.title,
            "branch": self.git_provider.get_pr_branch(),
            "description": "",
            "commit_messages_str": "",
            "people_comments": "",
            "previous_changes": [],
            "previous_blockers": [],
            "num_max_findings": get_settings().pr_reviewer.num_max_findings,
            "extra_instructions": get_settings().pr_reviewer.get("blocker_extra_instructions", ""),
            "related_tickets": [],
            "ticket_note": "",
            "diff": "",
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        }

    def _find_previous_review(self) -> dict:
        """Return the hidden block of the newest review comment this PR-Agent identity wrote."""
        if not self.git_provider.is_supported("get_issue_comments"):
            return {}
        verify = self.git_provider.supports_review_finding_state() is True
        for comment in self.git_provider.get_issue_comments_newest_first():
            body = self.git_provider._get_comment_body(comment)
            if not comment_matches_identity(body, PRBlockerReviewIdentity.PERSISTENT.value):
                continue
            if verify and not self.git_provider.is_comment_authored_by_pr_agent(comment):
                continue
            return parse_hidden_block(body)
        return {}

    def _people_comments(self) -> str:
        """Collect what people wrote on the PR, reviews first. Bots, PR-Agent and bare commands are left out."""
        if not self.git_provider.is_supported("get_issue_comments"):
            return ""
        comments, pr = [], getattr(self.git_provider, "pr", None)
        for getter in ("get_reviews", "get_review_comments"):
            if callable(getattr(pr, getter, None)):
                try:
                    comments += list(getattr(pr, getter)())
                except Exception as e:
                    get_logger().warning(f"Failed to read PR {getter}: {type(e).__name__}")
        comments += list(self.git_provider.get_issue_comments())
        lines, size = [], 0
        for comment in comments:
            login, user_type = _author(comment)
            body = (self.git_provider._get_comment_body(comment) or "").strip()
            if not body or body.startswith("/") or user_type.lower() == "bot" or login.endswith("[bot]"):
                continue
            with contextlib.suppress(Exception):  # an author that cannot be verified counts as a person
                if self.git_provider.is_comment_authored_by_pr_agent(comment) is True:
                    continue
            path = getattr(comment, "path", None) if not isinstance(comment, dict) else comment.get("path")
            where = f" on {path}:{getattr(comment, 'line', None) or ''}" if path else ""
            line = f"- {login or 'unknown'}{where}: {body[:DISCUSSION_CONTEXT_MAX_MESSAGE_CHARS]}"
            if size + len(line) <= MAX_PEOPLE_COMMENTS_CHARS:
                lines.append(line)
                size += len(line)
        return "\n".join(lines)

    async def _load_ticket(self) -> dict:
        """Read the ticket fresh on every run. The key comes from the PR title, else the branch."""
        tickets = await extract_tickets(self.git_provider) or []
        self.vars["related_tickets"] = copy.deepcopy(tickets)
        self.vars["ticket_note"] = get_settings().get("ticket_compliance_note", "") or ""
        key = extract_jira_ticket_key(self.vars["title"] or "", self.vars["branch"] or "")
        first = tickets[0] if tickets and isinstance(tickets[0], dict) else {}
        return {"key": key or str(first.get("ticket_id") or ""), "url": first.get("ticket_url", "")}

    async def _prepare_prediction(self, model: str) -> None:
        variables = copy.deepcopy(self.vars)
        output_reserve = getattr(self.ai_handler, "get_output_token_reserve", None)
        budget = AttemptTokenBudget.for_prompt_attempt(
            model, self.git_provider.pr, variables, self.system_prompt, self.user_prompt,
            ai_handler=self.ai_handler, output_token_reserve=output_reserve)
        budget.require_input_capacity(OUTPUT_BUFFER_TOKENS_SOFT_THRESHOLD, preserve_minimum=True)
        diff, self.not_reviewed = get_pr_diff(self.git_provider, budget.token_handler, model,
                                              add_line_numbers_to_hunks=True, return_remaining_files=True,
                                              output_token_reserve=output_reserve)
        if not diff:
            raise FallbackEligibleError(f"No PR diff fits the blocker review for {model}")
        fitted = budget.fit_prompt_variable(variables, "diff", diff, ai_handler=self.ai_handler,
                                            default_output_tokens=OUTPUT_BUFFER_TOKENS_HARD_THRESHOLD,
                                            preserve_minimum=True)
        if fitted.optional_text != diff:
            raise FallbackEligibleError(f"The blocker review diff does not fit the token limit for {model}")
        self.prediction, finish_reason = await self.ai_handler.chat_completion(
            model=model, temperature=get_settings().config.temperature,
            system=fitted.system_prompt, user=fitted.user_prompt)
        if finish_reason == "length":
            raise ValueError(f"The blocker review answer of {model} was cut off")

    def _pr_ref(self) -> str:
        repo = str(getattr(self.git_provider, "repo", "") or "").rstrip("/").split("/")[-1]
        number = getattr(self.git_provider, "pr_num", None) or getattr(self.git_provider.pr, "number", "")
        return f"{repo}#{number}"

    def _publish(self, body: str) -> None:
        provider = self.git_provider
        if not provider.supports_comment_editing():
            provider.publish_comment(body)
            return
        result = provider.publish_persistent_comment_full(
            body, initial_header=body.split("\n", 1)[0], update_header=False, final_update_message=True,
            identity_marker=PRBlockerReviewIdentity.PERSISTENT.value,
            require_agent_authorship=provider.supports_review_finding_state() is True, fallback_on_error=False)
        if result is None or result is False:
            raise RuntimeError("Failed to publish the blocker review comment")

    async def run(self) -> None:
        init_run_details()
        try:
            if not self.git_provider.get_files():
                get_logger().info(f"PR has no files: {self.pr_url}, skipping blocker review")
                return
            self.head_sha = self.git_provider.get_pr_head_sha() or ""
            get_logger().info(f"Blocker review of PR: {self.pr_url} at {self.head_sha[:7]}")
            self.system_prompt, self.user_prompt = load_blocker_prompt()
            self.previous = self._find_previous_review()
            self.vars.update(description=self.git_provider.get_user_description() or "",
                             commit_messages_str=self.git_provider.get_commit_messages(),
                             people_comments=self._people_comments(), previous_changes=self.previous.get("changes", []),
                             previous_blockers=self.previous.get("blockers", []))
            ticket = await self._load_ticket()
            await retry_with_fallback_models(self._prepare_prediction, model_type=ModelType.REGULAR,
                                             git_provider=self.git_provider)
            data = load_yaml((self.prediction or "").strip(), first_key="what_it_does", last_key="not_blocking",
                             keys_fix_yaml=["text:", "short:", "case:", "summary:", "risk_reason:", "undo:"])
            review = normalize_review(data, self.previous, get_settings().pr_reviewer.num_max_findings)
            if self.not_reviewed:
                # The token budget left these files out; say so instead of implying they were reviewed.
                review["not_blocking"].insert(0, f"Not reviewed, the diff did not fit: {', '.join(self.not_reviewed)}")
            body = render_comment(review, self._pr_ref(), self.head_sha, ticket)
            if get_settings().config.publish_output:
                self._publish(body)
            else:
                get_settings().data = {"artifact": body}
        except Exception as e:
            record_command_failure()
            get_logger().error(f"Failed to run the blocker review: {e}")
            if get_settings().config.publish_output:
                self.git_provider.publish_comment("Failed to run the blocker review on this PR")
            if get_settings().config.get("propagate_tool_errors", False):
                raise
