import re

from pr_agent.algo.types import EDIT_TYPE, FilePatchInfo


FILE_HEADER_PATTERN = r'^## File: (?:\'([^\']+)\'|"([^\"]+)")$'


def append_small_file_context_to_diff(
    diff_text: str,
    diff_files: list[FilePatchInfo],
    token_handler,
    *,
    max_lines: int,
    max_tokens: int,
) -> str:
    context = build_small_file_context(
        diff_text,
        diff_files,
        token_handler,
        max_lines=max_lines,
        max_tokens=max_tokens,
    )
    if not context:
        return diff_text

    return f"{diff_text}\n\nAdditional context for small touched files:\n======\n{context}\n======"


def build_small_file_context(
    diff_text: str,
    diff_files: list[FilePatchInfo],
    token_handler,
    *,
    max_lines: int,
    max_tokens: int,
) -> str:
    if not diff_text or not diff_files or max_lines <= 0 or max_tokens <= 0:
        return ""

    included_files = extract_files_in_diff(diff_text)
    if not included_files:
        return ""

    remaining_tokens = max_tokens
    context_blocks = []
    candidate_files = []

    for file in diff_files:
        if file.filename not in included_files:
            continue
        if file.edit_type == EDIT_TYPE.DELETED or not file.patch or not file.head_file:
            continue

        line_count = len(file.head_file.splitlines())
        if line_count > max_lines:
            continue

        candidate_files.append((line_count, file.filename, file))

    for _, _, file in sorted(candidate_files, key=lambda item: (item[0], item[1])):
        context_block = render_full_file_context(file)
        context_tokens = token_handler.count_tokens(context_block)
        if context_tokens > remaining_tokens:
            continue

        context_blocks.append(context_block)
        remaining_tokens -= context_tokens

    return "\n\n".join(context_blocks)


def extract_files_in_diff(diff_text: str) -> set[str]:
    matches = re.findall(FILE_HEADER_PATTERN, diff_text, flags=re.MULTILINE)
    return {single_quoted or double_quoted for single_quoted, double_quoted in matches}


def render_full_file_context(file: FilePatchInfo) -> str:
    numbered_lines = []
    for line_number, line in enumerate(file.head_file.splitlines(), start=1):
        numbered_lines.append(f"{line_number} {line}")

    return (
        f"## Full file context: '{file.filename.strip()}'\n"
        "### Current file content after PR changes\n"
        + "\n".join(numbered_lines)
    )
