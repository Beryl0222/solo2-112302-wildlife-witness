"""生态志愿站门面：持续接收、核派巡查、外发摘要。

角色视图：
- 值班员 duty_board：线索、印证情况、候选关系、建议范围与有权限的人；
  看不到受保护点精确位置，也不能从转述断言下达精确点位任务。
- 核查人员 verify_event：原始定位依据（原文、来源链、解析位置）完整可见。
- 外发员 publisher_board：草稿、简报、暴露告警；下架与更正由外发员决定。
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime

from . import dispatch, extraction, followup, resolution
from .gazetteer import Gazetteer
from .models import (
    CandidateLink,
    Epistemic,
    Event,
    EventStatus,
    Report,
    ReportKind,
    Statement,
)
from .publication import Publisher, Sentence


@dataclass
class IngestResult:
    report: Report
    statements: list[Statement] = field(default_factory=list)
    new_events: list[Event] = field(default_factory=list)
    updated_events: list[tuple[Event, str, str]] = field(default_factory=list)
    new_links: list[CandidateLink] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)


class Station:
    def __init__(self, gaz: Gazetteer, clock=None):
        self.gaz = gaz
        self.clock = clock or datetime.now
        self.reports: dict[str, Report] = {}
        self.statements: dict[str, Statement] = {}
        self.events: dict[str, Event] = {}
        self.links: dict[str, CandidateLink] = {}
        self.commissions: dict[str, dispatch.Commission] = {}
        self.publisher = Publisher(gaz)
        self._seq = itertools.count(1)
        self.audit: list[dict] = []

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._seq)}"

    # ---- 持续接收 ----

    def ingest(
        self,
        kind: ReportKind,
        author: str,
        text: str,
        received_at: datetime | None = None,
        claims: list[dict] | None = None,
        channel: str = "语音转写",
    ) -> IngestResult:
        received_at = received_at or self.clock()
        author = self.gaz.canonical_person(author)
        report = Report(
            id=self._id("rep"),
            kind=kind,
            author=author,
            text=text,
            received_at=received_at,
            channel=channel,
            claims=list(claims or []),
        )
        self.reports[report.id] = report
        self.audit.append(
            {
                "id": report.id,
                "kind": kind.value,
                "author": author,
                "text": text,
                "received_at": received_at.isoformat(),
                "channel": channel,
            }
        )
        result = IngestResult(report=report)

        if kind is ReportKind.FOLLOWUP:
            sts = extraction.extract_statements(report, self.gaz, lambda: self._id("st"))
            for s in sts:
                self.statements[s.id] = s
            result.statements = sts
            use_claims = list(claims) if claims else followup.parse_claims(
                report, self.statements, self.events, self.links, self.gaz
            )
            result.effects = followup.apply_claims(self, use_claims)
            # 回访可能改变事件结构（拆分/确认），外发员需单独判断原图是否暴露
            result.alerts = self.publisher.review_live(trigger=f"回访 {report.id}")
            return result

        for s in extraction.extract_statements(report, self.gaz, lambda: self._id("st")):
            self.statements[s.id] = s
            self._resolve_statement(s, result)
            result.statements.append(s)
        return result

    def _resolve_statement(self, s: Statement, result: IngestResult) -> None:
        merges: list[tuple[Event, resolution.Relation, str]] = []
        cands: list[tuple[Event, str]] = []
        for ev in self.events.values():
            if ev.status not in (EventStatus.OPEN, EventStatus.CONFIRMED, EventStatus.PARTIAL):
                continue
            rel, reason = resolution.relate(s, ev, self.statements, self.gaz)
            if rel in (resolution.Relation.MERGE_ECHO, resolution.Relation.MERGE_INDEPENDENT):
                merges.append((ev, rel, reason))
            elif rel is resolution.Relation.CANDIDATE:
                cands.append((ev, reason))

        if merges:
            ev, rel, reason = merges[0]
            resolution.attach(ev, s, self.statements, self.gaz)
            result.updated_events.append((ev, rel.value, reason))
            self._refresh_commission(ev)
            return

        ev = Event(
            id=self._id("ev"),
            statement_ids=[s.id],
            created_at=self.reports[s.report_id].received_at,
            node_id=s.location.node_id,
            time_range=s.time_range,
            independent_sources=resolution.independent_sources([s]),
        )
        self.events[ev.id] = ev
        result.new_events.append(ev)
        for other, reason in cands:
            lk = CandidateLink(id=self._id("lk"), event_a=ev.id, event_b=other.id, reason=reason)
            self.links[lk.id] = lk
            result.new_links.append(lk)
        self._refresh_commission(ev)

    # ---- 巡查委托 ----

    def _refresh_commission(self, ev: Event) -> None:
        stmts = [self.statements[i] for i in ev.statement_ids]
        existing = self._commission_of(ev.id)
        proposal = dispatch.propose(
            ev.id, stmts, ev.node_id, ev.independent_sources,
            self.gaz, list(self.gaz.people.values()),
            existing.id if existing else self._id("ms"),
        )
        if proposal is None:
            return
        if existing is None:
            self.commissions[proposal.id] = proposal
        elif existing.status == dispatch.PROPOSED:
            proposal.history = existing.history
            self.commissions[existing.id] = proposal
        # 已成行/需复核的委托不被自动改写，由值班员处理

    def _commission_of(self, event_id: str):
        for cm in self.commissions.values():
            if cm.event_id == event_id and cm.status not in (dispatch.CANCELLED, dispatch.DONE):
                return cm
        return None

    def decide_commission(self, commission_id: str, approve: bool, assignee: str | None = None):
        cm = self.commissions[commission_id]
        return dispatch.decide(cm, approve, assignee, list(self.gaz.people.values()), self.gaz)

    def narrow_commission(self, commission_id: str, node: str):
        return dispatch.narrow(self.commissions[commission_id], node, self.gaz)

    # ---- 角色视图 ----

    def duty_board(self) -> list[dict]:
        """值班员面板：不含受保护点精确位置。"""
        board = []
        for ev in self.events.values():
            if ev.status in (EventStatus.REJECTED, EventStatus.SPLIT, EventStatus.MERGED):
                continue
            stmts = [self.statements[i] for i in ev.statement_ids]
            basis = dispatch.basis_of(stmts, ev.independent_sources)
            cm = self._commission_of(ev.id)
            seg = self.gaz.segment_of(ev.node_id) if ev.node_id else None
            warnings = []
            if ev.node_id and self.gaz.nodes[ev.node_id].protected:
                warnings.append("涉及受保护地点，精确位置仅核查组可见")
            links = [
                {"id": lk.id, "with": lk.event_b if lk.event_a == ev.id else lk.event_a,
                 "reason": lk.reason, "status": lk.status}
                for lk in self.links.values()
                if ev.id in (lk.event_a, lk.event_b)
            ]
            if basis == "相互印证":
                advice = "建议成行（相互印证）"
            elif basis == "亲见":
                advice = "建议成行（亲见）"
            elif basis == "转述":
                advice = "可成行，但仅按区段核查（转述来源）"
            else:
                advice = "暂缓，先回访"
            board.append(
                {
                    "event_id": ev.id,
                    "status": ev.status.value,
                    "区段": self.gaz.nodes[seg].name if seg else "不明",
                    "时间": str(ev.time_range) if ev.time_range else "不明",
                    "依据": basis,
                    "独立来源": ev.independent_sources,
                    "相互印证": ev.independent_sources >= 2,
                    "候选关系": links,
                    "建议": advice,
                    "建议范围": (self.gaz.nodes[cm.scope_node].name if cm else "—")
                    + ("（精确）" if cm and cm.precise else "（区段）" if cm else ""),
                    "有巡护权限": dispatch.eligible(
                        list(self.gaz.people.values()), self.gaz, cm.scope_node
                    ) if cm else [],
                    "委托": f"{cm.id}/{cm.status}" if cm else "—",
                    "委托备注": cm.note if cm else "",
                    "警告": warnings,
                }
            )
        return board

    def verify_event(self, event_id: str) -> dict:
        """核查人员视图：原始定位依据完整可见（含受保护点）。"""
        ev = self.events[event_id]
        stmts = []
        for sid in ev.statement_ids:
            s = self.statements[sid]
            node = s.location.node_id
            stmts.append(
                {
                    "id": s.id,
                    "原文": s.text,
                    "署名": s.speaker,
                    "认识类型": s.epistemic.value,
                    "来源链": "→".join(s.source_chain),
                    "引用时间": s.quoted_time_raw or ("（按接收时间）" if s.time_assumed else "—"),
                    "解析时段": str(s.time_range) if s.time_range else "—",
                    "方位参照": s.location.raw,
                    "解析位置": self.gaz.nodes[node].name if node else "不明",
                    "回访结论": s.verdict or "—",
                }
            )
        node = ev.node_id
        return {
            "event_id": ev.id,
            "status": ev.status.value,
            "归并位置": self.gaz.nodes[node].name if node else "不明",
            "受保护": bool(node and self.gaz.nodes[node].protected),
            "独立来源": ev.independent_sources,
            "陈述": stmts,
            "拆分自/为": ev.superseded_by,
        }

    def publisher_board(self) -> dict:
        return {
            "草稿": [
                {"id": s.id, "text": s.text, "note": s.note}
                for s in self.publisher.sentences.values()
                if not s.live and s.bulletin_id is None
            ],
            "简报": [
                {"id": b.id, "title": b.title, "status": b.status,
                 "corrections": b.corrections, "withdrawn_reason": b.withdrawn_reason}
                for b in self.publisher.bulletins.values()
            ],
            "告警": self.publisher.alerts,
        }

    # ---- 外发 ----

    def draft_material(self, text: str, source: str = "") -> Sentence:
        return self.publisher.make_draft(text, source=source)

    def review_material(self, sentence_id: str):
        return self.publisher.review_draft(self.publisher.sentences[sentence_id])

    def publish_material(self, sentence_ids: list[str], title: str):
        return self.publisher.publish([self.publisher.sentences[i] for i in sentence_ids], title)

    def ingest_published(self, text: str, title: str):
        return self.publisher.ingest_published(text, title)

    def review_publications(self, trigger: str = "例行复核"):
        return self.publisher.review_live(trigger=trigger)

    def withdraw_bulletin(self, bulletin_id: str, reason: str, correction: str | None = None):
        b = self.publisher.withdraw(bulletin_id, reason)
        if correction:
            self.publisher.correct(bulletin_id, correction)
        return b

    def draft_summary(self, period: str) -> list[tuple[Sentence, list[str]]]:
        """起草月度线索摘要并逐句过推断控制。"""
        out = []
        for s in self.publisher.draft_summary(period, list(self.events.values()), self.statements):
            ok, notes = self.publisher.review_draft(s)
            if ok:
                out.append((s, notes))
        return out

    # ---- 审计 ----

    def audit_jsonl(self) -> str:
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in self.audit)
