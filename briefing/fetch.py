"""Zero-dependency RSS / RDF / Atom fetching and parsing."""
from __future__ import annotations
import gzip, io, socket, ssl, time, zlib
import urllib.error, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from .normalize import canonical_url, clean_text, sha, title_norm

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
RSS10 = "{http://purl.org/rss/1.0/}"

ISO_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


class FeedResult:
    def __init__(self, feed, items, status, error=None, etag=None, last_modified=None):
        self.feed = feed
        self.items = items
        self.status = status          # "ok" | "not_modified" | "error"
        self.error = error
        self.etag = etag
        self.last_modified = last_modified


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    cleaned = value.replace("Z", "+0000")
    if len(cleaned) > 6 and cleaned[-3] == ":" and (cleaned[-6] in "+-"):
        cleaned = cleaned[:-3] + cleaned[-2:]
    for fmt in ISO_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _text(el):
    if el is None:
        return ""
    return "".join(el.itertext()) if len(el) else (el.text or "")


def _first(node, *paths):
    for p in paths:
        found = node.find(p)
        if found is not None:
            val = _text(found).strip()
            if val:
                return val
    return ""


def _atom_link(node):
    best = ""
    for link in node.findall(ATOM + "link"):
        rel = link.get("rel", "alternate")
        href = link.get("href", "")
        if not href:
            continue
        if rel == "alternate":
            return href
        best = best or href
    return best


def _decompress(resp):
    raw = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        if "deflate" in enc:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        pass
    if raw[:2] == b"\x1f\x8b":
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except OSError:
            pass
    return raw


def http_get(url, cfg, etag=None, last_modified=None):
    timeout = cfg.path("fetch.timeout_seconds", 25)
    retries = cfg.path("fetch.retries", 2)
    headers = {
        "User-Agent": cfg.path("fetch.user_agent", "DailyBriefing/1.0"),
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return 200, _decompress(resp), resp.headers.get("ETag"), resp.headers.get("Last-Modified"), None
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return 304, b"", etag, last_modified, None
            last_err = "HTTP %s" % exc.code
            if exc.code in (400, 401, 403, 404, 410):
                break
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError) as exc:
            last_err = "%s: %s" % (type(exc).__name__, exc)
        except Exception as exc:  # noqa: BLE001 - a bad feed must never kill the run
            last_err = "%s: %s" % (type(exc).__name__, exc)
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return 0, b"", etag, last_modified, last_err


def parse_feed(data, feed):
    """Return a list of normalised item dicts from raw RSS/RDF/Atom bytes."""
    if not data:
        return []
    text = data.decode("utf-8", "replace").lstrip("﻿ \t\r\n")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Some publishers emit stray control characters; scrub and retry once.
        scrubbed = "".join(c for c in text if c >= " " or c in "\t\n\r")
        try:
            root = ET.fromstring(scrubbed)
        except ET.ParseError as exc:
            raise ValueError("XML parse error: %s" % exc)

    entries = (root.findall(".//item") or root.findall(".//" + RSS10 + "item")
               or root.findall(".//" + ATOM + "entry"))
    out = []
    for node in entries:
        title = clean_text(_first(node, "title", ATOM + "title", RSS10 + "title"))
        if not title:
            continue
        link = _first(node, "link", RSS10 + "link") or _atom_link(node)
        if not link:
            guid_el = node.find("guid")
            if guid_el is not None and (guid_el.text or "").startswith("http"):
                link = guid_el.text.strip()
        if not link:
            continue

        guid = _first(node, "guid", ATOM + "id", "{http://purl.org/dc/elements/1.1/}identifier") or link
        summary_raw = _first(node, "description", ATOM + "summary", RSS10 + "description",
                             CONTENT + "encoded", ATOM + "content")
        published_raw = _first(node, "pubDate", DC + "date", ATOM + "published",
                               ATOM + "updated", "published", "updated")
        author = clean_text(_first(node, DC + "creator", "author", ATOM + "author/" + ATOM + "name"))
        cats = [clean_text(_text(c)) for c in node.findall("category")]
        cats += [c.get("term", "") for c in node.findall(ATOM + "category")]

        published = parse_date(published_raw)
        canon = canonical_url(link)
        out.append({
            "title": title,
            "link": link,
            "canonical_url": canon,
            "guid": guid,
            "summary": clean_text(summary_raw),
            "published": published,
            "published_missing": published is None,
            "author": author,
            "categories": [c for c in cats if c],
            "source": feed["name"],
            "source_tier": feed.get("tier", 1.0),
            "domain": feed["domain"],
            "url_hash": sha(canon),
            "guid_hash": sha(guid),
            "title_norm": title_norm(title),
            "title_hash": sha(title_norm(title)),
        })
    return out


def fetch_one(feed, cfg, cache):
    cached = cache.get(feed["url"], {})
    try:
        status, body, etag, lastmod, err = http_get(
            feed["url"], cfg, cached.get("etag"), cached.get("last_modified"))
        if status == 304:
            return FeedResult(feed, [], "not_modified", etag=etag, last_modified=lastmod)
        if status != 200:
            return FeedResult(feed, [], "error", error=err or "unreachable")
        items = parse_feed(body, feed)
        if not items:
            return FeedResult(feed, [], "error", error="no parseable items", etag=etag, last_modified=lastmod)
        return FeedResult(feed, items, "ok", etag=etag, last_modified=lastmod)
    except Exception as exc:  # noqa: BLE001
        return FeedResult(feed, [], "error", error="%s: %s" % (type(exc).__name__, exc))


def fetch_all(cfg, cache, log=lambda *_: None):
    feeds = cfg.all_feeds
    workers = min(cfg.path("fetch.max_workers", 10), max(1, len(feeds)))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(lambda f: fetch_one(f, cfg, cache), feeds):
            results.append(res)
            log("  %-22s %-14s %s" % (
                res.feed["name"],
                res.status,
                ("%d items" % len(res.items)) if res.status == "ok" else (res.error or ""),
            ))
    return results
