"""区段、地标（含别称）、巡护径与受保护地点的登记知识。

地理与人员知识登记在 data/gazetteer.json（依据 corpus 中 l1/l2/a1 记录转写）。
公开材料只允许使用“区段”粒度；地标别称、巡护径同样计入识别风险。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    kind: str  # 区段 / 地标 / 巡护径 / 繁育点
    parent: str | None
    aliases: frozenset[str] = frozenset()
    protected: bool = False


@dataclass(frozen=True)
class Person:
    name: str
    aliases: frozenset[str] = frozenset()
    can_patrol: frozenset[str] = frozenset()  # 可巡护节点（含其子级）


class Gazetteer:
    def __init__(self, nodes: list[Node], people: list[Person], public_segment: str):
        self.nodes = {n.id: n for n in nodes}
        self.people = {p.name: p for p in people}
        self.public_segment = public_segment
        self._alias: dict[str, str] = {}
        for n in nodes:
            self._alias[n.name] = n.id
            for a in n.aliases:
                self._alias[a] = n.id
        self._person_alias: dict[str, str] = {}
        for p in people:
            self._person_alias[p.name] = p.name
            for a in p.aliases:
                self._person_alias[a] = p.name
        self._children: dict[str, list[str]] = {n.id: [] for n in nodes}
        for n in nodes:
            if n.parent:
                self._children[n.parent].append(n.id)

    @classmethod
    def load(cls, path: str | Path) -> "Gazetteer":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        nodes = [
            Node(
                id=r["id"],
                name=r["name"],
                kind=r["kind"],
                parent=r.get("parent"),
                aliases=frozenset(r.get("aliases", [])),
                protected=bool(r.get("protected", False)),
            )
            for r in raw["nodes"]
        ]
        people = [
            Person(
                name=r["name"],
                aliases=frozenset(r.get("aliases", [])),
                can_patrol=frozenset(r.get("can_patrol", [])),
            )
            for r in raw.get("people", [])
        ]
        return cls(nodes, people, raw["public_segment"])

    # ---- 名称解析 ----

    def find(self, text: str) -> Node | None:
        """在文本中找最长匹配的地名/别称。"""
        best: tuple[str, str] | None = None
        for alias, nid in self._alias.items():
            if alias in text and (best is None or len(alias) > len(best[0])):
                best = (alias, nid)
        return self.nodes[best[1]] if best else None

    def find_spans(self, text: str) -> list[tuple[int, int, Node]]:
        """返回 [(start, end, node)]，长匹配优先、互不重叠，按出现位置排序。"""
        hits: list[tuple[int, int, str]] = []
        for alias, nid in self._alias.items():
            start = 0
            while True:
                i = text.find(alias, start)
                if i < 0:
                    break
                hits.append((i, i + len(alias), nid))
                start = i + 1
        out: list[tuple[int, int, Node]] = []
        occupied: list[tuple[int, int]] = []
        for s, e, nid in sorted(hits, key=lambda h: -(h[1] - h[0])):
            if any(s < oe and e > os_ for os_, oe in occupied):
                continue
            occupied.append((s, e))
            out.append((s, e, self.nodes[nid]))
        return sorted(out, key=lambda h: h[0])

    def canonical_person(self, name: str) -> str:
        """人的别称归一（如“小齐”→“齐松”）；未知人名原样保留。"""
        return self._person_alias.get(name, name)

    def is_known_place(self, text: str) -> bool:
        return self.find(text) is not None

    # ---- 树结构 ----

    def children(self, nid: str) -> list[str]:
        return list(self._children.get(nid, []))

    def descendants(self, nid: str) -> set[str]:
        out: set[str] = set()
        stack = [nid]
        while stack:
            x = stack.pop()
            for c in self._children.get(x, []):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    def leaves(self, nid: str) -> set[str]:
        """nid 之下的叶子地点（含自身若为叶子）。"""
        ds = self.descendants(nid) | {nid}
        return {x for x in ds if not self._children.get(x)}

    def all_leaves(self) -> set[str]:
        return {n.id for n in self.nodes.values() if not self._children.get(n.id)}

    def ancestors(self, nid: str) -> list[str]:
        """含自身，向根排列。"""
        out = []
        x: str | None = nid
        while x is not None:
            out.append(x)
            x = self.nodes[x].parent
        return out

    def is_ancestor(self, a: str, b: str) -> bool:
        """a 是否为 b 的祖先（含相等）。"""
        return a in self.ancestors(b)

    def segment_of(self, nid: str) -> str | None:
        for a in self.ancestors(nid):
            if self.nodes[a].kind == "区段":
                return a
        return None

    def protected_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.protected]

    def nearest_public_ancestor(self, nid: str) -> str:
        """受保护节点向上收到的最细非保护节点（供值班员核派范围用）。"""
        x: str | None = nid
        while x is not None and self.nodes[x].protected:
            x = self.nodes[x].parent
        return x if x is not None else self.public_segment

    # ---- 位置参照关系 ----

    def refs_consistent(self, node_a: str | None, node_b: str | None) -> bool | None:
        """两个位置参照是否可能指向同一处。

        True=同一或包含关系；False=不同分支；None=信息不足无法判断。
        """
        if node_a is None or node_b is None:
            return None
        if self.is_ancestor(node_a, node_b) or self.is_ancestor(node_b, node_a):
            return True
        return False

    def more_specific(self, a: str | None, b: str | None) -> str | None:
        if a is None:
            return b
        if b is None:
            return a
        return b if self.is_ancestor(a, b) and a != b else a
