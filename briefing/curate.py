"""Claude-driven curation: priority, running order, and the details line.

The keyword scorer still runs first - it picks which candidates are worth an API
call and remains the safety net. Claude then makes the editorial judgement a
keyword table cannot: whether a story actually changes what a director should do
today. Every failure path degrades to the keyword ranking, because a briefing
that arrives keyword-ranked is infinitely better than one that does not arrive.
"""
from __future__ import annotations
import json, os, ssl, time
import urllib.error, urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

PRIORITIES = ("P1", "P2", "P3")


class CurationUnavailable(RuntimeError):
    """Raised when Claude cannot rank - always caught, never fatal."""


SYSTEM_PROMPT = """You are the editor of a daily intelligence briefing for the Director of \
Technology and Security at a mid-sized enterprise. You decide what reaches their desk each \
morning and in what order.

Your reader is accountable for security posture, technology strategy and budget. They are not \
a SOC analyst and not a researcher. They act on things, brief their board on things, and ignore \
the rest.

For each story assign a priority:

  P1 - Act today. Something in their environment or their obligations changed. Actively \
exploited vulnerabilities in widely deployed enterprise software, emergency directives, a \
breach at a peer or supplier, a regulatory deadline that just became real, a market move that \
changes a build-or-buy decision they are currently making.
  P2 - Review this week. Materially informs a decision or a board conversation, but nothing \
breaks if it waits until Thursday.
  P3 - Monitor. Worth knowing the direction of travel. Background.

Judge significance, not drama. A vendor press release describing a "critical" feature is not \
P1. A quietly worded advisory about a product that every enterprise runs may well be. Weigh how \
widely deployed the affected technology is, whether exploitation is actually happening, and \
whether the reader can realistically do anything in response.

Be strict with P1. At most {max_p1} per table, and fewer is normal - on an ordinary day one or \
zero stories genuinely demand action. If you mark everything urgent the priority column stops \
carrying information.

Order each table by how much the reader needs to see it, most important first. Priority and \
order should broadly agree, but you may rank a compelling P2 above a marginal P1 if that reads \
better.

Also write the "details" line for each story: one sentence, at most 28 words, stating what \
happened and why it matters to this reader. Lead with the concrete fact - the affected product, \
the CVE, the figure, the deadline. Strip vendor marketing language, "[...]" artifacts and \
outlet self-promotion. Never begin with "This article" or "The story". If the source summary is \
empty or useless, write the line from the headline alone. Write plainly and do not editorialise \
beyond the significance judgement.

You are ranking two independent tables: cyber security and AI news. Rank each on its own terms; \
do not compare across them.

Return a verdict for every story you are given, using its exact id. Do not invent ids and do not \
omit any."""

TOOL = {
    "name": "submit_briefing",
    "description": "Submit the ranked briefing. Every story supplied must appear exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cyber": {
                "type": "array",
                "description": "Cyber security stories, most important first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "priority": {"type": "string", "enum": list(PRIORITIES)},
                        "details": {"type": "string",
                                    "description": "One sentence, max 28 words, why it matters."},
                    },
                    "required": ["id", "priority", "details"],
                },
            },
            "ai": {
                "type": "array",
                "description": "AI stories, most important first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "priority": {"type": "string", "enum": list(PRIORITIES)},
                        "details": {"type": "string",
                                    "description": "One sentence, max 28 words, why it matters."},
                    },
                    "required": ["id", "priority", "details"],
                },
            },
        },
        "required": ["cyber", "ai"],
    },
}


# ------------------------------------------------------------------ api key
def api_key(cfg):
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        return env
    path = cfg.root / cfg.path("curation.api_key_file", "secrets/anthropic_api_key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    raise CurationUnavailable(
        "no Anthropic API key (set ANTHROPIC_API_KEY or create %s)" % path)


# ------------------------------------------------------------------ payload
def _age_label(item, now):
    pub = item.get("published")
    if not pub or item.get("published_missing"):
        return "unknown"
    hours = max(0.0, (now - pub).total_seconds() / 3600.0)
    return "%dh ago" % int(round(hours))


def build_candidates(tables, cfg, now):
    """Trim to the strongest candidates per domain and give each a stable id."""
    cap = cfg.path("curation.max_candidates_per_domain", 25)
    payload, index = {"cyber": [], "ai": []}, {}
    for domain in ("cyber", "ai"):
        for n, item in enumerate(tables[domain][:cap]):
            sid = "%s-%d" % (domain[0], n + 1)
            item["_cid"] = sid
            index[sid] = item
            entry = {
                "id": sid,
                "source": item["source"],
                "title": item["title"],
                "age": _age_label(item, now),
                "summary": (item.get("summary") or "")[:420],
            }
            if item.get("cves"):
                entry["cves"] = item["cves"]
            if item.get("cvss"):
                entry["cvss"] = item["cvss"]
            if item.get("also_in"):
                entry["also_reported_by"] = item["also_in"][:3]
            payload[domain].append(entry)
    return payload, index


def _request(cfg, key, body, timeout):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, method="POST", headers={
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_claude(cfg, payload, log=lambda *_: None):
    key = api_key(cfg)
    model = cfg.path("curation.model", "claude-opus-5")
    timeout = cfg.path("curation.timeout_seconds", 120)
    retries = cfg.path("curation.retries", 1)
    max_p1 = cfg.path("scoring.max_p1_per_table", 4)

    body = {
        "model": model,
        "max_tokens": cfg.path("curation.max_tokens", 8000),
        "system": SYSTEM_PROMPT.format(max_p1=max_p1),
        "tools": [TOOL],
        # Forcing the tool removes the whole class of "model wrote prose around
        # the JSON" failures - the response is schema-valid or it is an error.
        "tool_choice": {"type": "tool", "name": "submit_briefing"},
        "messages": [{
            "role": "user",
            "content": ("Today's candidate stories, already de-duplicated across sources.\n\n"
                        + json.dumps(payload, indent=1, ensure_ascii=False)
                        + "\n\nRank both tables and submit the briefing."),
        }],
    }

    last = None
    for attempt in range(retries + 1):
        try:
            resp = _request(cfg, key, body, timeout)
            for block in resp.get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "submit_briefing":
                    usage = resp.get("usage", {})
                    log("  Claude %s: %s in / %s out tokens"
                        % (model, usage.get("input_tokens", "?"), usage.get("output_tokens", "?")))
                    return block.get("input") or {}
            raise CurationUnavailable("model returned no tool_use block")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last = "HTTP %s: %s" % (exc.code, detail)
            # Auth and request errors will not fix themselves on a retry.
            if exc.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    raise CurationUnavailable(last or "unknown error")


# --------------------------------------------------------------- validation
def apply_verdict(verdict, index, tables, cfg, log=lambda *_: None):
    """Trust Claude's judgement, verify its bookkeeping.

    A model can omit an item, invent an id or emit an out-of-range priority.
    None of those may drop a story from the briefing, so anything unaccounted
    for keeps its keyword-derived priority and is appended in keyword order.
    """
    if not isinstance(verdict, dict):
        raise CurationUnavailable("verdict was not an object")

    rewrite = cfg.path("curation.rewrite_details", True)
    max_p1 = cfg.path("scoring.max_p1_per_table", 4)
    out, stats = {}, {"ranked": 0, "unknown_ids": 0, "restored": 0, "rewritten": 0}

    for domain in ("cyber", "ai"):
        original = [i for i in tables[domain] if i.get("_cid")]
        by_id = {i["_cid"]: i for i in original}
        rows = verdict.get(domain)
        if not isinstance(rows, list):
            rows = []

        ordered, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("id", ""))
            item = by_id.get(sid)
            if item is None:
                stats["unknown_ids"] += 1
                continue
            if sid in seen:
                continue
            seen.add(sid)

            priority = str(row.get("priority", "")).upper().strip()
            if priority in PRIORITIES:
                item["priority"] = priority
            details = (row.get("details") or "").strip()
            if rewrite and len(details) >= 15:
                item["claude_details"] = details
                stats["rewritten"] += 1
            item["curated"] = True
            ordered.append(item)
            stats["ranked"] += 1

        # Anything Claude did not return keeps its keyword priority and order.
        missing = [i for i in original if i["_cid"] not in seen]
        stats["restored"] += len(missing)
        ordered.extend(missing)

        # Readability guard: the prompt asks for restraint, this enforces it.
        # Same proportional rule the keyword path uses, so "P1" means the same
        # thing whichever ranking produced the table. Only ever demotes the
        # lowest-ranked excess, so Claude's top calls always stand.
        budget = min(max_p1, max(1, -(-len(ordered) // 3)))
        n1 = 0
        for item in ordered:
            if item["priority"] == "P1":
                n1 += 1
                if n1 > budget:
                    item["priority"] = "P2"

        for item in ordered:
            item.pop("_cid", None)
        out[domain] = ordered

    if stats["ranked"] == 0:
        raise CurationUnavailable("verdict matched none of the supplied stories")
    return out, stats


def curate(tables, cfg, now, log=lambda *_: None):
    """Returns (tables, status_dict). Never raises."""
    status = {"used": False, "model": cfg.path("curation.model", "claude-opus-5"), "error": None}
    if not cfg.path("curation.enabled", True):
        status["error"] = "disabled in settings"
        return tables, status
    if not any(tables[d] for d in ("cyber", "ai")):
        status["error"] = "nothing to rank"
        return tables, status

    try:
        payload, index = build_candidates(tables, cfg, now)
        log("\nAsking %s to prioritise %d cyber and %d AI stories…"
            % (status["model"], len(payload["cyber"]), len(payload["ai"])))
        verdict = call_claude(cfg, payload, log=log)
        ranked, stats = apply_verdict(verdict, index, tables, cfg, log=log)
        status.update(used=True, **stats)
        log("  Ranked %d, rewrote %d details%s."
            % (stats["ranked"], stats["rewritten"],
               (", restored %d unranked" % stats["restored"]) if stats["restored"] else ""))
        return ranked, status
    except CurationUnavailable as exc:
        log("\nClaude ranking unavailable (%s) - falling back to keyword scoring." % exc)
        status["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - curation must never break delivery
        log("\nClaude ranking failed (%s: %s) - falling back to keyword scoring."
            % (type(exc).__name__, exc))
        status["error"] = "%s: %s" % (type(exc).__name__, exc)

    for domain in ("cyber", "ai"):
        for item in tables[domain]:
            item.pop("_cid", None)
    return tables, status
