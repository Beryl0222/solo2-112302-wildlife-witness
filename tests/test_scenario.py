import json
import unittest
from pathlib import Path

from ecowatch.corpus_adapter import ingest_record
from ecowatch.gazetteer import Gazetteer
from ecowatch.models import EventStatus
from ecowatch.station import Station

ROOT = Path(__file__).resolve().parent.parent


class CorpusScenarioTest(unittest.TestCase):
    """用 corpus 记录走完整流程：接收 → 归并 → 回访 → 核派 → 外发。"""

    @classmethod
    def setUpClass(cls):
        corpus = json.loads((ROOT / "data" / "corpus.json").read_text(encoding="utf-8"))
        cls.records = {r["id"]: r for r in corpus["records"]}

    def setUp(self):
        self.st = Station(Gazetteer.load(ROOT / "data" / "gazetteer.json"))

    def test_full_flow(self):
        st = self.st
        ingest_record(st, self.records["n1"])
        ingest_record(st, self.records["n2"])

        # n2 的群消息与 n1 同源：并入但不计独立印证
        ev1 = next(e for e in st.events.values() if len(e.statement_ids) == 2)
        self.assertEqual(ev1.independent_sources, 1)
        # 米乐自己的目击单独成事件，与 ev1 挂待澄清候选
        self.assertTrue(any(l.status == "待澄清" for l in st.links.values()))

        # 回访 n3：只确认米乐自己那条，候选澄清为不同事件
        ingest_record(st, self.records["n3"])
        self.assertTrue(all(l.status == "已确认不同" for l in st.links.values()))
        self.assertIs(ev1.status, EventStatus.OPEN)
        confirmed = [e for e in st.events.values() if e.status is EventStatus.CONFIRMED]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].node_id, "xijiao")

        # 核派（v1）：齐松有东脚权限，成行
        cm = st._commission_of(ev1.id)
        st.decide_commission(cm.id, approve=True, assignee="齐松")
        self.assertEqual(cm.status, "已成行")

        # 外发：b1 暴露 → 下架更正；b2 改写后发布
        b1 = ingest_record(st, self.records["b1"])
        alerts = st.review_publications()
        self.assertTrue(any(a["bulletin"] == b1.id for a in alerts))
        st.withdraw_bulletin(b1.id, "唯一指向繁育点", correction="观察点描述作废")
        draft = ingest_record(st, self.records["b2"])
        ok, _notes = st.review_material(draft.id)
        self.assertTrue(ok)
        st.publish_material([draft.id], "月度线索地图·增补")

        # 月度摘要按区段粒度
        drafts = st.draft_summary("2026年11月")
        self.assertTrue(drafts)
        self.assertTrue(all("北汊" in s.text for s, _ in drafts))

        # 核查人员仍见原始定位依据
        view = st.verify_event(ev1.id)
        self.assertTrue(view["受保护"])
        self.assertEqual(len(view["陈述"]), 2)

        # 接收审计留痕
        self.assertEqual(len(st.audit), 3)  # n1/n2/n3 三条报告


if __name__ == "__main__":
    unittest.main()
