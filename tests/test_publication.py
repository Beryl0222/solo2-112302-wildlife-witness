import unittest
from datetime import datetime
from pathlib import Path

from ecowatch.gazetteer import Gazetteer
from ecowatch.models import ReportKind
from ecowatch.station import Station

GAZ = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"

B1 = "北汊有夜间活动记录。走过东脚草垛和石湾之外，剩下的就是上一期所称的那个观察点。"
B2 = "有人在红桥东脚的第三处发现活动迹象，之后会复核。"


def D(day, hour=9):
    return datetime(2026, 11, day, hour)


def make_station():
    return Station(Gazetteer.load(GAZ))


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.st = make_station()

    def test_alias_counts_as_identifying(self):
        """地标别称计入：红桥=长桥。"""
        clues = self.st.publisher.clues_from_text("红桥东脚的第三处")
        self.assertIn("changqiao", clues.within)
        self.assertIn("dongjiao", clues.within)
        self.assertTrue(clues.identifying)

    def test_combination_with_old_bulletin_pinpoints(self):
        """个别句子本身安全，与已发句子合用却唯一指向受保护点。"""
        self.st.ingest_published(B1, "b1")
        draft = self.st.publisher.make_draft(B2)
        site = self.st.publisher.pinpointed(extra=[draft])
        self.assertIsNotNone(site)
        self.assertEqual(site.id, "yandi2")

    def test_draft_rewritten_keeps_trend_text(self):
        """改写有问题的位置线索，保留与一般趋势有关的文字。"""
        draft = self.st.publisher.make_draft(B2)
        self.assertIsNone(self.st.publisher.pinpointed(extra=[draft]))  # 本身安全
        ok, notes = self.st.publisher.review_draft(draft)
        self.assertTrue(ok)
        self.assertTrue(notes)
        self.assertIn("北汊", draft.text)
        for bad in ("红桥", "东脚", "第三处"):
            self.assertNotIn(bad, draft.text)
        self.assertIn("发现活动迹象", draft.text)
        self.assertIn("复核", draft.text)

    def test_b1_exposed_withdrawn_and_corrected(self):
        """原图暴露：下架并发更正；下架后不再唯一指向。"""
        b = self.st.ingest_published(B1, "b1")
        alerts = self.st.review_publications()
        self.assertTrue(any(a["bulletin"] == b.id for a in alerts))
        self.assertEqual(alerts[0]["site"], "二号盐地")
        self.st.withdraw_bulletin(b.id, "唯一指向繁育点", correction="上一期观察点描述作废")
        self.assertIsNone(self.st.publisher.pinpointed())
        self.assertEqual(self.st.publisher.bulletins[b.id].status, "已下架")
        self.assertIn("作废", self.st.publisher.bulletins[b.id].corrections[0])

    def test_protected_name_scan(self):
        v = self.st.publisher.text_violations("在盐地附近看到")
        self.assertTrue(any("受保护" in x for x in v))

    def test_summary_uses_segment_granularity(self):
        self.st.ingest(ReportKind.PATROL, "齐松", "见两只在盐地啄食。", received_at=D(16, 19))
        drafts = self.st.draft_summary("2026年11月")
        self.assertEqual(len(drafts), 1)
        s, _notes = drafts[0]
        self.assertIn("北汊", s.text)
        self.assertNotIn("盐地", s.text)
        self.assertNotIn("东脚", s.text)
        self.assertIn("受保护", s.note)


if __name__ == "__main__":
    unittest.main()
