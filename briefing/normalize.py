"""URL canonicalisation and text cleaning - the foundation of reliable dedupe."""
from __future__ import annotations
import hashlib, html, re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote

TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "vero_", "_hs")
TRACKING_EXACT = {
    "ref", "source", "src", "fbclid", "gclid", "gbraid", "wbraid", "msclkid", "igshid",
    "mc_cid", "mc_eid", "cmp", "campaign_id", "sh", "share", "amp", "at_medium",
    "at_campaign", "guccounter", "guce_referrer", "guce_referrer_sig", "__twitter_impression",
    "smid", "partner", "referrer", "utm", "ncid", "sr_share", "taid", "leadsource",
}
# Feed proxies that wrap the real destination in a query parameter.
REDIRECT_PARAMS = ("url", "u", "target", "redirect", "dest")
REDIRECT_HOSTS = ("news.google.com", "feedproxy.google.com", "r.zemanta.com", "trk.klclick.com")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")

STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with", "at", "by",
    "from", "as", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
    "new", "says", "said", "after", "over", "into", "amid", "how", "why", "what", "his",
    "her", "their", "you", "your", "we", "us", "but", "not", "has", "have", "had", "will",
}


def canonical_url(url):
    """Strip tracking noise and unwrap feed-proxy redirects so the same story
    arriving via two feeds collapses to one identity."""
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    # Unwrap known redirect wrappers (may be nested).
    for _ in range(3):
        host = parts.netloc.lower()
        if any(h in host for h in REDIRECT_HOSTS) or "/redirect" in parts.path.lower():
            q = dict(parse_qsl(parts.query, keep_blank_values=True))
            inner = next((q[p] for p in REDIRECT_PARAMS if q.get(p, "").startswith("http")), None)
            if not inner:
                break
            try:
                parts = urlsplit(unquote(inner))
            except ValueError:
                break
        else:
            break

    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    netloc = netloc.replace(":80", "").replace(":443", "")

    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        kl = k.lower()
        if kl in TRACKING_EXACT or kl.startswith(TRACKING_PREFIXES):
            continue
        kept.append((k, v))
    query = urlencode(sorted(kept))

    path = parts.path
    # Drop feed-proxy artefacts and normalise the trailing slash.
    for junk in ("/amp/", "/amp"):
        if path.endswith(junk):
            path = path[: -len(junk)] or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, query, ""))


def clean_text(raw, limit=None):
    """HTML fragment -> readable single-line plain text."""
    if not raw:
        return ""
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    txt = re.sub(r"(?i)<br\s*/?>|</p>", " ", txt)
    txt = _TAG_RE.sub(" ", txt)
    txt = html.unescape(txt)
    txt = txt.replace(" ", " ").replace("[...]", "").replace("&nbsp;", " ")
    txt = _WS_RE.sub(" ", txt).strip()
    if limit and len(txt) > limit:
        cut = txt[:limit]
        if " " in cut[int(limit * 0.6):]:
            cut = cut[: cut.rfind(" ")]
        txt = cut.rstrip(" ,;:-–—.") + "…"
    return txt


def title_tokens(title):
    """Content-bearing token set used for cross-source near-duplicate matching."""
    t = _PUNCT_RE.sub(" ", html.unescape(title or "").lower())
    return {w for w in t.split() if w not in STOPWORDS and len(w) > 2}


def title_norm(title):
    return " ".join(sorted(title_tokens(title)))


def sha(value):
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


_ENT_SPLIT = re.compile(r"[^A-Za-z0-9&+.-]+")

# Different outlets refer to the same actor differently; without this,
# "EU AI Act enforcement" and "Europe starts enforcing the AI Act" look like
# stories about two different places.
ENTITY_ALIASES = {
    "europe": "eu", "european": "eu", "brussels": "eu",
    "usa": "us", "u.s.": "us", "u.s": "us", "america": "us", "american": "us",
    "washington": "us", "britain": "uk", "british": "uk", "england": "uk",
    "prc": "china", "chinese": "china", "russian": "russia", "moscow": "russia",
    "kremlin": "russia", "beijing": "china", "korean": "korea",
}

_MONEY_RE = re.compile(
    r"[$€£¥]\s?(\d[\d.,]*)\s*(trillion|billion|million|thousand|bn|mn|tn|[bmkt])?\b", re.I)
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)
_SCALE = {"trillion": "t", "tn": "t", "t": "t", "billion": "b", "bn": "b", "b": "b",
          "million": "m", "mn": "m", "m": "m", "thousand": "k", "k": "k"}


def alias(word):
    return ENTITY_ALIASES.get(word, word)


def money_tokens(title):
    """Monetary figures as single atomic tokens.

    '$12.3M' and '$12.3 million' both become 'amt12.3m'. Plain tokenisation
    shatters these into fragments too short to survive, throwing away one of the
    strongest signals that two headlines describe the same deal or ransom.
    """
    out = set()
    for num, scale in _MONEY_RE.findall(title or ""):
        num = num.rstrip(".,").replace(",", "")
        suffix = _SCALE.get((scale or "").lower(), "")
        out.add("amt" + num + suffix)
    return out


def key_signals(title):
    """The identifying facts of a headline: named entities, money and CVE ids."""
    sigs = set(title_entities(title)) | money_tokens(title)
    sigs |= {c.lower() for c in _CVE_RE.findall(title or "")}
    return sigs



def title_words(title):
    """Every word of the headline, lowercased - including short ones that
    title_tokens() drops. Used to test whether an entity is truly absent."""
    if not title:
        return set()
    words = {w.strip(".-&+").lower() for w in _ENT_SPLIT.split(html.unescape(title)) if w}
    return words | {ENTITY_ALIASES.get(w, w) for w in words} | money_tokens(title)


def title_entities(title):
    """Proper nouns / acronyms from the ORIGINAL casing - the words that actually
    identify *which* product, vendor or org a headline is about.

    'CISA adds Ivanti flaw to KEV catalog' -> {cisa, ivanti, kev}
    Capitalisation is the cheapest reliable entity signal available offline, and
    distinguishing Ivanti from Citrix is exactly what prevents wrong merges.
    """
    if not title:
        return set()
    words = [w for w in _ENT_SPLIT.split(html.unescape(title)) if w]
    ents = set()
    for w in words:
        core = w.strip(".-&+")
        if len(core) < 2:
            continue
        low = core.lower()
        if low in STOPWORDS or low in GENERIC_ENTITY_NOISE:
            continue
        # The first word is included too: headline case capitalises it either way,
        # so excluding it would make comparison asymmetric between two feeds that
        # order the same facts differently.
        if core.isupper() or any(c.isupper() for c in core[1:]) or core[0].isupper():
            ents.add(ENTITY_ALIASES.get(low, low))
    return ents


# Words that are frequently capitalised in headline case but identify nothing.
GENERIC_ENTITY_NOISE = {
    "new", "the", "a", "an", "how", "why", "what", "when", "report", "reports",
    "update", "updates", "security", "cyber", "data", "million", "billion",
    "warns", "says", "adds", "here", "top", "best", "first", "day", "zero",
}
