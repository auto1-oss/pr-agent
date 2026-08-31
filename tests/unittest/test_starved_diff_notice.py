"""A diff that pruning emptied must be reported on the pull request, not swallowed.

When the prompt overhead -- system, user, repo context, ticket -- fills the model budget on its
own, the pruner drops every changed file and the tool runs on an empty diff. `/review` then
published nothing at all, and `/improve` published "No code suggestions found for the PR", which
reads as a clean review of code that nothing looked at. Ten pull requests merged that way before
anyone noticed. See OPS-25871.

These tests pin the reporting, in both directions: the notice appears when the diff was starved,
and it does NOT appear when an empty result has any other cause.
"""

import pr_agent.algo.pr_processing as pr_processing
from pr_agent.algo.types import EDIT_TYPE
from pr_agent.algo.utils import STARVED_DIFF_HEADER, starved_diff_comment
from pr_agent.config_loader import get_settings


class FakeTokenHandler:
    def __init__(self, prompt_tokens=31000):
        self.prompt_tokens = prompt_tokens
        self.starved_diff = None

    def count_tokens(self, patch):
        return len(patch.split())


class FakeProvider:
    def __init__(self):
        self.published = []
        self.persistent = []

    def get_diff_files(self):
        return []

    def get_languages(self):
        return {"PHP": 100}

    def publish_comment(self, body, is_temporary=False):
        self.published.append(body)

    def publish_persistent_comment(self, body, **kwargs):
        self.persistent.append((body, kwargs))

    def edit_comment(self, comment, body):
        self.published.append(body)


FILE_DICT = {
    "src/Controller/SecurityController.php": {
        "patch": "+ one", "tokens": 10, "edit_type": EDIT_TYPE.MODIFIED,
    },
    "src/Utils/UtcTimeFormatter.php": {
        "patch": "+ two", "tokens": 10, "edit_type": EDIT_TYPE.ADDED,
    },
}


def _run_get_pr_diff(monkeypatch, files_in_patch, file_dict=None):
    """Drive get_pr_diff over the limit, with the pruner's outcome controlled by the caller."""
    file_dict = FILE_DICT if file_dict is None else file_dict
    token_handler = FakeTokenHandler()

    monkeypatch.setattr(pr_processing, "sort_files_by_main_languages", lambda languages, files: [{"files": files}])
    monkeypatch.setattr(pr_processing, "get_max_tokens", lambda model: 32000)
    # Over the soft threshold, so get_pr_diff takes the pruning branch.
    monkeypatch.setattr(
        pr_processing, "pr_generate_extended_diff",
        lambda *args, **kwargs: (["## File: 'x'"], 49265, [100]),
    )
    def fake_compressed_diff(*args, **kwargs):
        patches = [f"## File: '{name}'" for name in files_in_patch]
        return (
            [patches],              # patches_compressed_list
            [31500],                # total_tokens_list
            [[]],                   # deleted_files_list
            [list(file_dict)],      # remaining_files_list
            file_dict,              # file_dict
            [list(files_in_patch)],  # files_in_patches_list
        )

    monkeypatch.setattr(pr_processing, "pr_generate_compressed_diff", fake_compressed_diff)

    pr_processing.get_pr_diff(FakeProvider(), token_handler, "gpt-5.6-terra")
    return token_handler


def test_get_pr_diff_records_a_starved_diff_when_every_file_is_pruned(monkeypatch):
    token_handler = _run_get_pr_diff(monkeypatch, files_in_patch=[])

    assert token_handler.starved_diff is not None
    assert token_handler.starved_diff["max_tokens"] == 32000
    assert token_handler.starved_diff["prompt_tokens"] == 31000
    assert token_handler.starved_diff["skipped_files"] == sorted(FILE_DICT)


def test_get_pr_diff_records_nothing_when_a_file_survives_pruning(monkeypatch):
    token_handler = _run_get_pr_diff(
        monkeypatch, files_in_patch=["src/Utils/UtcTimeFormatter.php"]
    )

    assert token_handler.starved_diff is None


def test_get_pr_diff_records_nothing_when_the_pull_request_changed_no_files(monkeypatch):
    # An empty pull request is not a starved one, and it must not produce the notice.
    token_handler = _run_get_pr_diff(monkeypatch, files_in_patch=[], file_dict={})

    assert token_handler.starved_diff is None


def _file_patch(filename, patch):
    from pr_agent.algo.types import FilePatchInfo

    info = FilePatchInfo(
        base_file="old\n",
        head_file="new\n",
        patch=patch,
        filename=filename,
        edit_type=EDIT_TYPE.MODIFIED,
    )
    info.tokens = 400
    return info


def test_get_pr_multi_diffs_records_a_starved_diff_when_every_patch_is_skipped(monkeypatch):
    """The /improve path. It uses get_pr_multi_diffs, not get_pr_diff.

    This is the exact production path that published "No code suggestions found for the PR" for a
    pull request it had not read, so it needs its own record and its own test.
    """
    settings = get_settings()
    original = {
        "before": settings.config.patch_extra_lines_before,
        "after": settings.config.patch_extra_lines_after,
        "policy": settings.config.get("large_patch_policy", "skip"),
        "verbosity": settings.config.verbosity_level,
    }
    settings.config.patch_extra_lines_before = 0
    settings.config.patch_extra_lines_after = 0
    settings.config.large_patch_policy = "skip"
    settings.config.verbosity_level = 0

    files = [
        _file_patch("src/a.php", "@@ -1 +1 @@\n-old\n+" + ("new " * 200)),
        _file_patch("src/b.php", "@@ -1 +1 @@\n-old\n+" + ("new " * 200)),
    ]
    token_handler = FakeTokenHandler(prompt_tokens=31000)

    monkeypatch.setattr(pr_processing, "sort_files_by_main_languages", lambda languages, files_: [{"files": files}])
    monkeypatch.setattr(pr_processing, "get_max_tokens", lambda model: 32000)
    monkeypatch.setattr(
        pr_processing, "pr_generate_extended_diff",
        lambda *args, **kwargs: (["## File: 'x'"], 49265, [100]),
    )

    try:
        diffs = pr_processing.get_pr_multi_diffs(
            FakeProvider(), token_handler, "gpt-5.6-terra", max_calls=2, add_line_numbers=False
        )
    finally:
        settings.config.patch_extra_lines_before = original["before"]
        settings.config.patch_extra_lines_after = original["after"]
        settings.config.large_patch_policy = original["policy"]
        settings.config.verbosity_level = original["verbosity"]

    assert diffs == []
    assert token_handler.starved_diff is not None
    assert token_handler.starved_diff["skipped_files"] == ["src/a.php", "src/b.php"]
    assert token_handler.starved_diff["max_tokens"] == 32000


def test_get_pr_multi_diffs_records_nothing_when_the_pull_request_has_no_patches(monkeypatch):
    settings = get_settings()
    original_verbosity = settings.config.verbosity_level
    settings.config.verbosity_level = 0
    token_handler = FakeTokenHandler(prompt_tokens=31000)

    monkeypatch.setattr(pr_processing, "sort_files_by_main_languages", lambda languages, files_: [{"files": []}])
    monkeypatch.setattr(pr_processing, "get_max_tokens", lambda model: 32000)
    monkeypatch.setattr(
        pr_processing, "pr_generate_extended_diff",
        lambda *args, **kwargs: ([], 49265, []),
    )

    try:
        diffs = pr_processing.get_pr_multi_diffs(
            FakeProvider(), token_handler, "gpt-5.6-terra", max_calls=2, add_line_numbers=False
        )
    finally:
        settings.config.verbosity_level = original_verbosity

    assert diffs == []
    assert token_handler.starved_diff is None


def test_the_notice_says_no_review_happened_and_never_implies_a_clean_result():
    body = starved_diff_comment(
        {
            "max_tokens": 32000,
            "prompt_tokens": 31000,
            "skipped_files": ["a.php", "b.php"],
        },
        "/review",
    )

    assert body.startswith(STARVED_DIFF_HEADER)
    assert "did not review any code" in body
    assert "This is not a clean result." in body
    # The numbers a repository owner needs in order to act.
    assert "31000 of 32000" in body
    assert "96 %" in body
    assert "`a.php`" in body and "`b.php`" in body
    assert "repo_context_files" in body
    assert "default branch" in body
    # The phrases that would make this read as a verdict on the code.
    for forbidden in ("No major issues", "No code suggestions found", "looks good"):
        assert forbidden not in body


def test_the_notice_caps_the_file_list_and_keeps_the_exact_count():
    from pr_agent.algo.utils import STARVED_DIFF_MAX_LISTED_FILES

    names = [f"src/file_{i:03d}.php" for i in range(120)]
    body = starved_diff_comment(
        {"max_tokens": 32000, "prompt_tokens": 31000, "skipped_files": names}, "/review"
    )

    # A GitHub comment is capped at 65536 chars; the list must not be what breaks it.
    assert len(body) < 65536
    assert body.count("\n- `") == STARVED_DIFF_MAX_LISTED_FILES
    assert f"...and {120 - STARVED_DIFF_MAX_LISTED_FILES} more" in body
    # The count in the sentence stays exact even though the list is truncated.
    assert "120 changed file(s) were dropped" in body


def test_the_notice_neutralises_a_backtick_in_a_filename():
    body = starved_diff_comment(
        {"max_tokens": 32000, "prompt_tokens": 31000, "skipped_files": ["src/we`ird.php"]},
        "/review",
    )

    # The name must not be able to close its own code span and render the rest as markup.
    assert "src/we'ird.php" in body
    assert "we`ird" not in body


def test_the_notice_survives_a_report_with_no_token_numbers():
    # get_pr_diff always fills these, but the comment must not raise if a caller does not.
    body = starved_diff_comment({"skipped_files": []}, "/improve")

    assert STARVED_DIFF_HEADER in body
    assert "did not review any code" in body


def test_review_publishes_the_notice_when_the_diff_was_starved(monkeypatch):
    from pr_agent.tools.pr_reviewer import PRReviewer

    reviewer = PRReviewer.__new__(PRReviewer)
    reviewer.git_provider = FakeProvider()
    reviewer.token_handler = FakeTokenHandler()
    reviewer.token_handler.starved_diff = {
        "max_tokens": 32000, "prompt_tokens": 31000, "skipped_files": ["a.php"],
    }

    settings = get_settings()
    original = settings.config.publish_output
    settings.config.publish_output = True
    try:
        reviewer._publish_starved_diff_notice()
    finally:
        settings.config.publish_output = original

    assert len(reviewer.git_provider.persistent) == 1
    body, kwargs = reviewer.git_provider.persistent[0]
    assert STARVED_DIFF_HEADER in body
    assert kwargs["initial_header"] == STARVED_DIFF_HEADER


def test_review_publishes_nothing_when_the_prediction_is_empty_for_another_reason():
    from pr_agent.tools.pr_reviewer import PRReviewer

    reviewer = PRReviewer.__new__(PRReviewer)
    reviewer.git_provider = FakeProvider()
    reviewer.token_handler = FakeTokenHandler()  # starved_diff stays None

    settings = get_settings()
    original = settings.config.publish_output
    settings.config.publish_output = True
    try:
        reviewer._publish_starved_diff_notice()
    finally:
        settings.config.publish_output = original

    assert reviewer.git_provider.persistent == []
    assert reviewer.git_provider.published == []


def test_review_respects_publish_output_false():
    from pr_agent.tools.pr_reviewer import PRReviewer

    reviewer = PRReviewer.__new__(PRReviewer)
    reviewer.git_provider = FakeProvider()
    reviewer.token_handler = FakeTokenHandler()
    reviewer.token_handler.starved_diff = {
        "max_tokens": 32000, "prompt_tokens": 31000, "skipped_files": ["a.php"],
    }

    settings = get_settings()
    original = settings.config.publish_output
    settings.config.publish_output = False
    try:
        reviewer._publish_starved_diff_notice()
    finally:
        settings.config.publish_output = original

    assert reviewer.git_provider.persistent == []


def test_improve_replaces_the_no_suggestions_message_when_the_diff_was_starved():
    import asyncio

    from pr_agent.tools.pr_code_suggestions import PRCodeSuggestions

    tool = PRCodeSuggestions.__new__(PRCodeSuggestions)
    tool.git_provider = FakeProvider()
    tool.progress_response = None
    tool.token_handler = FakeTokenHandler()
    tool.token_handler.starved_diff = {
        "max_tokens": 32000, "prompt_tokens": 31000, "skipped_files": ["a.php"],
    }

    settings = get_settings()
    original = settings.config.publish_output
    settings.config.publish_output = True
    try:
        asyncio.run(tool.publish_no_suggestions())
    finally:
        settings.config.publish_output = original

    assert len(tool.git_provider.published) == 1
    body = tool.git_provider.published[0]
    assert STARVED_DIFF_HEADER in body
    assert "No code suggestions found for the PR." not in body


def test_improve_keeps_the_no_suggestions_message_on_a_real_empty_result():
    import asyncio

    from pr_agent.tools.pr_code_suggestions import PRCodeSuggestions

    tool = PRCodeSuggestions.__new__(PRCodeSuggestions)
    tool.git_provider = FakeProvider()
    tool.progress_response = None
    tool.token_handler = FakeTokenHandler()  # starved_diff stays None

    settings = get_settings()
    original = settings.config.publish_output
    settings.config.publish_output = True
    try:
        asyncio.run(tool.publish_no_suggestions())
    finally:
        settings.config.publish_output = original

    assert len(tool.git_provider.published) == 1
    assert "No code suggestions found for the PR." in tool.git_provider.published[0]
