"""Three-layer duplicate suppression.

  Layer 1  recency window  - only genuinely new material enters the pipeline
  Layer 2  in-run collapse - the same story arriving from several feeds becomes one row
  Layer 3  persistent store - anything already emailed is never emailed again
"""
from __future__ import annotations
from datetime import timedelta
from difflib import SequenceMatcher

from .normalize import key_signals, title_entities, title_words
from .store import utcnow


# High-frequency vocabulary carries almost no identifying signal: two unrelated
# stories both saying "new attack exploits flaw" are not the same story. Entity
# words (product and company names) are what actually identify an article.
GENERIC = {
    "new", "news", "report", "reports", "reported", "warns", "warning", "says",
    "patch", "patches", "patched", "fix", "fixes", "fixed", "flaw", "flaws", "bug",
    "bugs", "attack", "attacks", "attackers", "hackers", "hacker", "exploit",
    "exploits", "exploited", "exploiting", "active", "actively", "used", "using",
    "zero", "day", "zeroday", "critical", "high", "severe", "security", "cyber",
    "cybersecurity", "vulnerability", "vulnerabilities", "threat", "threats",
    "malware", "ransomware", "breach", "data", "users", "customers", "company",
    "firm", "million", "billion", "update", "updates", "release", "released",
    "releases", "launch", "launches", "launched", "announces", "announced",
    "reveals", "revealed", "adds", "now", "could", "can", "may", "more", "than",
    "about", "against", "after", "before", "amid", "here", "why", "how", "what",
    "ai", "artificial", "intelligence", "model", "models", "tool", "tools",
}
WEIGHT_GENERIC = 0.35


def _weight(tok):
    return WEIGHT_GENERIC if tok in GENERIC else 1.0


def _distinctive(tokens):
    return {t for t in tokens if t not in GENERIC and len(t) > 3}


def _similar(a, b, jac_t, seq_t):
    """Entity-weighted near-duplicate test.

    `a` and `b` are dicts carrying: tokens, entities, title_norm.
    """
    at, bt = a["tokens"], b["tokens"]
    if not at or not bt:
        return False

    ae, be = a["entities"], b["entities"]

    # VETO FIRST. If each headline names an entity that is completely absent from
    # the other, they are about different subjects no matter how similar the
    # phrasing. This keeps "CISA adds Ivanti flaw" apart from "CISA adds Citrix
    # flaw". Exclusivity is tested against every word of the other headline, so a
    # word that is merely capitalised on one side ("Stadler Rail" vs "rail giant
    # Stadler") does not count as a conflict.
    excl_a = {e for e in ae if e not in b["words"]}
    excl_b = {e for e in be if e not in a["words"]}
    if excl_a and excl_b:
        return False

    inter = at & bt
    wi = sum(_weight(t) for t in inter)
    wa = sum(_weight(t) for t in at)
    wb = sum(_weight(t) for t in bt)
    wu = wa + wb - wi
    wj = (wi / wu) if wu else 0.0

    if wj >= jac_t:
        return True

    # Identical subjects, reworded around them: "OpenAI raises $40B at a $340B
    # valuation" vs "OpenAI closes $40B round at $340B valuation".
    if ae and ae == be and wj >= 0.45:
        return True

    # The decisive test for real cross-source rewrites. Two outlets covering one
    # event share the identifying facts - vendor, product, CVE, dollar figure -
    # while sharing very little ordinary wording. Compare only those facts.
    ka, kb = a["keys"], b["keys"]
    shared = ka & kb
    if len(shared) >= 2:
        containment = len(shared) / min(len(ka), len(kb))
        if containment >= 0.6:
            return True

    dist_shared = _distinctive(at) & _distinctive(bt)
    smaller = min(len(at), len(bt))
    if smaller >= 4 and len(inter) / smaller >= 0.85 and dist_shared:
        return True

    return SequenceMatcher(None, a["title_norm"], b["title_norm"]).ratio() >= seq_t


def sig(title, title_norm_value=None):
    """Build the comparison signature for a headline."""
    from .normalize import title_norm as _tn
    tn = title_norm_value if title_norm_value is not None else _tn(title)
    return {"tokens": set(tn.split()), "entities": title_entities(title),
            "words": title_words(title), "keys": key_signals(title), "title_norm": tn}


def filter_recent(items, cfg, last_run, force=False, since_hours=None):
    """Keep items published inside the lookback window."""
    now = utcnow()
    lookback = since_hours if since_hours is not None else cfg.path("lookback_hours", 30)
    hard_max = cfg.path("hard_max_age_hours", 72)

    cutoff = now - timedelta(hours=lookback)
    if since_hours is not None:
        # An explicit --since is a deliberate instruction from the operator and
        # overrides the safety ceiling. Clamping it silently would make the flag
        # lie about what it does.
        pass
    else:
        if last_run and not force:
            # Extend back to the previous run so a missed day is still covered,
            # but never further than the hard ceiling.
            cutoff = min(cutoff, last_run - timedelta(hours=1))
        cutoff = max(cutoff, now - timedelta(hours=hard_max))

    kept, dropped = [], 0
    for it in items:
        pub = it.get("published")
        if pub is None:
            # Undated entries are common in advisory feeds; let the seen-store decide.
            it["published"] = now
            it["published_missing"] = True
            kept.append(it)
            continue
        if pub > now + timedelta(hours=6):   # clock-skewed / future-dated
            pub = now
            it["published"] = now
        if pub >= cutoff:
            kept.append(it)
        else:
            dropped += 1
    return kept, dropped, cutoff


def collapse_in_run(items, cfg):
    """Merge near-identical stories across sources. Highest-tier source wins;
    the others are recorded as corroboration."""
    jac = cfg.path("dedupe.jaccard_threshold", 0.70)
    seq = cfg.path("dedupe.sequence_threshold", 0.86)

    # Strongest signal first so the survivor is the most authoritative source.
    ordered = sorted(items, key=lambda i: (-i.get("source_tier", 1.0), i.get("published") or utcnow()))

    kept = []
    by_url, by_guid = {}, {}
    merged = 0
    for it in ordered:
        it["_sig"] = sig(it["title"], it["title_norm"])

        anchor = by_url.get(it["url_hash"]) or by_guid.get(it["guid_hash"])
        if anchor is None:
            for cand in kept:
                if cand["domain"] != it["domain"]:
                    continue
                if _similar(cand["_sig"], it["_sig"], jac, seq):
                    anchor = cand
                    break
        if anchor is not None:
            merged += 1
            if it["source"] != anchor["source"] and it["source"] not in anchor.setdefault("also_in", []):
                anchor["also_in"].append(it["source"])
            # Prefer the richest summary available across the cluster.
            if len(it.get("summary") or "") > len(anchor.get("summary") or ""):
                anchor["summary"] = it["summary"]
            continue

        it.setdefault("also_in", [])
        kept.append(it)
        by_url[it["url_hash"]] = it
        by_guid[it["guid_hash"]] = it

    for it in kept:
        it.pop("_sig", None)
    return kept, merged


def filter_unseen(items, store, cfg, force=False):
    """Drop anything already emailed - exact identity first, then fuzzy title
    against the recent send history."""
    if force:
        return items, 0

    jac = cfg.path("dedupe.jaccard_threshold", 0.70)
    seq = cfg.path("dedupe.sequence_threshold", 0.86)
    window = cfg.path("dedupe.fuzzy_title_window_days", 10)
    history = [sig(t, n) for n, t in store.recent_title_norms(window) if n]

    kept, suppressed = [], 0
    for it in items:
        if store.is_seen(it):
            suppressed += 1
            continue
        s_it = sig(it["title"], it["title_norm"])
        if any(_similar(s_it, h, jac, seq) for h in history):
            suppressed += 1
            continue
        kept.append(it)
    return kept, suppressed
