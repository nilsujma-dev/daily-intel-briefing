"""Director-level prioritisation.

Weighted toward decisions rather than technical detail: exploitation in the wild,
regulatory exposure, and money/market moves outrank incremental product news.
"""
from __future__ import annotations
import re
from datetime import timedelta

from .store import utcnow

# ---------------------------------------------------------------- cyber signals
CYBER_CRITICAL = {
    "actively exploited": 16, "exploited in the wild": 16, "in the wild": 11,
    "zero-day": 13, "zero day": 13, "zero-click": 12, "known exploited": 15,
    "kev catalog": 15, "kev": 9, "emergency directive": 18, "emergency patch": 12,
    "unauthenticated": 9, "remote code execution": 11, "pre-auth": 9,
    "supply chain": 12, "nation-state": 10, "state-sponsored": 10,
    "critical vulnerability": 10, "critical flaw": 10, "cvss 10": 12, "cvss 9": 10,
    "wormable": 12, "no patch": 10, "unpatched": 9, "exploit code": 9,
    "proof-of-concept": 6, "mass exploitation": 14, "widely exploited": 14,
}
CYBER_HIGH = {
    "ransomware": 9, "data breach": 9, "breach": 6, "extortion": 7, "leaked": 5,
    "backdoor": 7, "apt": 6, "threat actor": 5, "malware": 5, "botnet": 4,
    "credential": 5, "phishing": 4, "vulnerability": 4, "exploit": 5,
    "patch tuesday": 7, "advisory": 5, "cisa": 6, "espionage": 7, "wiper": 8,
    "privilege escalation": 6, "authentication bypass": 8, "takedown": 5,
    "insider threat": 6, "misconfiguration": 4, "exposed database": 6,
}
CYBER_BUSINESS = {
    "ciso": 8, "board": 6, "regulation": 8, "regulatory": 7, "compliance": 7,
    "fine": 7, "fined": 7, "lawsuit": 6, "sued": 6, "settlement": 6, "sec ": 6,
    "nis2": 9, "dora": 8, "gdpr": 7, "hipaa": 6, "sox": 5, "insurance": 6,
    "acquisition": 7, "acquires": 7, "merger": 6, "funding": 5, "layoffs": 5,
    "budget": 6, "governance": 6, "third-party risk": 8, "vendor risk": 7,
    "class action": 6, "disclosure rule": 8, "material incident": 9, "8-k": 7,
}

# ------------------------------------------------------------------- ai signals
AI_CRITICAL = {
    "acquires": 14, "acquisition": 14, "merger": 12, "ipo": 13, "buys": 10,
    "raises": 12, "funding round": 12, "series a": 8, "series b": 9, "series c": 10,
    "valuation": 11, "billion": 10, "eu ai act": 15, "ai act": 13,
    "regulation": 11, "regulator": 10, "antitrust": 12, "executive order": 12,
    "export controls": 12, "ban": 9, "lawsuit": 10, "copyright": 9,
    "partnership": 8, "exclusive deal": 9, "shuts down": 9, "layoffs": 8,
}
AI_HIGH = {
    "enterprise": 8, "adoption": 7, "roi": 8, "productivity": 6, "cio": 7,
    "deployment": 6, "governance": 8, "compliance": 8, "risk": 5, "safety": 6,
    "benchmark": 4, "open-source": 6, "open source": 6, "api": 4, "pricing": 7,
    "data center": 8, "datacenter": 8, "capex": 9, "chip": 7, "gpu": 6,
    "nvidia": 6, "openai": 6, "anthropic": 6, "google": 4, "microsoft": 5,
    "meta": 4, "agent": 6, "agents": 6, "revenue": 8, "market share": 8,
    "hiring": 5, "talent": 5, "enterprise customers": 9, "on-premise": 6,
}
AI_BUSINESS = {
    "strategy": 7, "competition": 6, "rival": 5, "market": 5, "contract": 7,
    "deal": 6, "customers": 6, "launch": 5, "enterprise ai": 9, "cost": 6,
    "efficiency": 6, "vendor": 6, "procurement": 7, "board": 6,
}

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)
CVSS_RE = re.compile(r"\bCVSS[^0-9]{0,12}(\d{1,2}(?:\.\d)?)", re.I)
MONEY_RE = re.compile(r"\$\s?\d[\d.,]*\s?(?:b|bn|billion|m|mn|million|k|trillion)?\b", re.I)

BANDS = {"cyber": (CYBER_CRITICAL, CYBER_HIGH, CYBER_BUSINESS),
         "ai": (AI_CRITICAL, AI_HIGH, AI_BUSINESS)}

NOISE = ("sponsored", "sponsor content", "advertisement", "webinar", "[sponsored]",
         "deals of the day", "best deals", "discount", "coupon", "giveaway",
         "black friday", "gift guide", "product review")


def _hits(text, table):
    score, matched = 0, []
    for phrase, weight in table.items():
        if phrase in text:
            score += weight
            matched.append(phrase)
    return score, matched


def score_item(item, cfg):
    domain = item["domain"]
    critical, high, business = BANDS[domain]
    text = (" " + item["title"] + " " + (item.get("summary") or "") + " ").lower()
    title_l = item["title"].lower()

    biz_w = cfg.path("scoring.business_strategy_weight", 1.35)

    s_crit, m_crit = _hits(text, critical)
    s_high, m_high = _hits(text, high)
    s_biz, m_biz = _hits(text, business)

    score = s_crit + (s_high * 0.65) + (s_biz * biz_w)
    reasons = list(dict.fromkeys(m_crit + m_biz + m_high))[:6]

    # Signals appearing in the headline itself matter more than in the body.
    for phrase in list(critical) + list(business):
        if phrase in title_l:
            score += 3

    # Structured severity markers.
    cves = CVE_RE.findall(item["title"] + " " + (item.get("summary") or ""))
    if cves:
        score += 5
        item["cves"] = sorted(set(c.upper() for c in cves))[:4]
    cvss = CVSS_RE.search(text)
    if cvss:
        try:
            val = float(cvss.group(1))
            item["cvss"] = val
            if val >= 9.0:
                score += 10
            elif val >= 7.0:
                score += 5
        except ValueError:
            pass
    if domain == "ai" and MONEY_RE.search(item["title"]):
        score += 6

    # Source authority.
    score *= item.get("source_tier", 1.0)

    # Freshness: a story from two hours ago is more actionable than one from
    # yesterday afternoon, but age never dominates substance.
    pub = item.get("published")
    if pub and not item.get("published_missing"):
        age_h = max(0.0, (utcnow() - pub).total_seconds() / 3600.0)
        boost_window = cfg.path("scoring.recency_boost_hours", 8)
        if age_h <= boost_window:
            score += 5
        elif age_h <= boost_window * 2:
            score += 2
        elif age_h > 48:
            score -= 3

    # Commercial noise has no place in a director's briefing.
    if any(n in text for n in NOISE):
        score -= 22
        item["noise"] = True

    item["score"] = round(score, 2)
    item["reasons"] = reasons
    return item["score"]


def assign_priorities(items, cfg):
    """Absolute thresholds, then quota caps.

    Thresholds alone drift: on a heavy news day everything clears P1 and the
    priority column stops meaning anything. Capping the count keeps "P1" a
    genuine short list of items worth reacting to today. Items are never
    promoted, only demoted, so a quiet day simply yields fewer P1s.
    """
    p1_t = cfg.path("scoring.p1_threshold", 26)
    p2_t = cfg.path("scoring.p2_threshold", 13)
    for it in items:
        s = it["score"]
        it["priority"] = "P1" if s >= p1_t else ("P2" if s >= p2_t else "P3")

    # Scale the quota to the size of the table. A five-item table with four P1s
    # communicates nothing; roughly the top third is a useful "act today" list.
    max_p1 = min(cfg.path("scoring.max_p1_per_table", 4), max(1, -(-len(items) // 3)))
    max_p2 = min(cfg.path("scoring.max_p2_per_table", 6), max(2, -(-len(items) // 2)))

    n1 = 0
    for it in items:                      # already sorted best-first
        if it["priority"] == "P1":
            n1 += 1
            if n1 > max_p1:
                it["priority"] = "P2"
    n2 = 0
    for it in items:
        if it["priority"] == "P2":
            n2 += 1
            if n2 > max_p2:
                it["priority"] = "P3"
    return items


def rank_and_trim(items, cfg):
    """Score, sort and cap each table so the email stays readable."""
    cap = cfg.path("max_items_per_table", 12)
    out = {}
    for domain in ("cyber", "ai"):
        subset = [i for i in items if i["domain"] == domain]
        for i in subset:
            score_item(i, cfg)
        subset = [i for i in subset if not i.get("noise")]
        subset.sort(key=lambda i: (-i["score"], -(i.get("published") or utcnow()).timestamp()))
        subset = subset[:cap]
        assign_priorities(subset, cfg)
        out[domain] = subset
    return out
