"""外发摘要与简报的位置线索控制。

两层防护：
1. 文字政策：公开材料只用约定的区段粒度；受保护地点的名称与别名不得出现；
   地标别称、巡护径同样计入（“红桥”就是“长桥”）。
2. 推断控制：把每条仍可访问的句子的位置线索看成对“观察点”候选集的约束
   （在某区域内 / 排除某些地点 / 声称锁定观察点）。若全部约束合并后唯一
   指向受保护点，则改写或省去有问题的位置线索，保留与一般趋势有关的文字。

先前发布且仍可访问的简报始终计入约束——受保护地点不能借助馆名别称、
路线和旧简报交叉推回。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .gazetteer import Gazetteer
from .models import Epistemic, Event, EventStatus, Statement

EXCLUSION = re.compile(r"(?P<excl>[^，。；]{0,30}?)(?:之外|以外)")
REMAINDER = re.compile(r"(?:剩下|其余)")
IDENTIFYING = re.compile(r"(?:剩下的就是|其余的就是|那个观察点|该观察点|的?第[一二三四五六七八九十\d]+处)")
ORDINAL = re.compile(r"的?第[一二三四五六七八九十\d]+处")


@dataclass
class Clues:
    """一条外发句子透露的位置线索。"""

    within: set[str] = field(default_factory=set)  # 观察点在这些节点范围内
    exclude: set[str] = field(default_factory=set)  # 排除这些叶子地点
    identifying: bool = False  # 是否声称锁定观察点（“剩下的就是”“第三处”）


@dataclass
class Sentence:
    id: str
    text: str
    clues: Clues
    origin_events: list[str] = field(default_factory=list)
    origin_statements: list[str] = field(default_factory=list)
    source: str = ""
    live: bool = False  # 仍可访问
    bulletin_id: str | None = None
    note: str = ""


@dataclass
class Bulletin:
    id: str
    title: str
    sentence_ids: list[str]
    status: str = "已发布"  # 已发布 / 已下架
    corrections: list[str] = field(default_factory=list)
    withdrawn_reason: str = ""


class Publisher:
    def __init__(self, gaz: Gazetteer):
        self.gaz = gaz
        self.sentences: dict[str, Sentence] = {}
        self.bulletins: dict[str, Bulletin] = {}
        self.alerts: list[dict] = []
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    # ---- 位置线索提取 ----

    def clues_from_text(self, text: str) -> Clues:
        clues = Clues()
        excl_spans: list[tuple[int, int]] = []
        for m in EXCLUSION.finditer(text):
            excl_spans.append(m.span("excl"))
            for _s, _e, node in self.gaz.find_spans(m.group("excl")):
                if self.gaz.children(node.id):
                    clues.within.add(node.id)  # “东脚草垛和石湾之外”：东脚是范围
                else:
                    clues.exclude.add(node.id)  # 草垛、石湾是被排除的地点
        if excl_spans and REMAINDER.search(text):
            clues.identifying = True
        if IDENTIFYING.search(text):
            clues.identifying = True
        for s, e, node in self.gaz.find_spans(text):
            if any(s >= a and e <= b for a, b in excl_spans):
                continue
            clues.within.add(node.id)
        return clues

    # ---- 推断控制 ----

    def _apply(self, candidates: set[str], clues: Clues) -> set[str]:
        for w in clues.within:
            candidates &= self.gaz.leaves(w)
        candidates -= clues.exclude
        return candidates

    def candidates(self, extra: list[Sentence] | None = None) -> set[str]:
        """全部仍可访问句子（外加拟发句子）合并后的观察点候选集。"""
        c = set(self.gaz.all_leaves())
        for s in self.sentences.values():
            if s.live:
                c = self._apply(c, s.clues)
        for s in extra or []:
            c = self._apply(c, s.clues)
        return c

    def pinpointed(self, extra: list[Sentence] | None = None):
        """约束合并后是否唯一指向某个受保护点。"""
        c = self.candidates(extra)
        if len(c) == 1:
            node = self.gaz.nodes[next(iter(c))]
            if node.protected:
                return node
        return None

    # ---- 文字政策 ----

    def text_violations(self, text: str) -> list[str]:
        out = []
        for s, e, node in self.gaz.find_spans(text):
            mention = text[s:e]
            if node.protected:
                out.append(f"出现受保护地点名称/别名“{mention}”")
            elif node.id != self.gaz.public_segment:
                out.append(f"超出区段粒度：“{mention}”")
        return out

    # ---- 改写与省略 ----

    def _generalize(self, text: str) -> str:
        """把区段以下的地名（含别称、巡护径）改写为区段名，去掉序数锁定。"""
        seg = self.gaz.nodes[self.gaz.public_segment].name
        spans = [
            (s, e, n)
            for s, e, n in self.gaz.find_spans(text)
            if n.id != self.gaz.public_segment
        ]
        for s, e, _n in sorted(spans, reverse=True):
            text = text[:s] + seg + text[e:]
        text = ORDINAL.sub("", text)
        while seg * 2 in text:
            text = text.replace(seg * 2, seg)
        text = text.replace(f"{seg}的{seg}", seg).replace(f"{seg}{seg}", seg)
        return text

    def _strip_location(self, text: str) -> str:
        """省去位置线索，保留与一般趋势有关的文字。"""
        parts = [p for p in re.split(r"[，。；]", text) if p]
        kept = [p for p in parts if not self.gaz.find_spans(p)]
        if kept:
            return "，".join(kept) + "。（位置从略）"
        return "近期有活动迹象报告，具体位置从略。"

    def review_draft(self, sentence: Sentence) -> tuple[bool, list[str]]:
        """审查拟发句子：先文字政策、再推断控制；返回 (可否发布, 处理记录)。"""
        notes: list[str] = []
        violations = self.text_violations(sentence.text)
        if violations:
            notes.append(f"改写：{'；'.join(violations)}，位置线索收到区段粒度")
            sentence.text = self._generalize(sentence.text)
            sentence.clues = Clues(within={self.gaz.public_segment})

        site = self.pinpointed(extra=[sentence])
        if site is not None:
            if sentence.clues.within != {self.gaz.public_segment}:
                sentence.text = self._generalize(sentence.text)
                sentence.clues = Clues(within={self.gaz.public_segment})
                notes.append(f"推断控制：与已发句子合用唯一指向{site.name}，已收到区段粒度")
                site = self.pinpointed(extra=[sentence])
            if site is not None:
                sentence.text = self._strip_location(sentence.text)
                sentence.clues = Clues()
                notes.append(f"推断控制：仍唯一指向{site.name}，已省去位置线索，保留趋势文字")

        ok = not self.text_violations(sentence.text) and self.pinpointed(extra=[sentence]) is None
        return ok, notes

    # ---- 简报生命周期 ----

    def make_draft(self, text: str, source: str = "") -> Sentence:
        s = Sentence(self._id("sn"), text, self.clues_from_text(text), source=source)
        self.sentences[s.id] = s
        return s

    def ingest_published(self, text: str, title: str) -> Bulletin:
        """登记一条先前发布、仍可访问的简报。"""
        s = Sentence(self._id("sn"), text, self.clues_from_text(text), live=True)
        b = Bulletin(self._id("bl"), title, [s.id])
        s.bulletin_id = b.id
        self.sentences[s.id] = s
        self.bulletins[b.id] = b
        return b

    def publish(self, sentences: list[Sentence], title: str) -> Bulletin:
        b = Bulletin(self._id("bl"), title, [s.id for s in sentences])
        for s in sentences:
            s.live = True
            s.bulletin_id = b.id
            self.sentences[s.id] = s
        self.bulletins[b.id] = b
        return b

    def review_live(self, trigger: str = "") -> list[dict]:
        """暴露复核：仍可访问的句子合并后唯一指向受保护点时告警。

        告警定位到“关键句”——拿掉它就不再唯一指向——所在简报，
        由外发员单独判断是否下架并更正。
        """
        site = self.pinpointed()
        if site is None:
            return []
        live = [s for s in self.sentences.values() if s.live]
        out = []
        for s in live:
            rest = set(self.gaz.all_leaves())
            for x in live:
                if x.id != s.id:
                    rest = self._apply(rest, x.clues)
            still = len(rest) == 1 and self.gaz.nodes[next(iter(rest))].protected
            if not still:
                out.append(
                    {
                        "bulletin": s.bulletin_id,
                        "sentence": s.id,
                        "site": site.name,
                        "reason": f"与仍可访问的句子合用唯一指向受保护点{site.name}",
                        "trigger": trigger,
                        "advice": "建议下架并更正",
                    }
                )
        self.alerts.extend(out)
        return out

    def withdraw(self, bulletin_id: str, reason: str) -> Bulletin:
        b = self.bulletins[bulletin_id]
        b.status = "已下架"
        b.withdrawn_reason = reason
        for sid in b.sentence_ids:
            self.sentences[sid].live = False
        return b

    def correct(self, bulletin_id: str, text: str) -> Bulletin:
        b = self.bulletins[bulletin_id]
        b.corrections.append(text)
        return b

    # ---- 月度线索摘要 ----

    def draft_summary(
        self,
        period: str,
        events: list[Event],
        statements: dict[str, Statement],
    ) -> list[Sentence]:
        """按约定的区段粒度起草月度线索摘要（猜测/未见不计入）。"""
        groups: dict[str, list[Event]] = {}
        for ev in events:
            if ev.status in (EventStatus.REJECTED, EventStatus.SPLIT, EventStatus.MERGED):
                continue
            stmts = [statements[i] for i in ev.statement_ids]
            if all(s.epistemic in (Epistemic.GUESS, Epistemic.NOT_SEEN) for s in stmts):
                continue
            seg = self.gaz.segment_of(ev.node_id) if ev.node_id else None
            groups.setdefault(seg or self.gaz.public_segment, []).append(ev)
        out = []
        for seg, evs in groups.items():
            confirmed = sum(1 for e in evs if e.status is EventStatus.CONFIRMED)
            text = f"{period}，{self.gaz.nodes[seg].name}有{len(evs)}起活动报告"
            if confirmed:
                text += f"（{confirmed}起已确认）"
            text += "。"
            s = Sentence(
                self._id("sn"),
                text,
                Clues(within={seg}),
                origin_events=[e.id for e in evs],
                origin_statements=[i for e in evs for i in e.statement_ids],
            )
            if any(e.node_id and self.gaz.nodes[e.node_id].protected for e in evs):
                s.note = "含受保护地点周边记录，已按区段粒度外发"
            self.sentences[s.id] = s
            out.append(s)
        return out
