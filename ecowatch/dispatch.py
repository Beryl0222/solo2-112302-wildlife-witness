"""巡查委托：值班员据线索决定是否成行、核查范围、以及谁已有巡护权限。

硬性规则：不能从转述的断言直接下达精确地点的任务——仅转述来源的线索
只能按区段核查；精确点位的委托必须有亲见或相互印证作底。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .gazetteer import Gazetteer, Person
from .models import Epistemic, Statement

PROPOSED = "待决定"
ACTIVE = "已成行"
REJECTED = "不成行"
NEEDS_REVIEW = "需复核"
DONE = "已完成"
CANCELLED = "已取消"


@dataclass
class Commission:
    id: str
    event_id: str
    scope_node: str
    precise: bool  # 是否精确点位（False=按区段核查）
    basis: str  # 相互印证 / 亲见 / 转述
    status: str = PROPOSED
    assignee: str | None = None
    note: str = ""
    history: list[str] = field(default_factory=list)


def basis_of(statements: list[Statement], independent: int) -> str:
    if independent >= 2:
        return "相互印证"
    if any(s.epistemic is Epistemic.FIRSTHAND for s in statements):
        return "亲见"
    if any(s.epistemic is Epistemic.HEARSAY for s in statements):
        return "转述"
    if all(s.epistemic is Epistemic.NOT_SEEN for s in statements):
        return "未见"
    return "猜测"


def eligible(people: list[Person], gaz: Gazetteer, scope_node: str) -> list[str]:
    """对核查范围已有巡护权限的人（权限覆盖该节点及其子级）。"""
    out = []
    for p in people:
        if any(gaz.is_ancestor(perm, scope_node) for perm in p.can_patrol):
            out.append(p.name)
    return sorted(out)


def propose(
    event_id: str,
    statements: list[Statement],
    event_node: str | None,
    independent: int,
    gaz: Gazetteer,
    people: list[Person],
    cid: str,
) -> Commission | None:
    """为事件生成委托建议；猜测与未见不派单。"""
    basis = basis_of(statements, independent)
    if basis in ("猜测", "未见"):
        return None
    node = event_node or gaz.public_segment
    if basis == "转述":
        scope = gaz.segment_of(node) or gaz.public_segment
        return Commission(
            cid,
            event_id,
            scope,
            precise=False,
            basis=basis,
            note="仅转述来源：不能从转述的断言直接下达精确地点的任务，按区段核查",
        )
    scope = gaz.nearest_public_ancestor(node)  # 受保护点向上收到非保护节点
    note = ""
    if any(s.epistemic is Epistemic.HEARSAY for s in statements):
        note = "含同源转述依据，独立来源数以面板为准"
    return Commission(cid, event_id, scope, precise=True, basis=basis, note=note)


def decide(
    cm: Commission, approve: bool, assignee: str | None, people: list[Person], gaz: Gazetteer
) -> Commission:
    """值班员决定成行/不成行；成行必须指名对范围有权限的巡查人。"""
    if not approve:
        cm.status = REJECTED
        cm.history.append("值班员决定不成行")
        return cm
    if assignee is None:
        raise ValueError("成行必须指名巡查人")
    ok = any(
        p.name == assignee and any(gaz.is_ancestor(perm, cm.scope_node) for perm in p.can_patrol)
        for p in people
    )
    if not ok:
        raise PermissionError(f"{assignee} 对 {cm.scope_node} 无巡护权限")
    cm.assignee = assignee
    cm.status = ACTIVE
    cm.history.append(f"值班员决定成行，巡查人 {assignee}")
    return cm


def narrow(cm: Commission, node: str, gaz: Gazetteer) -> Commission:
    """收窄核查范围；转述来源的委托不得收窄为精确点位。"""
    if cm.basis == "转述":
        raise PermissionError("不能从转述的断言直接下达精确地点的任务")
    if not gaz.is_ancestor(cm.scope_node, node):
        raise ValueError("新范围不在原核查范围内")
    cm.scope_node = node
    cm.precise = True
    cm.history.append(f"范围收窄至 {node}")
    return cm
