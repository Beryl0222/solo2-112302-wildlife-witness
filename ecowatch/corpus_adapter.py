"""把 data/corpus.json 的登记记录转为接收流。

记录格式：
- note：``周X[HH时]<署名>|<正文>``；署名带“回访”为回访补记，带“转”为转发。
- bulletin：``已发简报|<正文>`` 登记为仍可访问简报；``拟发布材料|<正文>`` 登记为草稿。
- landmark / access：已转写入 data/gazetteer.json，这里不再接收。
- visit：返回给调用方，由值班员在面板上核派，不在适配器里自动决定。
"""
from __future__ import annotations

import re
from datetime import date, datetime, time

from .models import ReportKind

WEEKDAY_DATE = {
    "周一": date(2026, 11, 16),
    "周二": date(2026, 11, 17),
    "周三": date(2026, 11, 18),
    "周四": date(2026, 11, 19),
    "周五": date(2026, 11, 20),
    "周六": date(2026, 11, 21),
    "周日": date(2026, 11, 22),
}

PREFIX = re.compile(r"^(?P<week>周[一二三四五六日天])(?:(?P<hour>\d{1,2})时)?(?P<who>[^|]*)\|(?P<body>.*)$", re.S)


def parse_note(text: str):
    m = PREFIX.match(text)
    if not m:
        raise ValueError(f"无法解析记录前缀：{text[:20]}…")
    week = m.group("week")
    hour = int(m.group("hour") or 9)
    who = m.group("who")
    body = m.group("body")
    kind = ReportKind.PATROL
    if "回访" in who:
        kind = ReportKind.FOLLOWUP
        who = who.replace("回访", "")
    elif "转" in who:
        kind = ReportKind.FORWARD
        who = re.sub(r"转.*", "", who)
    received = datetime.combine(WEEKDAY_DATE[week], time(hour))
    return kind, who, body, received


def ingest_record(station, record: dict):
    kind = record["kind"]
    if kind in ("landmark", "access"):
        return None  # 台账知识，见 data/gazetteer.json
    if kind == "note":
        k, who, body, received = parse_note(record["text"])
        return station.ingest(k, who, body, received_at=received, channel=f"corpus:{record['id']}")
    if kind == "bulletin":
        title, _, body = record["text"].partition("|")
        if "已发" in title:
            return station.ingest_published(body, title=record["id"])
        return station.draft_material(body, source=record["id"])
    if kind == "visit":
        return ("visit", record)
    return None  # pragma: no cover
