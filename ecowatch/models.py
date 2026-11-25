"""核心数据模型。

名词约定：
- 陈述 Statement：从一条转写里辨出的最小断言，带认识类型（亲见/转述/未见/猜测）、
  引用时间、说话时使用的方位参照，以及来源链（最初观察者 → … → 署名者）。
- 事件 Event：被认为指向同一次目击的陈述集合。只有来源链能分开、且时段与地点
  都指向同一事件，才算相互印证的线索。
- 候选关系 CandidateLink：相似但可能来自对岸或上一次观察的报告之间的“待澄清”
  关系。直接合并会误派巡查，因此只挂关系、不合并。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime


class Epistemic(enum.Enum):
    """陈述的认识来源类型。"""

    FIRSTHAND = "亲见"
    HEARSAY = "转述"
    NOT_SEEN = "未见"
    GUESS = "猜测"


class ReportKind(enum.Enum):
    """持续接收的三类材料。"""

    PATROL = "巡护短记"
    FORWARD = "转发"
    FOLLOWUP = "回访补记"


@dataclass(frozen=True)
class DateRange:
    """引用时间解析出的日期区间（日粒度）。"""

    start: date
    end: date

    def overlaps(self, other: "DateRange") -> bool:
        return self.start <= other.end and other.start <= self.end

    def gap_days(self, other: "DateRange") -> int:
        """两区间相隔的天数；重叠为 0。"""
        if self.overlaps(other):
            return 0
        if self.end < other.start:
            return (other.start - self.end).days
        return (self.start - other.end).days

    def union(self, other: "DateRange") -> "DateRange":
        return DateRange(min(self.start, other.start), max(self.end, other.end))

    def __str__(self) -> str:  # pragma: no cover - 展示用
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()}~{self.end.isoformat()}"


@dataclass
class LocationRef:
    """从转写解析出的位置，保留说话时的方位参照方式。

    node_id 指向台账中的节点（区段/地标/巡护径/繁育点）；qualifiers 记录
    “上游”“往东”这类相对方位；depends_on_speaker 标记“对岸”这类只有知道
    说话人位置才能落定的参照——带这种参照的报告不能直接与既有事件合并。
    """

    node_id: str | None = None
    qualifiers: tuple[str, ...] = ()
    depends_on_speaker: bool = False
    raw: str = ""


@dataclass
class Statement:
    id: str
    report_id: str
    seq: int
    text: str
    speaker: str
    epistemic: Epistemic
    source_chain: tuple[str, ...]
    quoted_time_raw: str | None = None
    time_range: DateRange | None = None
    time_assumed: bool = False
    location: LocationRef = field(default_factory=LocationRef)
    count: int | None = None
    is_repeat: bool = False
    verdict: str | None = None  # 回访结论：confirmed / denied / None


class EventStatus(enum.Enum):
    OPEN = "待核查"
    CONFIRMED = "已确认"
    PARTIAL = "部分确认"
    REJECTED = "已排除"
    SPLIT = "已拆分"
    MERGED = "已并入"


@dataclass
class Event:
    id: str
    statement_ids: list[str]
    created_at: datetime
    status: EventStatus = EventStatus.OPEN
    node_id: str | None = None
    time_range: DateRange | None = None
    independent_sources: int = 0
    superseded_by: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class CandidateLink:
    """两个事件之间“尚待澄清”的候选关系。"""

    id: str
    event_a: str
    event_b: str
    reason: str
    status: str = "待澄清"  # 待澄清 / 已确认同一 / 已确认不同


@dataclass
class Report:
    id: str
    kind: ReportKind
    author: str
    text: str
    received_at: datetime
    channel: str = "语音转写"
    claims: list[dict] = field(default_factory=list)  # 回访补记的结构化结论
