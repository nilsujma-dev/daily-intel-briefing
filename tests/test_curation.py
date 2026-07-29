"""Claude curation: correct application of a good verdict, and graceful
degradation for every way the call can go wrong."""
import sys, urllib.error
from datetime import timedelta
from io import BytesIO
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from briefing import curate
from briefing.config import Config
from briefing.store import utcnow

RESULTS = []


def check(label, cond, detail=""):
    RESULTS.append(bool(cond))
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", label, detail))


def mk(title, domain, score, priority):
    now = utcnow()
    return {"title": title, "summary": "Original feed summary for %s." % title,
            "domain": domain, "source": "Src", "source_tier": 1.0,
            "published": now - timedelta(hours=3), "published_missing": False,
            "score": score, "priority": priority, "link": "https://x/%s" % title[:5],
            "also_in": []}


def tables():
    return {
        "cyber": [mk("Cyber A", "cyber", 40, "P1"), mk("Cyber B", "cyber", 30, "P1"),
                  mk("Cyber C", "cyber", 20, "P2"), mk("Cyber D", "cyber", 10, "P3")],
        "ai": [mk("AI A", "ai", 35, "P1"), mk("AI B", "ai", 15, "P2")],
    }


def with_stub(cfg, responder):
    curate._request = lambda c, k, b, t: responder(b)
    curate.api_key = lambda c: "test-key"


def run(cfg, responder):
    with_stub(cfg, responder)
    t = tables()
    return curate.curate(t, cfg, utcnow(), log=lambda *_: None)


def tool_response(cyber, ai):
    return {"content": [{"type": "tool_use", "name": "submit_briefing",
                         "input": {"cyber": cyber, "ai": ai}}],
            "usage": {"input_tokens": 100, "out_tokens": 50}}


def main():
    cfg = Config()
    cfg.settings["recipient"] = "test@example.invalid"   # works on a fresh clone
    D = "A sufficiently long details sentence about the story."

    # 1 - happy path: reordering, priority override and rewritten details
    out, st = run(cfg, lambda b: tool_response(
        [{"id": "c-4", "priority": "P1", "details": D + " D4"},
         {"id": "c-1", "priority": "P3", "details": D + " D1"},
         {"id": "c-2", "priority": "P2", "details": D + " D2"},
         {"id": "c-3", "priority": "P3", "details": D + " D3"}],
        [{"id": "a-2", "priority": "P1", "details": D + " A2"},
         {"id": "a-1", "priority": "P2", "details": D + " A1"}]))
    check("verdict applied", st["used"], "(ranked=%s)" % st.get("ranked"))
    check("Claude order respected", [i["title"] for i in out["cyber"]]
          == ["Cyber D", "Cyber A", "Cyber B", "Cyber C"])
    check("Claude priority overrides keyword", out["cyber"][0]["priority"] == "P1"
          and out["cyber"][1]["priority"] == "P3")
    check("details rewritten", out["cyber"][0]["claude_details"].endswith("D4"))
    check("original summary preserved underneath",
          out["cyber"][0]["summary"].startswith("Original feed summary"))
    check("scratch id cleaned up", all("_cid" not in i for i in out["cyber"] + out["ai"]))

    # 2 - omitted items must never vanish
    out, st = run(cfg, lambda b: tool_response(
        [{"id": "c-1", "priority": "P1", "details": D}], [{"id": "a-1", "priority": "P1", "details": D}]))
    check("omitted items restored", len(out["cyber"]) == 4 and len(out["ai"]) == 2,
          "(cyber=%d ai=%d restored=%d)" % (len(out["cyber"]), len(out["ai"]), st.get("restored", 0)))
    check("restored keep keyword priority", out["cyber"][1]["priority"] == "P1"
          and out["cyber"][3]["priority"] == "P3")

    # 3 - hallucinated ids are ignored, real ones still applied
    out, st = run(cfg, lambda b: tool_response(
        [{"id": "c-99", "priority": "P1", "details": D}, {"id": "nonsense", "priority": "P1", "details": D},
         {"id": "c-2", "priority": "P1", "details": D}], [{"id": "a-1", "priority": "P2", "details": D}]))
    check("unknown ids discarded", st["unknown_ids"] == 2, "(unknown=%d)" % st["unknown_ids"])
    check("no items lost to bad ids", len(out["cyber"]) == 4)

    # 4 - out-of-range priority falls back to the keyword value
    out, st = run(cfg, lambda b: tool_response(
        [{"id": "c-3", "priority": "URGENT!!", "details": D}], [{"id": "a-1", "priority": "P1", "details": D}]))
    check("invalid priority ignored", out["cyber"][0]["priority"] == "P2",
          "(got %s, keyword was P2)" % out["cyber"][0]["priority"])

    # 5 - P1 inflation is capped
    out, st = run(cfg, lambda b: tool_response(
        [{"id": "c-%d" % i, "priority": "P1", "details": D} for i in (1, 2, 3, 4)],
        [{"id": "a-1", "priority": "P1", "details": D}]))
    cap = min(cfg.path("scoring.max_p1_per_table", 4), max(1, -(-4 // 3)))
    n1 = sum(1 for i in out["cyber"] if i["priority"] == "P1")
    check("P1 quota enforced", n1 == cap, "(%d P1 of 4 items, budget %d)" % (n1, cap))
    check("top-ranked keeps its P1", out["cyber"][0]["priority"] == "P1")

    # 6 - too-short details keep the feed summary
    out, st = run(cfg, lambda b: tool_response(
        [{"id": "c-1", "priority": "P1", "details": "nope"}], [{"id": "a-1", "priority": "P1", "details": D}]))
    check("junk details rejected", "claude_details" not in out["cyber"][0])

    # 7 - duplicate ids collapse
    out, st = run(cfg, lambda b: tool_response(
        [{"id": "c-1", "priority": "P1", "details": D}, {"id": "c-1", "priority": "P3", "details": D}],
        [{"id": "a-1", "priority": "P1", "details": D}]))
    check("duplicate id ignored", [i["title"] for i in out["cyber"]].count("Cyber A") == 1)

    # 8 - no tool_use block at all
    out, st = run(cfg, lambda b: {"content": [{"type": "text", "text": "Sure! Here you go."}]})
    check("prose-only response degrades", not st["used"] and len(out["cyber"]) == 4,
          "(%s)" % st["error"])

    # 9 - verdict matching nothing
    out, st = run(cfg, lambda b: tool_response([{"id": "zzz", "priority": "P1", "details": D}], []))
    check("empty verdict degrades", not st["used"] and len(out["cyber"]) == 4)

    # 10 - auth failure: must not retry
    calls = []
    def unauthorized(_b):
        calls.append(1)
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, BytesIO(b'{"error":"bad key"}'))
    out, st = run(cfg, unauthorized)
    check("401 degrades without retry", not st["used"] and len(calls) == 1, "(calls=%d)" % len(calls))
    check("all items survive auth failure", len(out["cyber"]) == 4 and len(out["ai"]) == 2)

    # 11 - transient failure: retried, then degrades
    calls2 = []
    def flaky(_b):
        calls2.append(1)
        raise urllib.error.URLError("connection timed out")
    out, st = run(cfg, flaky)
    check("transient error retried", len(calls2) == cfg.path("curation.retries", 1) + 1,
          "(attempts=%d)" % len(calls2))
    check("degrades after retries", not st["used"] and len(out["cyber"]) == 4)

    # 12 - malformed JSON body
    out, st = run(cfg, lambda b: (_ for _ in ()).throw(ValueError("Expecting value: line 1")))
    check("malformed response degrades", not st["used"] and len(out["cyber"]) == 4)

    # 13 - unexpected exception type must still not break delivery
    out, st = run(cfg, lambda b: (_ for _ in ()).throw(RuntimeError("something odd")))
    check("unexpected error contained", not st["used"] and len(out["cyber"]) == 4)

    # 14 - the request we actually send is well formed
    captured = {}
    def capture(b):
        captured.update(b)
        return tool_response([{"id": "c-1", "priority": "P1", "details": D}],
                             [{"id": "a-1", "priority": "P1", "details": D}])
    run(cfg, capture)
    check("model from settings", captured["model"] == cfg.path("curation.model"),
          "(%s)" % captured["model"])
    check("tool use forced", captured["tool_choice"] == {"type": "tool", "name": "submit_briefing"})
    check("P1 budget stated in system prompt",
          str(cfg.path("scoring.max_p1_per_table", 4)) in captured["system"])
    check("candidates carried in the message",
          "Cyber A" in captured["messages"][0]["content"] and "AI A" in captured["messages"][0]["content"])
    check("feed summaries included", "Original feed summary" in captured["messages"][0]["content"])

    total, passed = len(RESULTS), sum(RESULTS)
    print("\ncuration: %d/%d checks pass" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
