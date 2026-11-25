import unittest
from datetime import datetime
from pathlib import Path

from ecowatch.gazetteer import Gazetteer
from ecowatch.models import ReportKind
from ecowatch.station import Station

GAZ = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"


def D(day, hour=9):
    return datetime(2026, 11, day, hour)


def make_station():
    return Station(Gazetteer.load(GAZ))


class ResolutionTest(unittest.TestCase):
    def test_echo_does_not_corroborate(self):
        """多个署名不代表多个独立来源：群消息转述与原件同源。"""
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "见两只在盐地啄食。", received_at=D(16, 19))
        st.ingest(ReportKind.FORWARD, "米乐", "昨儿在盐地看见两只，是小齐跟群里说的。", received_at=D(17))
        ev = next(e for e in st.events.values() if len(e.statement_ids) == 2)
        self.assertEqual(ev.independent_sources, 1)
        row = next(r for r in st.duty_board() if r["event_id"] == ev.id)
        self.assertFalse(row["相互印证"])

    def test_independent_chains_corroborate(self):
        """来源链能分开、时段地点一致 → 相互印证。"""
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "见两只在盐地啄食。", received_at=D(16, 19))
        st.ingest(ReportKind.PATROL, "米乐", "周一我也在盐地看到一只。", received_at=D(17))
        ev = next(e for e in st.events.values() if len(e.statement_ids) == 2)
        self.assertEqual(ev.independent_sources, 2)
        row = next(r for r in st.duty_board() if r["event_id"] == ev.id)
        self.assertTrue(row["相互印证"])

    def test_adjacent_time_becomes_candidate(self):
        """时段相邻的相似报告可能是上一次观察，不合并。"""
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "见两只在盐地啄食。", received_at=D(16, 19))
        st.ingest(ReportKind.PATROL, "米乐", "周二在盐地看到一只。", received_at=D(18))
        self.assertEqual(len([e for e in st.events.values()]), 2)
        link = next(iter(st.links.values()))
        self.assertEqual(link.status, "待澄清")
        self.assertIn("时段相邻", link.reason)

    def test_opposite_bank_not_merged(self):
        """“对岸”依赖说话人位置，只挂候选关系。"""
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "见两只在盐地啄食。", received_at=D(16, 19))
        st.ingest(ReportKind.PATROL, "米乐", "在对岸看到两只。", received_at=D(16, 20))
        self.assertEqual(len(st.events), 2)
        link = next(iter(st.links.values()))
        self.assertEqual(link.status, "待澄清")
        self.assertIn("说话人位置", link.reason)

    def test_two_sightings_stay_separate(self):
        """一段里两个互不相同的目击拆成两个事件。"""
        st = make_station()
        result = st.ingest(
            ReportKind.PATROL, "齐松", "周二在桥脚看到一只，周四又在草垛看到两只。", received_at=D(20)
        )
        self.assertEqual(len(result.new_events), 2)
        nodes = {e.node_id for e in result.new_events}
        self.assertEqual(nodes, {"changqiao", "caoduo"})


if __name__ == "__main__":
    unittest.main()
