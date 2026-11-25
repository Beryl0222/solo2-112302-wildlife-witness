"""回访补记：确认/否定/定位/候选澄清/事件拆分。

回访结论可以是结构化 claims（回访员在记录时编码），也可以由规则从
回访文字中解析（“今早那只说的是西脚”“和上游那两只不是一处”）。
一段里两个互不相同的目击，回访结论只作用于被指认的那一条陈述。
"""
from __future__ import annotations

import re

from . import dispatch, resolution
from .models import Epistemic, Event, EventStatus

LOCATE = re.compile(r"说的是(?P<loc>[一-龥]{1,6})")
DISTINCT = re.compile(r"不是(?:一处|同一处|一回事|同一只|一批)")
SAME = re.compile(r"是(?:同一处|一回事|同一批)")


def _event_of(events: dict[str, Event], statement_id: str) -> Event | None:
    for ev in events.values():
        if statement_id in ev.statement_ids:
            return ev
    return None


def _latest_open_firsthand(statements, events, author: str):
    """同一署名最近一条仍待核的亲见陈述（回访通常核自己那条）。"""
    target = None
    for s in statements.values():
        if s.speaker != author or s.epistemic is not Epistemic.FIRSTHAND or s.verdict:
            continue
        ev = _event_of(events, s.id)
        if ev and ev.status in (EventStatus.OPEN, EventStatus.PARTIAL):
            target = s
    return target


def parse_claims(report, statements, events, links, gaz) -> list[dict]:
    """从回访文字规则解析结论。"""
    claims: list[dict] = []
    target = _latest_open_firsthand(statements, events, report.author)

    m = LOCATE.search(report.text)
    if m and target:
        node = gaz.find(m.group("loc"))
        if node:
            claims.append({"action": "locate", "statement": target.id, "node": node.id})
            claims.append({"action": "confirm", "statement": target.id})

    if target and DISTINCT.search(report.text):
        ev = _event_of(events, target.id)
        for lk in links.values():
            if lk.status == "待澄清" and ev and ev.id in (lk.event_a, lk.event_b):
                claims.append({"action": "resolve_candidate", "link": lk.id, "same": False})
    if target and SAME.search(report.text):
        ev = _event_of(events, target.id)
        for lk in links.values():
            if lk.status == "待澄清" and ev and ev.id in (lk.event_a, lk.event_b):
                claims.append({"action": "resolve_candidate", "link": lk.id, "same": True})
    return claims


def recompute_status(ev: Event, statements) -> None:
    vs = [statements[i].verdict for i in ev.statement_ids]
    conf = vs.count("confirmed")
    den = vs.count("denied")
    if conf and den:
        ev.status = EventStatus.PARTIAL
    elif conf:
        ev.status = EventStatus.CONFIRMED
    elif den and den == len(vs):
        ev.status = EventStatus.REJECTED
    else:
        ev.status = EventStatus.OPEN


def merge_events(station, keep_id: str, drop_id: str) -> None:
    """候选澄清为“同一”后并入：陈述归并、独立来源重算。"""
    keep, drop = station.events[keep_id], station.events[drop_id]
    for sid in list(drop.statement_ids):
        resolution.attach(keep, station.statements[sid], station.statements, station.gaz)
    drop.status = EventStatus.MERGED
    drop.superseded_by = [keep_id]
    recompute_status(keep, station.statements)
    station._refresh_commission(keep)


def split_event(station, event_id: str, groups: list[list[str]], note: str = "") -> list[str]:
    """把一次共享目击拆成多个事件。

    后果：原委托标记“需复核”（值班员能看出哪张委托应改范围或分开）；
    新事件各自重新评估委托；核查人员仍能通过原事件看到原始定位依据。
    """
    gaz = station.gaz
    ev = station.events[event_id]
    flat = [s for g in groups for s in g]
    if sorted(flat) != sorted(ev.statement_ids):
        raise ValueError("拆分必须覆盖原事件全部陈述")

    new_ids: list[str] = []
    for g in groups:
        nid = station._id("ev")
        stmts = [station.statements[s] for s in g]
        node = None
        tr = None
        for s in stmts:
            node = gaz.more_specific(node, s.location.node_id)
            if s.time_range:
                tr = s.time_range if tr is None else tr.union(s.time_range)
        e = Event(id=nid, statement_ids=list(g), created_at=ev.created_at, node_id=node, time_range=tr)
        e.independent_sources = resolution.independent_sources(stmts)
        recompute_status(e, station.statements)
        station.events[nid] = e
        new_ids.append(nid)

    ev.status = EventStatus.SPLIT
    ev.superseded_by = new_ids
    ev.note = note or "回访拆分为多个事件"

    for cm in station.commissions.values():
        if cm.event_id == event_id and cm.status in (dispatch.PROPOSED, dispatch.ACTIVE):
            cm.status = dispatch.NEEDS_REVIEW
            cm.history.append("事件拆分：核查范围需调整或拆分为多张委托")
    for lk in station.links.values():
        if lk.status == "待澄清" and event_id in (lk.event_a, lk.event_b):
            lk.status = "已确认不同"
    for nid in new_ids:
        station._refresh_commission(station.events[nid])
    return new_ids


def apply_claims(station, claims: list[dict]) -> list[str]:
    """逐条应用回访结论，返回处理记录。"""
    effects: list[str] = []
    for c in claims:
        action = c["action"]
        if action in ("confirm", "deny"):
            st = station.statements[c["statement"]]
            st.verdict = "confirmed" if action == "confirm" else "denied"
            ev = _event_of(station.events, st.id)
            if ev:
                recompute_status(ev, station.statements)
                station._refresh_commission(ev)
            effects.append(f"陈述 {st.id} 回访结论：{'确认' if action == 'confirm' else '排除'}")

        elif action == "locate":
            st = station.statements[c["statement"]]
            st.location.node_id = c["node"]
            ev = _event_of(station.events, st.id)
            if ev:
                ev.node_id = c["node"]
                station._refresh_commission(ev)
            effects.append(f"陈述 {st.id} 位置核定为 {station.gaz.nodes[c['node']].name}")

        elif action == "resolve_candidate":
            lk = station.links[c["link"]]
            if c["same"]:
                lk.status = "已确认同一"
                merge_events(station, lk.event_a, lk.event_b)
                effects.append(f"候选关系 {lk.id} 确认为同一事件，已并入 {lk.event_a}")
            else:
                lk.status = "已确认不同"
                effects.append(f"候选关系 {lk.id} 确认为不同事件")

        elif action == "split":
            new_ids = split_event(station, c["event"], c["groups"], c.get("note", ""))
            effects.append(f"事件 {c['event']} 拆分为 {'、'.join(new_ids)}")
        else:  # pragma: no cover - 防御
            raise ValueError(f"未知回访结论动作：{action}")
    return effects
