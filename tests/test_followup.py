import unittest
from datetime import datetime
from pathlib import Path

from ecowatch import dispatch
from ecowatch.gazetteer import Gazetteer
from ecowatch.models import Epistemic, EventStatus, ReportKind
from ecowatch.station import Station

GAZ = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"


def D(day, hour=9):
    return datetime(2026, 11, day, hour)


def make_station():
    return Station(Gazetteer.load(GAZ))


def event_of(st, statement_id):
    return next(e for e in st.events.values() if statement_id in e.statement_ids)


class FollowupTest(unittest.TestCase):
    def test_followup_confirms_only_one(self):
        """一段里两个目击，回访只确认其中一个；另一个事件不受影响。"""
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "红桥下我站在上游往东看，见两只在盐地啄食，桥西未见。", received_at=D(16, 19))
        st.ingest(ReportKind.FORWARD, "米乐", "昨儿桥脚又看见两只，是小齐跟群里说的。我今早自己去桥下也看见一只。", received_at=D(17))
        st.ingest(ReportKind.FOLLOWUP, "米乐", "今早那只说的是西脚。旧桥头下了堤先向下游，和上游那两只不是一处。", received_at=D(18))

        own = next(
            s for s in st.statements.values()
            if s.speaker == "米乐" and s.epistemic is Epistemic.FIRSTHAND
        )
        self.assertEqual(own.verdict, "confirmed")
        self.assertEqual(own.location.node_id, "xijiao")
        ev_own = event_of(st, own.id)
        self.assertIs(ev_own.status, EventStatus.CONFIRMED)
        self.assertEqual(ev_own.node_id, "xijiao")

        # 齐松那条（含群消息回声）未被确认，仍是待核查
        ev1 = next(e for e in st.events.values() if e.id != ev_own.id and len(e.statement_ids) == 2)
        self.assertIs(ev1.status, EventStatus.OPEN)

        # 候选关系澄清为“不同事件”
        self.assertTrue(st.links)
        self.assertTrue(all(l.status == "已确认不同" for l in st.links.values()))

    def test_split_shared_sighting(self):
        """回访把一次共享目击拆成两个事件：委托需复核、外发复核暴露、核查仍见原始依据。"""
        st = make_station()
        st.ingest(ReportKind.PATROL, "齐松", "周一在草垛看到一只。", received_at=D(17))
        st.ingest(ReportKind.PATROL, "米乐", "周一我也在草垛看到一只。", received_at=D(17))
        ev = next(e for e in st.events.values() if len(e.statement_ids) == 2)
        cm = st._commission_of(ev.id)
        self.assertIsNotNone(cm)

        # 已发简报仍在架（唯一指向繁育点），拆分触发外发复核
        st.ingest_published("走过东脚草垛和石湾之外，剩下的就是上一期所称的那个观察点。", "b1")

        s1, s2 = ev.statement_ids
        result = st.ingest(
            ReportKind.FOLLOWUP, "齐松", "核实是两次，不是一处。",
            received_at=D(18),
            claims=[{"action": "split", "event": ev.id, "groups": [[s1], [s2]], "note": "两次目击"}],
        )

        self.assertIs(ev.status, EventStatus.SPLIT)
        self.assertEqual(len(ev.superseded_by), 2)
        # 值班员能看出哪张委托应改范围或分开
        self.assertEqual(cm.status, dispatch.NEEDS_REVIEW)
        # 外发员收到暴露告警
        self.assertTrue(result.alerts)
        self.assertEqual(result.alerts[0]["site"], "二号盐地")
        # 核查人员继续看到原始定位依据
        view = st.verify_event(ev.id)
        self.assertEqual(len(view["陈述"]), 2)
        self.assertTrue(all(s["原文"] for s in view["陈述"]))


if __name__ == "__main__":
    unittest.main()
