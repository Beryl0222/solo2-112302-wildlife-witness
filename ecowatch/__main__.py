"""演示：用 corpus 记录走一遍 接收 → 核派 → 外发 全流程。

运行：python3 -m ecowatch
"""
from __future__ import annotations

import json
from pathlib import Path

from .corpus_adapter import ingest_record
from .gazetteer import Gazetteer
from .station import Station

ROOT = Path(__file__).resolve().parent.parent


def _hr(title: str) -> None:
    print(f"\n=== {title} ===")


def _show_result(st: Station, result) -> None:
    for s in result.statements:
        loc = st.gaz.nodes[s.location.node_id].name if s.location.node_id else "不明"
        t = s.quoted_time_raw or ("按接收时间" if s.time_assumed else "—")
        print(
            f"  陈述 {s.id} [{s.epistemic.value}] “{s.text}”"
            f" | 来源链 {'→'.join(s.source_chain)} | 引用时间 {t} | 位置 {loc}"
        )
    for ev, rel, reason in result.updated_events:
        print(f"  并入事件 {ev.id}（{rel}：{reason}），独立来源 {ev.independent_sources}")
    for ev in result.new_events:
        print(f"  新事件 {ev.id}")
    for lk in result.new_links:
        print(f"  候选关系 {lk.id}：{lk.event_a} ~ {lk.event_b}（待澄清：{lk.reason}）")
    for e in result.effects:
        print(f"  回访处理：{e}")
    for a in result.alerts:
        print(f"  外发告警：{a['reason']}（{a['advice']}）")


def main() -> None:
    gaz = Gazetteer.load(ROOT / "data" / "gazetteer.json")
    st = Station(gaz)
    corpus = json.loads((ROOT / "data" / "corpus.json").read_text(encoding="utf-8"))
    records = {r["id"]: r for r in corpus["records"]}

    _hr("1. 接收巡护短记 n1（齐松，周一19时）")
    _show_result(st, ingest_record(st, records["n1"]))

    _hr("2. 接收转发 n2（米乐转群消息，周二）：一段里两个目击")
    _show_result(st, ingest_record(st, records["n2"]))
    print("  说明：群消息那条来源链为 齐松→米乐，与 n1 同源，不计独立印证；"
          "米乐自己今早那条单独成事件，与 n1 只挂候选关系。")

    _hr("3. 接收回访补记 n3（米乐，周三）：只确认其中一个目击")
    _show_result(st, ingest_record(st, records["n3"]))

    _hr("4. 值班员面板")
    for row in st.duty_board():
        print(f"  {row['event_id']} [{row['status']}] 区段{row['区段']} 时间{row['时间']}"
              f" 依据:{row['依据']} 独立来源:{row['独立来源']}")
        print(f"    建议:{row['建议']} 范围:{row['建议范围']} 有权限:{row['有巡护权限']}"
              f" 委托:{row['委托']}")
        for lk in row["候选关系"]:
            print(f"    候选关系 {lk['id']} ↔ {lk['with']}：{lk['status']}（{lk['reason']}）")
        for w in row["警告"]:
            print(f"    警告：{w}")
        if row["委托备注"]:
            print(f"    备注：{row['委托备注']}")

    _hr("5. 核派（对应 v1：周四齐松赴长桥查看）")
    ev1 = next(e for e in st.events.values() if len(e.statement_ids) == 2)
    cm = st._commission_of(ev1.id)
    st.decide_commission(cm.id, approve=True, assignee="齐松")
    print(f"  委托 {cm.id} → {cm.status}，巡查人 齐松，范围 {st.gaz.nodes[cm.scope_node].name}")
    try:
        st.decide_commission(cm.id, approve=True, assignee="米乐")
    except PermissionError as exc:
        print(f"  若改派米乐：{exc}（米乐只有西脚权限）")

    _hr("6. 外发：登记已发简报 b1 并做暴露复核")
    b1 = ingest_record(st, records["b1"])
    alerts = st.review_publications()
    for a in alerts:
        print(f"  告警：简报 {a['bulletin']} —— {a['reason']}；{a['advice']}")
    if alerts:
        st.withdraw_bulletin(
            alerts[0]["bulletin"],
            reason="与仍可访问句子合用唯一指向繁育点",
            correction="更正：上一期关于“观察点”的描述作废，请勿据此寻找。",
        )
        print(f"  简报 {alerts[0]['bulletin']} 已下架并发更正。")

    _hr("7. 外发：拟发布材料 b2 过审查（个别句子安全≠合用安全）")
    draft = ingest_record(st, records["b2"])
    ok, notes = st.review_material(draft.id)
    for n in notes:
        print(f"  {n}")
    print(f"  改写后：“{draft.text}”")
    st.publish_material([draft.id], title="月度线索地图·增补")

    _hr("8. 月度线索摘要（约定的区段粒度）")
    sentences = []
    for s, notes in st.draft_summary("2026年11月"):
        print(f"  草稿：“{s.text}”{('（' + s.note + '）') if s.note else ''}")
        for n in notes:
            print(f"    {n}")
        sentences.append(s)
    st.publish_material([s.id for s in sentences], title="2026年11月线索摘要")
    print("  已发布。")

    _hr("9. 核查人员视图：原始定位依据完整可见")
    view = st.verify_event(ev1.id)
    print(f"  事件 {view['event_id']} [{view['status']}] 归并位置 {view['归并位置']}"
          f"（受保护：{view['受保护']}）独立来源 {view['独立来源']}")
    for s in view["陈述"]:
        print(f"    {s['id']} [{s['认识类型']}] “{s['原文']}” 来源链 {s['来源链']}"
              f" 位置 {s['解析位置']} 回访 {s['回访结论']}")

    _hr("10. 外发员面板")
    board = st.publisher_board()
    for b in board["简报"]:
        print(f"  简报 {b['id']}（{b['title']}）：{b['status']} {b['corrections'] or b['withdrawn_reason']}")


if __name__ == "__main__":
    main()
