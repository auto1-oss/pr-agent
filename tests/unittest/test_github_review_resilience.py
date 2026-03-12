import re

import pytest
from github import GithubException
from requests.exceptions import RequestException

from pr_agent.algo.git_patch_processing import extract_hunk_headers
from pr_agent.algo.types import FilePatchInfo
from pr_agent.git_providers.github_provider import GithubProvider
from pr_agent.tools.pr_reviewer import PRReviewer


def test_extract_hunk_headers_defaults_missing_sizes_to_one():
    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[ ]?(.*)", "@@ -12 +34 @@ section")

    assert extract_hunk_headers(match) == ("section", 1, 1, 12, 34)


def test_extract_hunk_headers_preserves_explicit_zero_and_single_line_new_range():
    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[ ]?(.*)", "@@ -0,0 +1 @@")

    assert extract_hunk_headers(match) == ("", 0, 1, 0, 1)


def test_validate_comments_inside_hunks_handles_single_line_hunks():
    provider = GithubProvider.__new__(GithubProvider)
    provider.get_diff_files = lambda: [
        FilePatchInfo(
            base_file="old\n",
            head_file="new\n",
            patch="@@ -1 +1 @@\n-old\n+new\n",
            filename="foo.py",
        )
    ]

    suggestions = [
        {
            "body": "```suggestion\nnew\n```",
            "relevant_file": "foo.py",
            "relevant_lines_start": 1,
            "relevant_lines_end": 1,
            "original_suggestion": {"existing_code": "old", "improved_code": "new"},
        }
    ]

    validated = provider.validate_comments_inside_hunks(suggestions)

    assert validated[0]["relevant_lines_start"] == 1
    assert validated[0]["relevant_lines_end"] == 1


def test_get_languages_returns_empty_map_for_transient_github_errors():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "owner/repo"

    class FailingRepo:
        def get_languages(self):
            raise GithubException(503, {"message": "Service unavailable"}, None)

    provider._get_repo = lambda: FailingRepo()

    assert provider.get_languages() == {}


def test_get_languages_reraises_non_transient_github_errors():
    provider = GithubProvider.__new__(GithubProvider)

    class FailingRepo:
        def get_languages(self):
            raise GithubException(404, {"message": "Not found"}, None)

    provider._get_repo = lambda: FailingRepo()

    with pytest.raises(GithubException):
        provider.get_languages()


def test_get_languages_returns_empty_map_for_request_errors():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "owner/repo"

    class FailingRepo:
        def get_languages(self):
            raise RequestException("connection dropped")

    provider._get_repo = lambda: FailingRepo()

    assert provider.get_languages() == {}


def test_pr_reviewer_initial_vars_ignore_cached_related_tickets(monkeypatch):
    class FakeGitProvider:
        def __init__(self):
            self.pr = type("PR", (), {"title": "review resilience test"})()

        def get_languages(self):
            return {}

        def get_files(self):
            return []

        def get_incremental_commits(self, _incremental):
            return None

        def is_supported(self, _capability):
            return True

        def get_pr_description(self, split_changes_walkthrough=False):
            if split_changes_walkthrough:
                return "", []
            return ""

        def get_pr_branch(self):
            return "review-resilience-test"

        def get_num_of_files(self):
            return 0

        def get_commit_messages(self):
            return ""

    captured_vars = {}

    class FakeTokenHandler:
        def __init__(self, _pr, vars, _system, _user):
            captured_vars.update(vars)

    class FakeAIHandler:
        def __init__(self):
            self.main_pr_language = None

    from pr_agent.config_loader import get_settings
    import pr_agent.tools.pr_reviewer as pr_reviewer_module

    settings = get_settings()
    previous_related_tickets = settings.get("related_tickets", [])

    monkeypatch.setattr(pr_reviewer_module, "get_git_provider_with_context", lambda _pr_url: FakeGitProvider())
    monkeypatch.setattr(pr_reviewer_module, "get_main_pr_language", lambda _languages, _files: "python")
    monkeypatch.setattr(pr_reviewer_module, "TokenHandler", FakeTokenHandler)

    try:
        settings.set("related_tickets", [{"title": "stale", "labels": ["bad-shape"]}])

        reviewer = PRReviewer("https://example.com/org/repo/pull/1", ai_handler=FakeAIHandler)

        assert reviewer.vars["related_tickets"] == []
        assert captured_vars["related_tickets"] == []
    finally:
        settings.set("related_tickets", previous_related_tickets)
