"""End-to-end pipeline test with the network and Gmail stubbed out."""
import os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tests.fixtures as fx
from briefing import fetch as fetchmod, gmail, pipeline
from briefing.config import Config

SENT = []


def fake_fetch_all(cfg, cache, log=lambda *_: None):
    out = []
    for feed in cfg.all_feeds:
        payload = fx.FEEDS.get(feed["name"])
        if payload is None:
            out.append(fetchmod.FeedResult(feed, [], "error", error="no fixture"))
            continue
        items = fetchmod.parse_feed(payload, feed)
        out.append(fetchmod.FeedResult(feed, items, "ok", etag="e", last_modified="l"))
        log("  %-22s ok             %d items" % (feed["name"], len(items)))
    return out


def fake_send(cfg, to, subject, html, text):
    SENT.append({"to": to, "subject": subject, "html": html, "text": text})
    return "fake-msg-%d" % len(SENT)


def main():
    tmp = Path(tempfile.mkdtemp())
    fetchmod.fetch_all = fake_fetch_all
    pipeline.fetchmod.fetch_all = fake_fetch_all
    gmail.send = fake_send
    pipeline.gmail.send = fake_send

    # Isolate state, and supply a recipient. The suite must pass on a fresh
    # clone where config/settings.local.json does not exist yet, so it cannot
    # rely on the operator's real address.
    orig_init = Config.__init__
    def patched(self):
        orig_init(self)
        self.settings["recipient"] = "test@example.invalid"
        self.state_dir = tmp
        self.db_path = tmp / "test.db"
    Config.__init__ = patched

    print("=" * 78); print("RUN 1 - fresh database, everything is new"); print("=" * 78)
    r1 = pipeline.run(preview_path=str(tmp / "preview.html"))

    print()
    print("=" * 78); print("RUN 2 - immediately again, nothing should be new"); print("=" * 78)
    r2 = pipeline.run()

    print()
    print("=" * 78); print("ASSERTIONS"); print("=" * 78)
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print("  [%s] %s %s" % ("PASS" if cond else "FAIL", label, detail))

    check("run 1 sent an email", r1["status"] == "sent", "(status=%s)" % r1["status"])
    check("recipient came from config", SENT[0]["to"] == "test@example.invalid",
          "(%s)" % SENT[0]["to"])
    check("run 2 sent nothing", r2["status"] == "empty", "(status=%s)" % r2["status"])
    check("exactly one email total", len(SENT) == 1, "(%d sent)" % len(SENT))
    check("run 2 suppressed the whole set", r2["suppressed"] >= r1["cyber"] + r1["ai"],
          "(suppressed=%d)" % r2["suppressed"])

    html = SENT[0]["html"]
    check("both tables present",
          "Enterprise Cyber Security Intelligence Briefing" in html and "AI News Briefing" in html)
    for col in ("Priority", "Source", "Short Information", "Additional Details", "Link"):
        check("column '%s'" % col, col in html)

    # Cross-source merges: Ivanti (3 feeds), Stadler (2), OpenAI/Metriq (2), EU AI Act (2)
    check("Ivanti story appears once", html.count("Ivanti Connect Secure") <= 2,
          "(occurrences=%d)" % html.count("Ivanti Connect Secure"))
    check("Stadler merged", (html.count("Stadler") <= 2), "(occurrences=%d)" % html.count("Stadler"))
    check("Metriq merged", html.count("Metriq") <= 2, "(occurrences=%d)" % html.count("Metriq"))
    check("cross-source corroboration shown", "Also reported by" in html)
    check("sponsored item filtered out", "Best VPN deals" not in html)
    check("stale item filtered out", "Ancient" not in html and "ancient" not in html)
    check("emergency directive is P1", "P1" in html)
    check("subject line formed", "Cyber" in SENT[0]["subject"] and "AI" in SENT[0]["subject"],
          "(%s)" % SENT[0]["subject"])
    check("plain-text alternative built", len(SENT[0]["text"]) > 400)

    print()
    print("Run 1 selected: %d cyber, %d AI (merged=%s)" % (r1["cyber"], r1["ai"], r1.get("suppressed")))
    print("Subject: %s" % SENT[0]["subject"])

    shutil.copy(tmp / "preview.html", ROOT / "state" / "sample-briefing.html")
    print("\nSample email written to state/sample-briefing.html")
    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
