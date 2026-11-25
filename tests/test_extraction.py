import unittest
from datetime import datetime
from pathlib import Path

from ecowatch import extraction
from ecowatch.gazetteer import Gazetteer
from ecowatch.models import Epistemic, Report, ReportKind

GAZ = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"


def make_report(text, author="齐松", kind=ReportKind.PATROL, received=datetime(2026, 11, 17, 9, 0)):
    return Report(id="r1", kind=kind, author=author, text=text, received_at=received)


class ExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gaz = Gazetteer.load(GAZ)

    def extract(self, text, **kw):
        report = make_report(text, **kw)
        seq = iter(range(1, 100))
        return extraction.extract_statements(report, self.gaz, lambda: f"s{next(seq)}")

    def test_firsthand_and_not_seen(self):
        sts = self.extract("红桥下我站在上游往东看，见两只在盐地啄食，桥西未见。")
        self.assertEqual(len(sts), 2)
        see, notseen = sts
        self.assertIs(see.epistemic, Epistemic.FIRSTHAND)
        self.assertEqual(see.location.node_id, "yandi2")
        self.assertEqual(see.count, 2)
        self.assertIn("上游", see.location.qualifiers)
        self.assertIs(notseen.epistemic, Epistemic.NOT_SEEN)
        self.assertEqual(notseen.location.node_id, "xijiao")

    def test_hearsay_attribution_and_two_sightings(self):
        sts = self.extract(
            "昨儿桥脚又看见两只，是小齐跟群里说的。我今早自己去桥下也看见一只。",
            author="米乐",
            kind=ReportKind.FORWARD,
        )
        self.assertEqual(len(sts), 2)
        heard, own = sts
        self.assertIs(heard.epistemic, Epistemic.HEARSAY)
        self.assertEqual(heard.source_chain, ("齐松", "米乐"))  # 小齐→齐松
        self.assertEqual(heard.quoted_time_raw, "昨儿")
        self.assertEqual(heard.time_range.start.isoformat(), "2026-11-16")
        self.assertTrue(heard.is_repeat)
        self.assertIs(own.epistemic, Epistemic.FIRSTHAND)
        self.assertEqual(own.source_chain, ("米乐",))
        self.assertEqual(own.time_range.start.isoformat(), "2026-11-17")

    def test_opening_example_anaphora_and_quoted_week(self):
        sts = self.extract(
            "桥脚下面又看到了，上星期小周也在那边报过",
            author="老王",
            received=datetime(2026, 11, 25, 10, 0),
        )
        self.assertEqual(len(sts), 2)
        first, cited = sts
        self.assertIs(first.epistemic, Epistemic.FIRSTHAND)
        self.assertEqual(first.location.node_id, "changqiao")
        self.assertIs(cited.epistemic, Epistemic.HEARSAY)
        self.assertEqual(cited.source_chain, ("小周", "老王"))
        self.assertEqual(cited.quoted_time_raw, "上星期")
        self.assertEqual(
            (cited.time_range.start.isoformat(), cited.time_range.end.isoformat()),
            ("2026-11-16", "2026-11-22"),
        )
        self.assertEqual(cited.location.node_id, "changqiao")  # “那边”回指桥脚

    def test_guess(self):
        sts = self.extract("可能是从上游漂下来的")
        self.assertEqual(len(sts), 1)
        self.assertIs(sts[0].epistemic, Epistemic.GUESS)

    def test_two_distinct_sightings_in_one_paragraph(self):
        sts = self.extract(
            "周二在桥脚看到一只，周四又在草垛看到两只",
            received=datetime(2026, 11, 20, 9, 0),
        )
        self.assertEqual(len(sts), 2)
        self.assertEqual(sts[0].location.node_id, "changqiao")
        self.assertEqual(sts[0].time_range.start.isoformat(), "2026-11-17")
        self.assertEqual(sts[1].location.node_id, "caoduo")
        self.assertEqual(sts[1].time_range.start.isoformat(), "2026-11-19")

    def test_speaker_relative_direction(self):
        sts = self.extract("在对岸看到两只")
        self.assertEqual(len(sts), 1)
        self.assertTrue(sts[0].location.depends_on_speaker)


if __name__ == "__main__":
    unittest.main()
