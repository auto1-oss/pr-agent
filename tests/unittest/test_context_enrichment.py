from pr_agent.algo.context_enrichment import (
    append_small_file_context_to_diff,
    append_small_file_context_to_diffs,
    extract_files_in_diff,
    render_full_file_context,
)
from pr_agent.algo.types import EDIT_TYPE, FilePatchInfo


class MockTokenHandler:
    def count_tokens(self, text: str) -> int:
        return len(text)


class TestContextEnrichment:
    def test_append_small_file_context_to_diff_adds_only_small_included_files(self):
        diff_text = "## File: 'src/small.py'\n\n@@ ... @@\n__new hunk__\n1 +print('hi')\n"
        diff_files = [
            FilePatchInfo(
                base_file="",
                head_file="print('hi')\nprint('bye')\n",
                patch="@@ -0,0 +1,2 @@\n+print('hi')\n+print('bye')\n",
                filename="src/small.py",
                edit_type=EDIT_TYPE.ADDED,
            ),
            FilePatchInfo(
                base_file="",
                head_file="\n".join([f"line {i}" for i in range(1, 201)]),
                patch="@@ -0,0 +1,200 @@\n+...",
                filename="src/large.py",
                edit_type=EDIT_TYPE.ADDED,
            ),
        ]

        enriched_diff = append_small_file_context_to_diff(
            diff_text,
            diff_files,
            MockTokenHandler(),
            max_lines=10,
            max_tokens=500,
        )

        assert "Additional context for small touched files" in enriched_diff
        assert "## Full file context: 'src/small.py'" in enriched_diff
        assert "1 print('hi')" in enriched_diff
        assert "src/large.py" not in enriched_diff

    def test_append_small_file_context_to_diff_respects_token_budget(self):
        diff_text = "## File: 'src/one.py'\n\n@@ ... @@\n__new hunk__\n1 +print('hi')\n"
        diff_files = [
            FilePatchInfo(
                base_file="",
                head_file="print('hi')\nprint('bye')\n",
                patch="@@ -0,0 +1,2 @@\n+print('hi')\n+print('bye')\n",
                filename="src/one.py",
                edit_type=EDIT_TYPE.ADDED,
            ),
        ]

        enriched_diff = append_small_file_context_to_diff(
            diff_text,
            diff_files,
            MockTokenHandler(),
            max_lines=10,
            max_tokens=10,
        )

        assert enriched_diff == diff_text

    def test_append_small_file_context_to_diff_skips_deleted_or_missing_head_files(self):
        diff_text = (
            "## File: 'src/deleted.py'\n\n@@ ... @@\n__new hunk__\n"
            "\n## File: 'src/missing.py'\n\n@@ ... @@\n__new hunk__\n1 +print('hi')\n"
        )
        diff_files = [
            FilePatchInfo(
                base_file="print('bye')\n",
                head_file="",
                patch="@@ -1 +0,0 @@\n-print('bye')\n",
                filename="src/deleted.py",
                edit_type=EDIT_TYPE.DELETED,
            ),
            FilePatchInfo(
                base_file="",
                head_file=None,
                patch="@@ -0,0 +1 @@\n+print('hi')\n",
                filename="src/missing.py",
                edit_type=EDIT_TYPE.ADDED,
            ),
        ]

        enriched_diff = append_small_file_context_to_diff(
            diff_text,
            diff_files,
            MockTokenHandler(),
            max_lines=10,
            max_tokens=500,
        )

        assert enriched_diff == diff_text

    def test_append_small_file_context_to_diff_prefers_smaller_files_when_budget_is_limited(self):
        diff_text = (
            "## File: 'src/medium.py'\n\n@@ ... @@\n__new hunk__\n1 +medium\n"
            "\n## File: 'src/small_a.py'\n\n@@ ... @@\n__new hunk__\n1 +small_a\n"
            "\n## File: 'src/small_b.py'\n\n@@ ... @@\n__new hunk__\n1 +small_b\n"
        )
        medium_file = FilePatchInfo(
            base_file="",
            head_file="\n".join(["medium"] * 3),
            patch="@@ -0,0 +1,3 @@\n+medium\n+medium\n+medium\n",
            filename="src/medium.py",
            edit_type=EDIT_TYPE.ADDED,
        )
        small_a_file = FilePatchInfo(
            base_file="",
            head_file="small_a\n",
            patch="@@ -0,0 +1 @@\n+small_a\n",
            filename="src/small_a.py",
            edit_type=EDIT_TYPE.ADDED,
        )
        small_b_file = FilePatchInfo(
            base_file="",
            head_file="small_b\n",
            patch="@@ -0,0 +1 @@\n+small_b\n",
            filename="src/small_b.py",
            edit_type=EDIT_TYPE.ADDED,
        )
        max_tokens = len(render_full_file_context(small_a_file)) + len(render_full_file_context(small_b_file))

        enriched_diff = append_small_file_context_to_diff(
            diff_text,
            [medium_file, small_a_file, small_b_file],
            MockTokenHandler(),
            max_lines=10,
            max_tokens=max_tokens,
        )

        assert "## Full file context: 'src/medium.py'" not in enriched_diff
        assert "## Full file context: 'src/small_a.py'" in enriched_diff
        assert "## Full file context: 'src/small_b.py'" in enriched_diff

    def test_append_small_file_context_to_diffs_enriches_each_chunk_independently(self):
        diff_texts = [
            "## File: 'src/one.py'\n\n@@ ... @@\n__new hunk__\n1 +updated = True\n",
            "## File: 'src/two.py'\n\n@@ ... @@\n__new hunk__\n1 +enabled = True\n",
        ]
        diff_files = [
            FilePatchInfo(
                base_file="updated = False\n",
                head_file="updated = True\n",
                patch="@@ -1 +1 @@\n-updated = False\n+updated = True\n",
                filename="src/one.py",
                edit_type=EDIT_TYPE.MODIFIED,
            ),
            FilePatchInfo(
                base_file="enabled = False\n",
                head_file="enabled = True\n",
                patch="@@ -1 +1 @@\n-enabled = False\n+enabled = True\n",
                filename="src/two.py",
                edit_type=EDIT_TYPE.MODIFIED,
            ),
        ]

        enriched_diffs = append_small_file_context_to_diffs(
            diff_texts,
            diff_files,
            MockTokenHandler(),
            max_lines=10,
            max_tokens=500,
        )

        assert "## Full file context: 'src/one.py'" in enriched_diffs[0]
        assert "## Full file context: 'src/two.py'" not in enriched_diffs[0]
        assert "## Full file context: 'src/two.py'" in enriched_diffs[1]
        assert "## Full file context: 'src/one.py'" not in enriched_diffs[1]

    def test_render_full_file_context_keeps_blank_line_numbers(self):
        file = FilePatchInfo(
            base_file="",
            head_file="alpha\n\nomega\n",
            patch="@@ -0,0 +1,3 @@\n+alpha\n+\n+omega\n",
            filename="src/blank_lines.py",
            edit_type=EDIT_TYPE.ADDED,
        )

        rendered_context = render_full_file_context(file)

        assert "1 alpha\n2 \n3 omega" in rendered_context

    def test_extract_files_in_diff_supports_matching_quotes_only(self):
        diff_text = (
            "## File: 'src/single.py'\n"
            '## File: "src/double.py"\n'
            "## File: 'src/mismatch.py\"\n"
        )

        extracted_files = extract_files_in_diff(diff_text)

        assert extracted_files == {"src/single.py", "src/double.py"}
