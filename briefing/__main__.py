"""CLI entry point:  python3 -m briefing [options]"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime
from pathlib import Path

from .config import Config


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="briefing",
        description="Daily AI + Cyber Security intelligence briefing to email.")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the briefing and print it, but send nothing and record nothing")
    ap.add_argument("--preview", nargs="?", const="auto", default=None, metavar="PATH",
                    help="write the HTML email to a file for inspection")
    ap.add_argument("--force", action="store_true",
                    help="ignore the already-sent history (re-include old stories)")
    ap.add_argument("--since", type=float, metavar="HOURS",
                    help="override the lookback window, e.g. --since 72")
    ap.add_argument("--check-feeds", action="store_true", help="test every feed and exit")
    ap.add_argument("--check-claude", action="store_true",
                    help="verify the Anthropic API key actually works")
    ap.add_argument("--authorize", action="store_true", help="run the one-time Google sign-in")
    ap.add_argument("--stats", action="store_true", help="show run history and feed health")
    ap.add_argument("--diagnose", action="store_true",
                    help="explain exactly why a briefing came out empty")
    ap.add_argument("--mark-seen", action="store_true",
                    help="record the current window as already sent, without emailing "
                         "(recovery after a lost send-history)")
    ap.add_argument("--test-email", action="store_true",
                    help="send a short test message to confirm delivery works")
    ap.add_argument("--quiet", action="store_true", help="only print warnings and errors")
    args = ap.parse_args(argv)

    # Lets a caller force a non-sending run without altering the unit file.
    # deploy.sh uses this to execute the real systemd unit as a smoke test:
    # the sandbox gets exercised for real, but no email goes out.
    if os.environ.get("BRIEFING_DRY_RUN", "").strip() in ("1", "true", "yes"):
        args.dry_run = True

    from . import pipeline

    log = (lambda *a: None) if args.quiet else print

    if args.authorize:
        from . import gmail
        gmail.authorize(Config(), log=print)
        return 0
    if args.test_email:
        from . import gmail
        gmail.send_test(Config(), log=print)
        return 0
    if args.check_claude:
        from . import curate
        return curate.check(Config(), log=print)
    if args.check_feeds:
        return 0 if pipeline.check_feeds(log=print) else 1
    if args.stats:
        pipeline.show_stats(log=print)
        return 0
    if args.diagnose:
        return pipeline.diagnose(log=print)
    if args.mark_seen:
        return pipeline.mark_seen(since_hours=args.since, log=print)

    preview_path = args.preview
    if preview_path == "auto":
        cfg = Config()
        out = cfg.state_dir / "preview"
        out.mkdir(exist_ok=True)
        preview_path = str(out / ("briefing-%s.html" % datetime.now().strftime("%Y%m%d-%H%M%S")))

    try:
        result = pipeline.run(dry_run=args.dry_run, force=args.force,
                              since_hours=args.since, preview_path=preview_path, log=log)
    except Exception:  # noqa: BLE001 - already logged with a traceback
        return 2
    return 0 if result.get("status") in ("sent", "empty", "dry-run") else 1


if __name__ == "__main__":
    sys.exit(main())
