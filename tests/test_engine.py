import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import seed  # noqa: E402


def build_kb(with_guidelines=True):
    rows, n = [], [0]

    def add(kind, title, body, extra):
        n[0] += 1
        rows.append({"id": n[0], "kind": kind, "title": title, "body": body, "extra": json.dumps(extra)})

    if with_guidelines:
        for t, topic, b in seed.GUIDELINES:
            add("guideline", t, b, {"topic": topic})
    for k, s, kw, v in seed.FACTS:
        add("fact", k, s, {"keywords": kw, "value": v})
    for p, b, note in seed.TERMS:
        add("term", p, note, {"banned": b})
    for p, note in seed.CLAIMS:
        add("claim", p, note, {})
    for name, h in seed.ASSETS:
        add("asset", name, h, {"type": "color"})
    return engine.load_kb(rows)


KB = build_kb()


def run(text, channel="social"):
    return engine.review(text, channel, "", KB)[0]


class DeterministicChecks(unittest.TestCase):
    def test_banned_term_suggests_preferred_and_cites_term(self):
        f = [x for x in run("Our new flask is here.") if x["category"] == "terminology"][0]
        self.assertEqual(f["suggestion"], "bottle")
        self.assertEqual(f["source_kind"], "term")

    def test_term_case_is_preserved(self):
        f = [x for x in run("Flask launch today.") if x["category"] == "terminology"][0]
        self.assertEqual(f["suggestion"], "Bottle")

    def test_guaranteed_claim_keeps_intent(self):
        f = [x for x in run("It is guaranteed to keep drinks cold.") if x["category"] == "claims"][0]
        self.assertEqual(f["suggestion"], "designed")
        self.assertEqual(f["source_kind"], "guideline")
        self.assertIn("guarantee", f["source_quote"].lower())

    def test_wrong_fact_is_flagged_with_approved_value(self):
        f = [x for x in run("We ship to 25 countries.") if x["category"] == "facts"][0]
        self.assertEqual((f["excerpt"], f["suggestion"]), ("25", "32"))
        self.assertEqual(f["source_kind"], "fact")

    def test_correct_fact_is_not_flagged(self):
        self.assertEqual([x for x in run("We ship to 32 countries.") if x["category"] == "facts"], [])

    def test_approved_claim_is_not_flagged_but_wrong_number_beside_it_is(self):
        self.assertEqual(run("Enjoy free shipping on orders over $50."), [])
        out = run("Every bottle contains 100% recycled stainless steel and comes with a 5-year warranty.")
        self.assertEqual([(x["excerpt"], x["suggestion"]) for x in out], [("100", "90")])

    def test_off_palette_colour_suggests_nearest(self):
        f = [x for x in run("Use #FF5533 for the button.") if x["category"] == "visual"][0]
        self.assertEqual(f["suggestion"], "#E86A4A")

    def test_accessibility_alt_text_and_link_text(self):
        cats = {(x["category"], x["severity"]) for x in run("![](a.png) and [click here](http://x.example)")}
        self.assertIn(("accessibility", "high"), cats)
        self.assertIn(("accessibility", "medium"), cats)

    def test_tone_exclamation_caps_and_slang(self):
        out = run("AMAZING news, we are gonna launch!!! Great!")
        texts = {x["excerpt"] for x in out}
        self.assertTrue({"AMAZING", "gonna", "!!!"} <= texts)

    def test_hype_words_only_on_formal_channels(self):
        self.assertEqual([x for x in run("This is epic.", "social") if x["excerpt"] == "epic"], [])
        self.assertEqual(len([x for x in run("This is epic.", "press_release") if x["excerpt"] == "epic"]), 1)

    def test_clean_sample_has_no_findings(self):
        self.assertEqual(run(seed.SAMPLES["clean"][3], "email"), [])

    def test_findings_do_not_overlap(self):
        out = run(seed.SAMPLES["social"][3])
        for a, b in zip(out, out[1:]):
            self.assertLessEqual(a["end"], b["start"])


class ExplainabilityRules(unittest.TestCase):
    def test_no_guideline_means_no_source_and_low_confidence(self):
        kb = build_kb(with_guidelines=False)
        f = [x for x in engine.review("Guaranteed results!", "social", "", kb)[0] if x["category"] == "claims"][0]
        self.assertIsNone(f["source_kind"])
        self.assertLessEqual(f["confidence"], 0.4)


class Suggestions(unittest.TestCase):
    def test_apply_edits_non_overlapping_and_overlapping(self):
        text = "aaa bbb ccc"
        new, n = engine.apply_edits(text, [(0, 3, "X"), (8, 11, "Z")])
        self.assertEqual((new, n), ("X bbb Z", 2))
        new, n = engine.apply_edits(text, [(0, 7, "Q"), (4, 8, "R")])
        self.assertEqual(n, 1)


class AiLayer(unittest.TestCase):
    TEXT = "This bottle is simply the finest in the world. Contact me at jo@example.com or +1 555 123 4567."

    def chunks(self):
        return engine.build_chunks(KB, self.TEXT)

    def test_redaction(self):
        r = engine.redact(self.TEXT)
        self.assertNotIn("jo@example.com", r)
        self.assertNotIn("555", r)

    def test_verified_source_is_kept(self):
        ch = self.chunks()
        cid, c = next((k, v) for k, v in ch.items() if "superlatives" in v["text"])
        items = [{"quote": "the finest in the world", "category": "claims", "severity": "medium", "explanation": "x",
                  "source_id": cid, "source_quote": "Do not use unsupported superlatives", "suggestion": "well made", "confidence": 0.9}]
        f = engine.verify_ai_findings(items, self.TEXT, ch, [])[0]
        self.assertEqual(f["source_kind"], "guideline")
        self.assertEqual(f["detected_by"], "ai")
        self.assertEqual(f["suggestion"], "well made")

    def test_invented_source_quote_is_downgraded(self):
        ch = self.chunks()
        cid = next(iter(ch))
        items = [{"quote": "the finest in the world", "category": "claims", "explanation": "x", "source_id": cid,
                  "source_quote": "Rule 99: never say finest", "confidence": 0.95}]
        f = engine.verify_ai_findings(items, self.TEXT, ch, [])[0]
        self.assertIsNone(f["source_kind"])
        self.assertLessEqual(f["confidence"], 0.4)

    def test_quote_not_in_content_is_dropped(self):
        items = [{"quote": "text that is not there", "category": "tone", "explanation": "x"}]
        self.assertEqual(engine.verify_ai_findings(items, self.TEXT, self.chunks(), []), [])

    def test_overlap_with_rule_finding_is_dropped(self):
        items = [{"quote": "the finest", "category": "tone", "explanation": "x"}]
        start = self.TEXT.find("the finest")
        self.assertEqual(engine.verify_ai_findings(items, self.TEXT, self.chunks(), [(start, start + 5)]), [])

    def test_call_ai_parses_fenced_json_and_handles_errors(self):
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"content": [{"type": "text", "text": "```json\n[{\"quote\": \"a\"}]\n```"}]}
        with mock.patch("requests.post", return_value=ok):
            items, status = engine.call_ai("t", "", "social", {}, [], "k", "m")
        self.assertEqual((items, status), ([{"quote": "a"}], "used"))
        with mock.patch("requests.post", return_value=mock.Mock(status_code=529)):
            self.assertEqual(engine.call_ai("t", "", "social", {}, [], "k", "m"), ([], "unavailable (HTTP 529)"))
        with mock.patch("requests.post", side_effect=TimeoutError()):
            self.assertEqual(engine.call_ai("t", "", "social", {}, [], "k", "m"), ([], "unavailable (TimeoutError)"))

    def test_review_never_sends_unredacted_pii(self):
        sent = {}

        def fake(redacted, intent, channel, chunks, existing, key, model, timeout=30):
            sent["text"] = redacted
            return [], "used"

        with mock.patch.object(engine, "call_ai", fake):
            engine.review(self.TEXT, "social", "", KB, {"api_key": "k", "model": "m"})
        self.assertNotIn("jo@example.com", sent["text"])


if __name__ == "__main__":
    unittest.main()
