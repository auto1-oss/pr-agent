"""Parse, normalize and render the blocker review. The hidden block is a contract with merge tools: keep it stable."""
import re

import yaml

BLOCK_START = "<!-- pr-agent-review"
BLOCK_MODE = "review-and-blockers"
BLOCKER_TYPES = ("defect", "criterion_missing", "outside_ticket", "undeclared_dependency", "unsafe_alone")
RISK_LEVELS = ("low", "mid", "high")
EVIDENCE_STATUSES = ("proven", "failed", "missing", "outdated")
MAX_EVIDENCE_NOTE_CHARS = 300
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


def source_key(value) -> str:
    """Normalize a proof source so the model's spelling of a link or of the description matches ours."""
    text = _text(value).strip("<>").rstrip("/").lower()
    return "description" if text in ("description", "pr description") else text


def _evidence(status: str, source: str = "", by: str = "", by_author: str = "", note: str = "") -> dict:
    return {"status": status, "source": source, "by": by, "by_author": by_author, "note": note}


def normalize_evidence(item, sources: dict | None) -> dict:
    """Validate one proof verdict. Who wrote the proof comes from ``sources``, never from the model.

    ``sources`` maps ``source_key`` of every comment shown to the model (and of the description) to
    ``{"source", "by", "by_author"}``. A proof that points anywhere else is not proof.
    """
    item = item if isinstance(item, dict) else {}
    status = _choice(item.get("status"), EVIDENCE_STATUSES, "missing")
    note = _text(item.get("note"))[:MAX_EVIDENCE_NOTE_CHARS]
    if status == "missing":
        return _evidence("missing", note=note)
    known = (sources or {}).get(source_key(item.get("source")))
    if known:
        return _evidence(status, known["source"], known["by"], known["by_author"], note)
    if status == "proven":
        return _evidence("missing", note="; ".join(filter(None, ["source not found", note])))
    # A failed or outdated result may live outside the PR, for example a CI run the model found linked.
    return _evidence(status, _text(item.get("source")), note=note)


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


def normalize_review(data: dict, previous: dict, max_not_blocking: int, sources: dict | None = None) -> dict:
    """Validate the model output. Blockers that do not fit a type or lack a case become Not blocking."""
    if not isinstance(data, dict):
        raise ValueError("Blocker review output is not a mapping")
    changes = [
        {"id": item.get("id"), "text": _text(item.get("text")), "short": _text(item.get("short") or item.get("text")),
         "evidence": normalize_evidence(item.get("evidence"), sources)}
        for item in data.get("what_it_does") or [] if isinstance(item, dict) and _text(item.get("text"))
    ]
    if not changes or not isinstance(data.get("blockers"), list):
        # A cut-off or malformed answer must fail the run, never read as a review without blockers. A mapping
        # or an empty value in place of the list would otherwise drop every blocker in it.
        raise ValueError("Blocker review output has no What it does sentences or no blockers list")
    ticket = data.get("ticket") if isinstance(data.get("ticket"), dict) else {}
    blockers, not_blocking = [], []
    for item in data["blockers"]:
        if not isinstance(item, dict):
            # A blocker written as plain text has no type or case, so it is shown as not blocking.
            if _text(item):
                not_blocking.append(_text(item))
            continue
        if not _text(item.get("text")):
            # A finding under another key (description, message) would otherwise vanish from the review.
            raise ValueError("Blocker review output has a blocker without text")
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
    """Return the hidden block of an earlier review, or an empty dict when there is none.

    Scalars stay strings (``yes`` is not True, a sha of digits is not a number), as merge tools read them.
    """
    match = _BLOCK_RE.search(body or "")
    if not match:
        return {}
    try:
        data = yaml.load(match.group(1), Loader=yaml.BaseLoader)  # noqa: S506 - BaseLoader builds no objects
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    parsed = {key: _text(data.get(key)) for key in
              ("pr", "reviewed_sha", "ticket", "criteria", "risk", "risk_reason", "reversible", "undo")}
    parsed.update({key: [item for item in data.get(key) or [] if isinstance(item, dict)]
                   for key in ("changes", "blockers")})
    for change in parsed["changes"]:
        evidence = change.get("evidence") if isinstance(change.get("evidence"), dict) else {}
        change["evidence"] = _evidence(_choice(evidence.get("status"), EVIDENCE_STATUSES, "missing"),
                                       *(_text(evidence.get(key)) for key in ("source", "by", "by_author", "note")))
    ids = _IDS_RE.search(body[match.end():])
    parsed["highest"] = {"C": int(ids.group(1)), "B": int(ids.group(2))} if ids else {}
    return parsed


def review_from_block(parsed: dict) -> dict:
    """Rebuild what render_hidden_block needs from a parsed block, to write it again with new proof."""
    changes = [{"id": _text(c.get("id")), "text": _text(c.get("text")), "short": _text(c.get("text")),
                "evidence": c["evidence"]} for c in parsed.get("changes", [])]
    blockers = [{key: _text(b.get(key)) for key in ("id", "type", "text", "case")} for b in parsed.get("blockers", [])]
    reversible = _choice(parsed.get("reversible"), ("yes", "no"), "no")
    return {"changes": changes, "risk": _choice(parsed.get("risk"), RISK_LEVELS, "high"),
            "risk_reason": parsed.get("risk_reason", ""), "reversible": reversible,
            "undo": parsed.get("undo", "") if reversible == "no" else "",
            "criteria": _choice(parsed.get("criteria"), ("present", "missing"), "missing"), "ticket_check": "",
            "blockers": blockers, "not_blocking": [], "highest": parsed.get("highest", {})}


def _scalar(value: str) -> str:
    return yaml.safe_dump(value, width=1 << 30, allow_unicode=True).strip().removesuffix("...").strip()


def render_hidden_block(review: dict, pr_ref: str, head_sha: str, ticket_key: str) -> str:
    lines = [BLOCK_START, f"mode: {BLOCK_MODE}", f"pr: {_scalar(_text(pr_ref))}", f"reviewed_sha: {head_sha}",
             f"ticket: {_scalar(_text(ticket_key) or 'none')}", f"criteria: {review['criteria']}", "changes:"]
    for change in review["changes"]:
        evidence = change.get("evidence") or _evidence("missing")
        lines += [f"  - id: {change['id']}", f"    text: {_scalar(change['short'])}", "    evidence:",
                  f"      status: {evidence['status']}"]
        lines += [f"      {key}: {_scalar(evidence[key])}" for key in ("source", "by", "by_author", "note")]
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


_LINK_RE = re.compile(r"https?://[^\s()\[\]<>]+")


def _proof_line(change: dict) -> str:
    evidence = change.get("evidence") or _evidence("missing")
    parts = [f"- **{change['id']}** {evidence['status'].capitalize()}"]
    source = evidence["source"]
    if source == "description":
        where = "PR description"
    elif _LINK_RE.fullmatch(source):
        kind = "comment" if re.search(r"#(issuecomment|discussion_r|pullrequestreview)-?\d", source) else "link"
        where = f"[{kind}]({source})"
    else:
        where = source.replace("|", "/")
    if where and evidence["by"]:
        where += f" by {evidence['by']}" + (" (author)" if evidence["by_author"] == "yes" else "")
    return " · ".join(parts + [text for text in (where, evidence["note"].replace("|", "/")) if text])


def render_proof(review: dict) -> str:
    """The visible Proof section, from the heading to the blank line before Blockers."""
    return "\n".join(["### Proof", ""] + [_proof_line(change) for change in review["changes"]])


def replace_proof(body: str, review: dict) -> str | None:
    """Swap the Proof section and the hidden block of a published review; None when the body has no block.

    Only the proof changes. The rest of the comment, written by the full review, stays as it is.
    """
    match = _BLOCK_RE.search(body or "")
    ids = _IDS_RE.match(body, match.end() + 1) if match else None
    blockers = body.find("\n### Blockers\n") if match else -1
    if not match or not ids or blockers < 0 or blockers > match.start():
        return None
    proof = body.find("\n### Proof\n", 0, blockers)
    head = body[:proof] if proof >= 0 else body[:blockers]
    parsed = parse_hidden_block(body)
    block = render_hidden_block(review, parsed["pr"], parsed["reviewed_sha"], "" if parsed["ticket"] == "none"
                                else parsed["ticket"])
    return head + "\n" + render_proof(review) + "\n" + body[blockers:match.start()] + block + body[ids.end():]


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
            f"| **Ticket** | {ticket_cell.replace('|', '/')} |", "", render_proof(review), "", "### Blockers", ""]
    if not review["blockers"]:
        out.append(f"None on `{short_sha}`.")
    for blocker in review["blockers"]:
        out.append(f"- **{blocker['id']}** `{blocker['type']}` {blocker['text']} Case: {blocker['case']}")
    out += ["", "### Not blocking", ""]
    out += [f"{index}. {text}" for index, text in enumerate(review["not_blocking"], start=1)] or ["Nothing."]
    out += ["", render_hidden_block(review, pr_ref, head_sha, key)]
    return "\n".join(out)
