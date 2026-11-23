"""Blocker mode of /review: one persistent comment with the blockers and a hidden machine-readable block."""
import contextlib
import copy
import datetime
import itertools
import tomllib
from functools import partial

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from pr_agent.algo.ai_handlers.base_ai_handler import BaseAiHandler
from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
from pr_agent.algo.blocker_review import (
    normalize_evidence,
    normalize_review,
    parse_hidden_block,
    render_comment,
    replace_proof,
    review_from_block,
    source_key,
)
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
from pr_agent.log import get_logger
from pr_agent.tickets.jira_cloud import extract_jira_ticket_key
from pr_agent.tools.ticket_pr_compliance_check import extract_tickets

MAX_PEOPLE_COMMENTS_CHARS = 12000
MAX_COMMITS = 20
MAX_COMMIT_FILES = 30
CUT_MARKER = "\n[... cut ...]\n"
# Comments carrying this marker are merge gate reports about the PR, not proof written by a person.
MERGE_GATE_MARKER = "<!-- merge-gate"


def is_blocker_mode_enabled() -> bool:
    return str(get_settings().pr_reviewer.get("blocker_mode", False)).strip().lower() in ("1", "true", "yes", "on")


def _load_prompt(section: str) -> tuple[str, str]:
    """Read the system and user templates of one section from the TOML file the host configured."""
    path = str(get_settings().pr_reviewer.get("blocker_prompt_path", "") or "").strip()
    with open(path, "rb") as prompt_file:  # an unset path fails here, like a missing file
        prompt = tomllib.load(prompt_file).get(section, {})
    if not prompt.get("system") or not prompt.get("user"):
        raise ValueError(f"The blocker prompt file needs [{section}] system and user")
    return prompt["system"], prompt["user"]


def load_blocker_prompt() -> tuple[str, str]:
    return _load_prompt("pr_blocker_review_prompt")


def load_evidence_prompt() -> tuple[str, str]:
    return _load_prompt("pr_evidence_review_prompt")


def _field(item, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _author(comment) -> tuple[str, str]:
    user = _field(comment, "user")
    get = user.get if isinstance(user, dict) else (lambda key: getattr(user, key, ""))
    return str(get("login") or ""), str(get("type") or "")


def _utc(value) -> str:
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(datetime.timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return " ".join(str(value or "").split())


def _attribute(value) -> str:
    """A value that cannot end the attribute or the tag it is written into."""
    return " ".join(str(value or "").split()).replace('"', "'").replace("<", "").replace(">", "")


def ignored_logins() -> set[str]:
    logins = get_settings().pr_reviewer.get("evidence_ignored_logins", []) or []
    logins = [logins] if isinstance(logins, str) else logins
    return {str(login).strip().lower() for login in logins if str(login).strip()}


def is_person(login: str, user_type: str) -> bool:
    """A login of a person: not a bot and not an ignored login."""
    login = (login or "").lower()
    return (bool(login) and (user_type or "").lower() != "bot" and not login.endswith("[bot]")
            and login not in ignored_logins())


def is_people_comment(login: str, user_type: str, body: str) -> bool:
    """A comment a person wrote about the PR: not a bot, not a command, not a merge gate report."""
    body = (body or "").strip()
    return (bool(body) and not body.startswith("/") and is_person(login, user_type)
            and MERGE_GATE_MARKER not in body)


def _cited(review: dict) -> set[str]:
    return {source_key(c["evidence"]["source"]) for c in review.get("changes", [])
            if isinstance(c.get("evidence"), dict) and c["evidence"].get("source")}


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
            "description_date": "",
            "commit_messages_str": "",
            "people_comments": "",
            "commits": "",
            "previous_changes": [],
            "previous_blockers": [],
            "num_max_findings": get_settings().pr_reviewer.num_max_findings,
            "extra_instructions": get_settings().pr_reviewer.get("blocker_extra_instructions", ""),
            "related_tickets": [],
            "ticket_note": "",
            "diff": "",
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        }

    def _find_previous_comment(self) -> tuple[object, str]:
        """Return the newest review comment this PR-Agent identity wrote and its body."""
        if not self.git_provider.is_supported("get_issue_comments"):
            return None, ""
        verify = self.git_provider.supports_review_finding_state() is True
        for comment in self.git_provider.get_issue_comments_newest_first():
            body = self.git_provider._get_comment_body(comment)
            if not comment_matches_identity(body, PRBlockerReviewIdentity.PERSISTENT.value):
                continue
            if verify and not self.git_provider.is_comment_authored_by_pr_agent(comment):
                continue
            return comment, body
        return None, ""

    def _find_previous_review(self) -> dict:
        """Return the hidden block of the newest review comment this PR-Agent identity wrote."""
        return parse_hidden_block(self._find_previous_comment()[1])

    def _people_comments(self, description: str, cited: set[str] = frozenset()) -> tuple[str, dict]:
        """Render what people wrote on the PR and return the sources the model may cite as proof.

        Comments an earlier review cited come first, then the newest. Bots, PR-Agent, bare commands,
        merge gate reports and ignored logins are left out. Who wrote a source is taken from here, so
        the model cannot claim a comment was written by someone else.
        """
        provider, pr = self.git_provider, getattr(self.git_provider, "pr", None)
        pr_author = _author(pr)[0] if pr is not None else ""
        sources = {}
        if description.strip():
            sources["description"] = {"source": "description", "by": pr_author, "by_author": "yes"}
        if not provider.is_supported("get_issue_comments"):
            return "", sources
        comments = []
        for getter in ("get_reviews", "get_review_comments"):
            if callable(getattr(pr, getter, None)):
                try:
                    comments += list(getattr(pr, getter)())
                except Exception as e:
                    get_logger().warning(f"Failed to read PR {getter}: {type(e).__name__}")
        comments += list(provider.get_issue_comments())
        entries = []
        for comment in comments:
            login, user_type = _author(comment)
            body = (provider._get_comment_body(comment) or "").strip()
            if not is_people_comment(login, user_type, body):
                continue
            with contextlib.suppress(Exception):  # an author that cannot be verified counts as a person
                if provider.is_comment_authored_by_pr_agent(comment) is True:
                    continue
            url = _attribute(_field(comment, "html_url"))
            created = _utc(_field(comment, "created_at") or _field(comment, "submitted_at"))
            entries.append((source_key(url) in cited, _utc(_field(comment, "updated_at")) or created, created, url,
                            login, body, comment))
        entries.sort(key=lambda entry: entry[:2], reverse=True)
        limit = max(200, int(get_settings().pr_reviewer.get("evidence_comment_max_chars", 6000) or 6000))
        parts, size = [], 0
        for _, updated, created, url, login, body, comment in entries:
            if len(body) > limit:
                keep = (limit - len(CUT_MARKER)) // 2
                body = body[:keep] + CUT_MARKER + body[-keep:]
            path = _field(comment, "path")
            where = f' file="{_attribute(path)}:{_attribute(_field(comment, "line"))}"' if path else ""
            by_author = "yes" if login and login.lower() == pr_author.lower() else "no"
            text = (f'<comment url="{url}" by="{_attribute(login) or "unknown"}" author="{by_author}" '
                    f'created="{created}" updated="{updated}"{where}>\n'
                    f'{body.replace("</comment", "</ comment")}\n</comment>')
            if size + len(text) > MAX_PEOPLE_COMMENTS_CHARS:
                continue  # a smaller, older comment may still fit
            parts.append(text)
            size += len(text)
            if url:
                sources[source_key(url)] = {"source": url, "by": login, "by_author": by_author}
        return "\n".join(parts), sources

    def _description_date(self) -> str:
        """When a person last wrote the description, so the model can tell proof in it that is older than a change.

        Bot and PR-Agent edits do not count, so a tool that rewrites the description does not make old proof
        look new. Without a person's edit the description is as old as the PR. Empty when the provider has
        no edit history.
        """
        edits = self.git_provider.get_description_edits()
        if edits is None:
            return ""
        for edit in edits:
            login = edit.get("login") or ""
            if not is_person(login, edit.get("type") or ""):
                continue
            with contextlib.suppress(Exception):  # a login that cannot be verified counts as a person
                if self.git_provider.is_comment_authored_by_pr_agent({"user": {"login": login}}) is True:
                    continue
            return _utc(edit.get("edited_at"))
        return _utc(_field(getattr(self.git_provider, "pr", None), "created_at"))

    def _commits(self) -> str:
        """The newest commits with date and files, so the model can tell proof that is older than a change."""
        try:
            commits = list(getattr(self.git_provider, "pr_commits", None) or [])[-MAX_COMMITS:]
        except Exception as e:
            get_logger().warning(f"Failed to read the PR commits: {type(e).__name__}")
            return ""
        lines = []
        for commit in commits:
            try:
                details = getattr(commit, "commit", None)
                date = (getattr(getattr(details, "committer", None), "date", None)
                        or getattr(getattr(details, "author", None), "date", None))
                message = _attribute(str(getattr(details, "message", "") or "").split("\n", 1)[0])[:100]
                files = [str(f.filename) for f in itertools.islice(getattr(commit, "files", None) or [],
                                                                   MAX_COMMIT_FILES)]
                lines.append(f'- {str(commit.sha)[:7]} {_utc(date)} "{message}": {", ".join(files) or "no files"}')
            except Exception as e:
                get_logger().warning(f"Failed to read a PR commit: {type(e).__name__}")
        return "\n".join(lines)

    def _current_head(self) -> str:
        """Read the head again; the provider keeps the PR it loaded at the start. Empty when that fails."""
        fetch = getattr(type(self.git_provider), "_get_pr", None)
        if fetch is None:
            return self.git_provider.get_pr_head_sha() or ""
        try:
            return str(getattr(getattr(self.git_provider._get_pr(), "head", None), "sha", "") or "")
        except Exception as e:
            get_logger().warning(f"Failed to read the PR head again: {type(e).__name__}")
            return ""

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
        output = get_pr_diff(self.git_provider, budget.token_handler, model,
                             add_line_numbers_to_hunks=True, return_remaining_files=True,
                             output_token_reserve=output_reserve)
        # get_pr_diff returns only the diff string when the full diff fits, a (diff, remaining) tuple otherwise.
        diff, self.not_reviewed = output if isinstance(output, tuple) else (output, [])
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
            body, initial_header=body.split("\n", 1)[0], update_header=False,
            final_update_message=get_settings().pr_reviewer.final_update_message,
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
            description = self.git_provider.get_user_description() or ""
            people_comments, self.sources = self._people_comments(description, _cited(self.previous))
            self.vars.update(description=description,
                             description_date=self._description_date() if description.strip() else "",
                             commit_messages_str=self.git_provider.get_commit_messages(),
                             people_comments=people_comments, commits=self._commits(),
                             previous_changes=self.previous.get("changes", []),
                             previous_blockers=self.previous.get("blockers", []))
            ticket = await self._load_ticket()
            await retry_with_fallback_models(self._prepare_prediction, model_type=ModelType.REGULAR,
                                             git_provider=self.git_provider)
            data = load_yaml((self.prediction or "").strip(), first_key="what_it_does", last_key="not_blocking",
                             keys_fix_yaml=["text:", "short:", "case:", "summary:", "risk_reason:", "undo:", "source:",
                                            "note:"])
            review = normalize_review(data, self.previous, get_settings().pr_reviewer.num_max_findings, self.sources)
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

    async def _predict_evidence(self, model: str) -> None:
        self.prediction, finish_reason = await self.ai_handler.chat_completion(
            model=model, temperature=get_settings().config.temperature,
            system=self.system_prompt, user=self.user_prompt)
        if finish_reason == "length":
            raise ValueError(f"The proof check answer of {model} was cut off")

    async def run_evidence(self) -> None:
        """Proof-only pass: match what people wrote to the changes of the review of the current head.

        It runs after a person comments and rewrites only the Proof section and the hidden block. Without
        a review of the current head there is nothing to match, so it does nothing.
        """
        init_run_details()
        try:
            self.head_sha = self.git_provider.get_pr_head_sha() or ""
            comment, body = self._find_previous_comment()
            parsed = parse_hidden_block(body)
            if comment is None or not parsed or not self.head_sha or parsed["reviewed_sha"] != self.head_sha:
                get_logger().info(f"No review of the head of {self.pr_url} yet, skipping the proof check")
                return
            system, user = load_evidence_prompt()
            review = review_from_block(parsed)
            description = self.git_provider.get_user_description() or ""
            people_comments, self.sources = self._people_comments(description, _cited(review))
            variables = {
                "title": self.vars["title"], "branch": self.vars["branch"], "description": description,
                "description_date": self._description_date() if description.strip() else "",
                "people_comments": people_comments, "commits": self._commits(), "date": self.vars["date"],
                "changes": [{"id": c["id"], "text": c["text"], "evidence": c["evidence"]} for c in review["changes"]],
                "extra_instructions": self.vars["extra_instructions"],
            }
            environment = SandboxedEnvironment(undefined=StrictUndefined)
            self.system_prompt = environment.from_string(system).render(variables)
            self.user_prompt = environment.from_string(user).render(variables)
            await retry_with_fallback_models(self._predict_evidence, model_type=ModelType.REGULAR,
                                             git_provider=self.git_provider)
            data = load_yaml((self.prediction or "").strip(), first_key="evidence", keys_fix_yaml=["source:", "note:"])
            if not isinstance(data, dict) or not isinstance(data.get("evidence"), list):
                raise ValueError("The proof check output has no evidence list")
            verdicts = {str(item.get("id") or "").strip().upper(): item for item in data["evidence"]
                        if isinstance(item, dict)}
            for change in review["changes"]:
                # A change the model left out has no proof.
                change["evidence"] = normalize_evidence(verdicts.get(change["id"]), self.sources)
            new_body = replace_proof(body, review)
            if new_body is None:
                raise ValueError("The review comment has no hidden block to update")
            if new_body == body:
                get_logger().info(f"Proof of {self.pr_url} unchanged")
                return
            if not get_settings().config.publish_output:
                get_settings().data = {"artifact": new_body}
                return
            latest, latest_body = self._find_previous_comment()
            if self._current_head() != self.head_sha or latest is None or latest_body != body:
                # A push or a full review won the race; its own run decides the proof.
                get_logger().info(f"The review of {self.pr_url} changed during the proof check, not updating it")
                return
            self.git_provider.edit_comment(latest, new_body)
        except Exception as e:
            record_command_failure()
            get_logger().error(f"Failed to run the proof check: {e}")
            if get_settings().config.get("propagate_tool_errors", False):
                raise
