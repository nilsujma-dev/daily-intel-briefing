"""Orchestration: fetch -> filter -> dedupe -> score -> render -> send."""
from __future__ import annotations
import traceback
from datetime import datetime, timedelta

from . import fetch as fetchmod
from . import curate as curatemod
from . import gmail
from .config import Config
from .dedupe import collapse_in_run, filter_recent, filter_unseen
from .render import render_html, render_text
from .score import rank_and_trim
from .store import Store, utcnow


def local_tz():
    return datetime.now().astimezone().tzinfo


def run(dry_run=False, force=False, since_hours=None, preview_path=None, log=print):
    cfg = Config()
    store = Store(cfg.db_path)
    run_id = store.start_run()
    started = utcnow()
    summary = {"run_id": run_id, "sent": False, "error": None}

    try:
        cfg.require_recipient()
        log("Run %s  |  %s" % (run_id, started.astimezone(local_tz()).strftime("%Y-%m-%d %H:%M:%S %Z")))
        log("\nFetching %d feeds…" % len(cfg.all_feeds))

        cache = store.feed_cache()
        results = fetchmod.fetch_all(cfg, cache, log=log)

        items, feed_status = [], []
        for res in results:
            store.update_feed_cache(res.feed["url"], res.etag, res.last_modified, res.status)
            feed_status.append({"name": res.feed["name"], "status": res.status,
                                "error": res.error, "count": len(res.items)})
            items.extend(res.items)

        healthy = sum(1 for f in feed_status if f["status"] in ("ok", "not_modified"))
        fetched = len(items)
        log("\nFetched %d items from %d/%d healthy feeds." % (fetched, healthy, len(feed_status)))
        if healthy == 0:
            raise RuntimeError("every feed failed - check network connectivity")

        last_run = store.last_successful_run()
        items, aged_out, cutoff = filter_recent(items, cfg, last_run, force=force, since_hours=since_hours)
        log("Recency window from %s → %d in window, %d too old."
            % (cutoff.astimezone(local_tz()).strftime("%d %b %H:%M"), len(items), aged_out))

        items, merged = collapse_in_run(items, cfg)
        log("Cross-source duplicates merged: %d → %d unique stories." % (merged, len(items)))

        items, suppressed = filter_unseen(items, store, cfg, force=force)
        log("Already-sent suppressed: %d → %d new stories." % (suppressed, len(items)))

        # Keyword scoring runs first regardless: it selects which stories are
        # worth an API call, and it is the ranking we fall back to.
        tables = rank_and_trim(items, cfg)
        tables, curation = curatemod.curate(tables, cfg, utcnow(), log=log)

        cyber_n, ai_n = len(tables["cyber"]), len(tables["ai"])
        log("\nSelected: %d cyber, %d AI." % (cyber_n, ai_n))

        window_h = since_hours if since_hours is not None else cfg.path("lookback_hours", 30)
        meta = {"generated_at": utcnow(), "run_id": run_id, "fetched": fetched,
                "merged": merged, "suppressed": suppressed,
                "window_label": "%dh" % int(round((utcnow() - cutoff).total_seconds() / 3600)),
                "feed_status": feed_status, "curation": curation}

        tz = local_tz()
        html = render_html(tables, meta, tz)
        text = render_text(tables, meta, tz)

        if preview_path:
            with open(preview_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            log("Preview written to %s" % preview_path)

        total = cyber_n + ai_n
        min_items = cfg.path("min_items_to_send", 1)
        if total < min_items and not cfg.path("send_empty_briefing", False):
            log("\nNothing new to report - no email sent.")
            store.finish_run(run_id, "empty", cyber_n, ai_n, suppressed, "no new items")
            summary.update(cyber=cyber_n, ai=ai_n, suppressed=suppressed, status="empty")
            return summary

        subject = cfg.path("subject_template").format(
            date=utcnow().astimezone(tz).strftime("%d %b %Y"), cyber_n=cyber_n, ai_n=ai_n)

        if dry_run:
            log("\n[dry-run] Would send to %s\n[dry-run] Subject: %s" % (cfg.require_recipient(), subject))
            log("[dry-run] State not updated - these items remain unsent.")
            store.finish_run(run_id, "dry-run", cyber_n, ai_n, suppressed, subject)
            summary.update(cyber=cyber_n, ai=ai_n, suppressed=suppressed, status="dry-run",
                           subject=subject, curation=curation)
            return summary

        msg_id = gmail.send(cfg, cfg.require_recipient(), subject, html, text)
        log("\nSent to %s (message %s)" % (cfg.require_recipient(), msg_id))

        # Only record as sent AFTER Gmail confirms, so a send failure never
        # silently swallows a day of news.
        sent_items = tables["cyber"] + tables["ai"]
        store.mark_sent(sent_items, run_id)
        pruned = store.prune(cfg.path("dedupe.retention_days", 45))
        store.finish_run(run_id, "sent", cyber_n, ai_n, suppressed,
                         "message %s | ranking=%s" % (msg_id, "claude" if curation.get("used") else "keyword"))
        log("Recorded %d items as sent; pruned %d expired rows." % (len(sent_items), pruned))
        summary.update(cyber=cyber_n, ai=ai_n, suppressed=suppressed, status="sent", sent=True,
                       subject=subject, message_id=msg_id, curation=curation)
        return summary

    except Exception as exc:  # noqa: BLE001
        detail = "%s: %s" % (type(exc).__name__, exc)
        log("\nFAILED - %s" % detail)
        log(traceback.format_exc())
        store.finish_run(run_id, "error", detail=detail)
        summary["error"] = detail
        summary["status"] = "error"
        raise
    finally:
        store.close()


def check_feeds(log=print):
    cfg = Config()
    log("Checking %d feeds…\n" % len(cfg.all_feeds))
    results = fetchmod.fetch_all(cfg, {}, log=lambda *_: None)
    bad = 0
    for res in sorted(results, key=lambda r: (r.feed["domain"], r.feed["name"])):
        mark = "ok " if res.status == "ok" else "ERR"
        if res.status != "ok":
            bad += 1
        newest = ""
        if res.items:
            dates = [i["published"] for i in res.items if i.get("published")]
            if dates:
                newest = "newest %s" % max(dates).astimezone(local_tz()).strftime("%d %b %H:%M")
        log("[%s] %-8s %-22s %3d items  %s%s" % (mark, res.feed["domain"], res.feed["name"],
                                                 len(res.items), newest,
                                                 ("  " + res.error) if res.error else ""))
    log("\n%d/%d feeds healthy." % (len(results) - bad, len(results)))
    return bad == 0


def show_stats(log=print):
    cfg = Config()
    store = Store(cfg.db_path)
    s = store.stats()
    log("Items remembered as sent: %d\n" % s["seen_total"])
    log("Recent runs:")
    if not s["runs"]:
        log("  (none yet)")
    for r in s["runs"]:
        log("  %s  %-8s cyber=%-3d ai=%-3d suppressed=%-3d  %s"
            % (r["started_at"][:19], r["status"], r["cyber_count"] or 0,
               r["ai_count"] or 0, r["suppressed"] or 0, r["run_id"]))
    log("\nFeed health:")
    for f in s["feeds"]:
        log("  %-8s fails=%-2d %s" % (f["last_status"], f["fail_streak"] or 0, f["feed_url"]))
    store.close()


def diagnose(log=print):
    """Why is the briefing empty? Shows the real age distribution per feed.

    Deliberately ignores the HTTP cache (no ETag/If-Modified-Since) so every feed
    returns its full contents rather than a 304, and reports what the recency
    window and the seen-store are each removing.
    """
    from .config import Config
    from .dedupe import collapse_in_run, filter_recent, filter_unseen
    from datetime import timedelta

    cfg = Config()
    store = Store(cfg.db_path)
    tz = local_tz()
    now = utcnow()

    log("Now: %s\n" % now.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z"))
    log("Fetching all feeds without cache validators…\n")
    results = fetchmod.fetch_all(cfg, {}, log=lambda *_: None)

    lookback = cfg.path("lookback_hours", 30)
    hard_max = cfg.path("hard_max_age_hours", 72)
    buckets = [6, 12, 24, 30, 48, 72, 168, 720]

    log("%-22s %5s %5s  %-17s %-17s %s" % ("FEED", "ITEMS", "NODATE", "NEWEST", "OLDEST", "AGE OF NEWEST"))
    log("-" * 104)

    all_items, totals = [], {b: 0 for b in buckets}
    for res in sorted(results, key=lambda r: (r.feed["domain"], r.feed["name"])):
        if res.status != "ok":
            log("%-22s  ERROR  %s" % (res.feed["name"], res.error))
            continue
        dated = [i["published"] for i in res.items if i.get("published") and not i.get("published_missing")]
        nodate = sum(1 for i in res.items if i.get("published_missing") or not i.get("published"))
        newest = max(dated) if dated else None
        oldest = min(dated) if dated else None
        age = ("%.1f h" % ((now - newest).total_seconds() / 3600.0)) if newest else "-"
        log("%-22s %5d %5d  %-17s %-17s %s" % (
            res.feed["name"], len(res.items), nodate,
            newest.astimezone(tz).strftime("%d %b %H:%M") if newest else "-",
            oldest.astimezone(tz).strftime("%d %b %H:%M") if oldest else "-",
            age))
        for d in dated:
            hours = (now - d).total_seconds() / 3600.0
            for b in buckets:
                if hours <= b:
                    totals[b] += 1
        all_items.extend(res.items)

    log("\nItems published within the last…")
    for b in buckets:
        marker = ""
        if b == lookback:
            marker = "   <-- current lookback_hours"
        elif b == hard_max:
            marker = "   <-- hard_max_age_hours"
        log("  %4dh : %4d%s" % (b, totals[b], marker))

    log("\nPipeline attrition on %d fetched items:" % len(all_items))
    last_run = store.last_successful_run()
    if last_run:
        log("  last successful run : %s" % last_run.astimezone(tz).strftime("%d %b %H:%M %Z"))
    kept, aged, cutoff = filter_recent(list(all_items), cfg, last_run)
    log("  cutoff              : %s" % cutoff.astimezone(tz).strftime("%d %b %H:%M %Z"))
    log("  in window           : %d   (dropped %d as too old)" % (len(kept), aged))
    kept, merged = collapse_in_run(kept, cfg)
    log("  after dedupe        : %d   (merged %d cross-source duplicates)" % (len(kept), merged))
    unseen, suppressed = filter_unseen(kept, store, cfg)
    log("  never sent before   : %d   (suppressed %d already emailed)" % (len(unseen), suppressed))

    stats = store.stats()
    log("\nSeen-store holds %d previously sent items." % stats["seen_total"])

    if not unseen:
        log("\nDIAGNOSIS")
        if totals[lookback] == 0 and totals[168] > 0:
            newest_overall = max((i["published"] for i in all_items
                                  if i.get("published") and not i.get("published_missing")),
                                 default=None)
            gap = (now - newest_overall).total_seconds() / 3600.0 if newest_overall else 0
            log("  Nothing was published inside the %dh window - the freshest item across all" % lookback)
            log("  feeds is %.0fh old. The feeds are lagging, not broken." % gap)
            log("  Raise lookback_hours in config/settings.json to at least %d, or run:" % int(gap + 12))
            log("      ./run.sh --since %d" % int(gap + 12))
        elif suppressed:
            log("  Everything in the window was already emailed - this is correct behaviour.")
            log("  To resend regardless:  ./run.sh --force --since 72")
        else:
            log("  Items exist but none survived. Inspect with:  ./run.sh --since 168 --dry-run")
    store.close()
    return 0



def mark_seen(since_hours=None, log=print):
    """Record everything currently in the window as already sent, without emailing.

    Recovery tool for a lost or replaced send-history: it re-synchronises the
    database with what has actually gone out, so the next scheduled run starts
    from a clean baseline instead of re-sending days of old news.

    Anything genuinely new that arrived inside the window is also marked, so use
    the narrowest window that covers the gap.
    """
    cfg = Config()
    store = Store(cfg.db_path)
    run_id = store.start_run()
    try:
        cache = store.feed_cache()
        results = fetchmod.fetch_all(cfg, cache, log=lambda *_: None)
        items = []
        for res in results:
            store.update_feed_cache(res.feed["url"], res.etag, res.last_modified, res.status)
            items.extend(res.items)
        log("Fetched %d items." % len(items))

        items, aged, cutoff = filter_recent(items, cfg, store.last_successful_run(),
                                            since_hours=since_hours)
        items, merged = collapse_in_run(items, cfg)
        fresh, suppressed = filter_unseen(items, store, cfg)

        log("Window from %s" % cutoff.astimezone(local_tz()).strftime("%d %b %H:%M %Z"))
        log("  %d in window, %d duplicates merged, %d already known."
            % (len(items), merged, suppressed))

        if not fresh:
            log("\nNothing to mark - the history already covers this window.")
            store.finish_run(run_id, "marked", 0, 0, suppressed, "no-op")
            return 0

        store.mark_sent(fresh, run_id)
        cyber = sum(1 for i in fresh if i["domain"] == "cyber")
        store.finish_run(run_id, "marked", cyber, len(fresh) - cyber, suppressed,
                         "marked %d as seen without sending" % len(fresh))
        log("\nMarked %d stories as already sent (%d cyber, %d AI). No email was sent."
            % (len(fresh), cyber, len(fresh) - cyber))
        log("The next scheduled run will only report what is genuinely newer.")
        return 0
    finally:
        store.close()
