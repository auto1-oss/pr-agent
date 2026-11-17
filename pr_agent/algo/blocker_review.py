"""Parse, normalize and render the blocker review. The hidden block is a contract with merge tools: keep it stable."""
import re

import yaml

BLOCK_START = "<!-- pr-agent-review"
BLOCK_MODE = "review-and-blockers"
BLOCKER_TYPES = ("defect", "criterion_missing", "outside_ticket", "undeclared_dependency", "unsafe_alone")
RISK_LEVELS = ("low", "mid", "high")
_BLOCK_RE = re.compile(r"<!-- pr-agent-review\n(.*?)\n-->", re.DOTALL)
# Highest ids ever used, kept outside the contract block so a dropped id is never given to a new item.
_IDS_RE = re.compile(r"<!-- pr-agent:blocker-ids C=(\d+) B=(\d+) -->")


def _text(value) -> str:
    """Return a single-line string that can neither open nor close an HTML comment."""
    if isinstance(value, dict):
        value = "; ".join(f"{key}: {item}" for key, item in value.items())
    text = "" if value is None else " ".join(str(value).split())
    return text.replace("-->", "- ->").replace("--!>", "- -!>").replace("<!--", "<!- -")


def _choice(value, allowed: tuple, default: str) -> str:
    value = _text(value).lower()
    return value if value in allowed else default


def _yes_no(value, default: str) -> str:
    return ("yes" if value else "no") if isinstance(value, bool) else _choice(value, ("yes", "no"), default)


def _number(item_id: str, prefix: str) -> int | None:
    match = re.fullmatch(rf"{prefix}([1-9]\d{{0,3}})", _text(item_id).upper())
    return int(match.group(1)) if match else None


def assign_ids(items: list[dict], previous_ids: list[str], prefix: str, highest: int = 0) -> list[dict]:
    """Keep ids the previous review used; other items get a number above every number used so far."""
    previous = {number for item_id in previous_ids if (number := _number(item_id, prefix)) is not None}
    next_number = max(previous | {highest}) + 1
    seen = set()
    for item in items:
        number = _number(item.get("id"), prefix)
        if number is None or number not in previous or number in seen:
            number = next_number
            next_number += 1
        seen.add(number)
        item["id"] = f"{prefix}{number}"
    return items


def normalize_review(data: dict, previous: dict, max_not_blocking: int) -> dict:
    """Validate the model output. Blockers that do not fit a type or lack a case become Not blocking."""
    if not isinstance(data, dict):
        raise ValueError("Blocker review output is not a mapping")
    changes = [
        {"id": item.get("id"), "text": _text(item.get("text")), "short": _text(item.get("short") or item.get("text"))}
        for item in data.get("what_it_does") or [] if isinstance(item, dict) and _text(item.get("text"))
    ]
    if not changes or "blockers" not in data:
        # A cut-off answer must fail the run, never read as a review without blockers.
        raise ValueError("Blocker review output has no What it does sentences or no blockers key")
    ticket = data.get("ticket") if isinstance(data.get("ticket"), dict) else {}
    blockers, not_blocking = [], []
    for item in data.get("blockers") or []:
        if not isinstance(item, dict) or not _text(item.get("text")):
            continue
        blocker = {"id": item.get("id"), "type": _text(item.get("type")).lower(), "text": _text(item.get("text")),
                   "case": _text(item.get("case"))}
        if blocker["type"] in BLOCKER_TYPES and blocker["case"]:
            blockers.append(blocker)
        else:
            not_blocking.append(blocker["text"])
    not_blocking += [_text(item) for item in data.get("not_blocking") or [] if _text(item)]
    reversible = _yes_no(data.get("reversible"), "no")
    highest = previous.get("highest", {})
    review = {
        "changes": assign_ids(changes, [c.get("id") for c in previous.get("changes", [])], "C", highest.get("C", 0)),
        "risk": _choice(data.get("risk"), RISK_LEVELS, "high"),
        "risk_reason": _text(data.get("risk_reason")),
        "reversible": reversible,
        "undo": _text(data.get("undo")) if reversible == "no" else "",
        "criteria": _choice(ticket.get("criteria"), ("present", "missing"), "missing"),
        "ticket_check": _text(ticket.get("summary")),
        "blockers": assign_ids(blockers, [b.get("id") for b in previous.get("blockers", [])], "B", highest.get("B", 0)),
        "not_blocking": not_blocking[:max(0, max_not_blocking)],
    }
    review["highest"] = {prefix: max([highest.get(prefix, 0)] + [_number(i["id"], prefix) for i in review[key]])
                         for prefix, key in (("C", "changes"), ("B", "blockers"))}
    return review


def parse_hidden_block(body: str) -> dict:
    """Return the hidden block of an earlier review, or an empty dict when there is none."""
    match = _BLOCK_RE.search(body or "")
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    parsed = {key: [item for item in data.get(key) or [] if isinstance(item, dict)] for key in ("changes", "blockers")}
    ids = _IDS_RE.search(body[match.end():])
    parsed["highest"] = {"C": int(ids.group(1)), "B": int(ids.group(2))} if ids else {}
    return parsed


def _scalar(value: str) -> str:
    return yaml.safe_dump(value, width=1 << 30, allow_unicode=True).strip().removesuffix("...").strip()


def render_hidden_block(review: dict, pr_ref: str, head_sha: str, ticket_key: str) -> str:
    lines = [BLOCK_START, f"mode: {BLOCK_MODE}", f"pr: {_scalar(_text(pr_ref))}", f"reviewed_sha: {head_sha}",
             f"ticket: {_scalar(_text(ticket_key) or 'none')}", f"criteria: {review['criteria']}", "changes:"]
    for change in review["changes"]:
        lines += [f"  - id: {change['id']}", f"    text: {_scalar(change['short'])}"]
    lines += [f"risk: {review['risk']}", f"risk_reason: {_scalar(review['risk_reason'])}",
              f"reversible: {review['reversible']}"]
    if review["reversible"] == "no":
        lines.append(f"undo: {_scalar(review['undo'] or 'not stated')}")
    lines.append("blockers:" if review["blockers"] else "blockers: []")
    for blocker in review["blockers"]:
        lines += [f"  - id: {blocker['id']}", f"    type: {blocker['type']}",
                  f"    text: {_scalar(blocker['text'])}", f"    case: {_scalar(blocker['case'])}"]
    highest = review.get("highest", {})
    return "\n".join(lines + ["-->", f"<!-- pr-agent:blocker-ids C={highest.get('C', 0)} B={highest.get('B', 0)} -->"])


def render_comment(review: dict, pr_ref: str, head_sha: str, ticket: dict) -> str:
    short_sha = head_sha[:7]
    out = [f"## PR Review · reviewed `{short_sha}`", "", "### What it does", ""]
    out += [f"- **{change['id']}** {change['text']}" for change in review["changes"]]
    reversible = f"**{review['reversible'].capitalize()}**" + (f" · {review['undo']}" if review["undo"] else "")
    key = ticket.get("key") or ""
    ticket_cell = f"[{key}]({ticket['url']})" if key and ticket.get("url") else (key or "No ticket found")
    ticket_cell += f" · Acceptance criteria {review['criteria']}. {review['ticket_check']}".rstrip()
    out += ["", "| | |", "|---|---|",
            f"| **Risk** | **{review['risk'].capitalize()}** · {review['risk_reason']} |",
            f"| **Reversible** | {reversible} |",
            f"| **Ticket** | {ticket_cell.replace('|', '/')} |", "", "### Blockers", ""]
    if not review["blockers"]:
        out.append(f"None on `{short_sha}`.")
    for blocker in review["blockers"]:
        out.append(f"- **{blocker['id']}** `{blocker['type']}` {blocker['text']} Case: {blocker['case']}")
    out += ["", "### Not blocking", ""]
    out += [f"{index}. {text}" for index, text in enumerate(review["not_blocking"], start=1)] or ["Nothing."]
    out += ["", render_hidden_block(review, pr_ref, head_sha, key)]
    return "\n".join(out)
