import unittest
from datetime import datetime
from pathlib import Path

from ecowatch import dispatch
from ecowatch.gazetteer import Gazetteer
from ecowatch.models import ReportKind
from ecowatch.station import Station

GAZ = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"


def D(day, hour=9):
    return datetime(2026, 11, day, hour)


def make_station():
    return Station(Gazetteer.load(GAZ))


class DispatchTest(unittest.TestCase):
    def test_hearsay_only_gets_segment_scope(self):
        """不能从转述的断言直接下达精确地点的任务。"""
        st = make_station()
        st.ingest(ReportKind.FORWARD, "米乐", "听说桥脚那边看到两只。", received_at=D(17))
        cm = next(iter(st.commissions.values()))
        self.assertFalse(cm.precise)
        self.assertEqual(cm.scope_node, "beicha")
        self.assertEqual(cm.basis, "转述")
        with self.assertRaises(PermissionError):
            st.narrow_commission(cm.id, "changqiao")

    def test_permission_enforced_on_decide(self):
        """成行必须指名对范围已有巡护权限的人。"""
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "见两只在盐地啄食。", received_at=D(16, 19))
        cm = next(iter(st.commissions.values()))
        # 受保护点向上收到非保护节点，值班员看不到精确位置
        self.assertEqual(cm.scope_node, "dongjiao")
        self.assertTrue(cm.precise)
        with self.assertRaises(PermissionError):
            st.decide_commission(cm.id, approve=True, assignee="米乐")  # 米乐只有西脚权限
        st.decide_commission(cm.id, approve=True, assignee="齐松")
        self.assertEqual(cm.status, dispatch.ACTIVE)

    def test_guess_gets_no_commission(self):
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "可能是从上游漂下来的。", received_at=D(17))
        self.assertEqual(len(st.commissions), 0)
        row = st.duty_board()[0]
        self.assertIn("暂缓", row["建议"])

    def test_duty_board_shows_scope_and_eligible(self):
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "见两只在盐地啄食。", received_at=D(16, 19))
        row = st.duty_board()[0]
        self.assertEqual(row["有巡护权限"], ["齐松"])
        self.assertIn("东脚", row["建议范围"])
        self.assertTrue(any("受保护" in w for w in row["警告"]))


if __name__ == "__main__":
    unittest.main()
