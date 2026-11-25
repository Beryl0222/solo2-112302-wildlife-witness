"""事件归并与相互印证。

规则要点：
- 多个署名不代表多个独立来源；来源链相交（含转发、群消息转述）只能作为
  同源回声并入事件，不计独立来源。
- 只有来源链能分开、且时段与地点都指向同一事件，才合并为相互印证。
- 相似但可能来自对岸（方位参照依赖说话人位置）或上一次观察（时段相邻）
  的报告，不合并，只挂“待澄清”的候选关系，以免误派巡查。
- 猜测与未见不参与归并，各自单独成事件留档。
"""
from __future__ import annotations

import enum

from .gazetteer import Gazetteer
from .models import Epistemic, Event, Statement

CANDIDATE_GAP_DAYS = 3


class Relation(enum.Enum):
    MERGE_INDEPENDENT = "独立印证"
    MERGE_ECHO = "同源转述"
    CANDIDATE = "待澄清候选"
    NONE = "无关"


def chains_independent(a: Statement, b: Statement) -> bool:
    return not set(a.source_chain) & set(b.source_chain)


def independent_sources(statements: list[Statement]) -> int:
    """事件内互不相交的来源链的最大条数（猜测/未见不计入印证）。"""
    chains = [
        set(s.source_chain)
        for s in statements
        if s.epistemic in (Epistemic.FIRSTHAND, Epistemic.HEARSAY)
    ]
    used: set[str] = set()
    count = 0
    for chain in sorted(chains, key=len):
        if not chain & used:
            used |= chain
            count += 1
    return count


def relate(stmt: Statement, event: Event, statements: dict[str, Statement], gaz: Gazetteer):
    """判断新陈述与既有事件的关系，返回 (Relation, 理由)。"""
    if stmt.epistemic in (Epistemic.GUESS, Epistemic.NOT_SEEN):
        return Relation.NONE, "猜测与未见不参与归并"
    estmts = [statements[i] for i in event.statement_ids]
    if any(s.epistemic in (Epistemic.GUESS, Epistemic.NOT_SEEN) for s in estmts):
        return Relation.NONE, "猜测与未见不参与归并"

    if stmt.location.depends_on_speaker:
        return Relation.CANDIDATE, "方位参照依赖说话人位置（如对岸），可能并非同一处"

    loc_ok = gaz.refs_consistent(stmt.location.node_id, event.node_id)
    if loc_ok is False:
        return Relation.NONE, "位置指向不同地点"

    if stmt.time_range and event.time_range:
        gap = stmt.time_range.gap_days(event.time_range)
        if gap > CANDIDATE_GAP_DAYS:
            return Relation.NONE, "时段相隔较远"
    else:
        gap = None

    independent = all(chains_independent(stmt, s) for s in estmts)

    if loc_ok is True and gap == 0:
        if independent:
            return Relation.MERGE_INDEPENDENT, "来源链独立，时段与地点一致"
        return Relation.MERGE_ECHO, "来源链相交：同源转述，不计独立来源"
    if loc_ok is True and gap is None:
        return Relation.CANDIDATE, "时间不明，无法确认同一"
    if loc_ok is True:
        return Relation.CANDIDATE, "时段相邻，可能是上一次观察"
    return Relation.CANDIDATE, "位置信息不足，无法确认同一"


def attach(event: Event, stmt: Statement, statements: dict[str, Statement], gaz: Gazetteer) -> None:
    """把陈述并入事件并刷新归并结果。"""
    event.statement_ids.append(stmt.id)
    event.node_id = gaz.more_specific(event.node_id, stmt.location.node_id)
    if stmt.time_range:
        event.time_range = (
            stmt.time_range if event.time_range is None else event.time_range.union(stmt.time_range)
        )
    event.independent_sources = independent_sources(
        [statements[i] for i in event.statement_ids]
    )
