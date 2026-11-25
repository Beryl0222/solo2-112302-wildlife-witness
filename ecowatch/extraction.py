"""语音转写的规则解析。

从每条报告文本中辨出：
- 认识类型：亲见 / 转述 / 未见 / 猜测；
- 引用时间（如“昨儿”“上星期”）并解析为日期区间；
- 说话时使用的方位参照（地标、上下游、对岸、“那边”……）；
- 来源链（最初观察者 → … → 署名者）。

解析以句（。！？）为单位判定归属（“是小齐跟群里说的”会回溯影响整句），
以分句（，；）为单位辨出观察断言。一段里两个互不相同的目击会拆成两条陈述。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .gazetteer import Gazetteer
from .models import DateRange, Epistemic, LocationRef, Report, ReportKind, Statement

SENT_SPLIT = re.compile(r"[。！？!?]+")
CLAUSE_SPLIT = re.compile(r"[，；,;]+")

NOT_SEEN = re.compile(r"(?:没看到|没看见|未见|没发现|没有看到|没瞧见|不见)")
GUESS = re.compile(r"(?:可能|大概|也许|估计|像是|好像|疑似|说不定|应该)")
OBSERVE = re.compile(
    r"(?:看见|看到|瞧见|目击|发现|听见|听到|拍到|遇见|见(?=[一两三四五六七八九十\d]+[只条群]))"
)
# 转述动词：“小周也在那边报过”——整句是转述的一次目击
REPORT_VERB = re.compile(r"(?:报过|报告|上报|提到|反映|讲过)")

# 归属标记：“是小齐跟群里说的”“小周也在那边报过”“老李说他见过”……
# 先定位转述动词，再向前取最靠右的合法人名（避免时间词抢占匹配）。
_N = r"(?:(?!跟|在|说|的|也|又|就|有|没|不|我|你|他|她|它|那|这|是|听|据)[一-龥])"
REPORT_VERBS = re.compile(
    r"(?:跟群里说|群里说|说了|说的|讲过|提到|报过|报告过|上报|告诉|说(?=[他她它我你]))"
)
NAME_CAND = re.compile(rf"[小老]{_N}{{1,2}}|{_N}{{2,3}}")
ANON_HEARSAY = re.compile(r"(?:听说|据说|有人说|群众反映|群里看到|【转发】|转发)")
FIRST_PERSON = re.compile(r"(?:我|咱|自己|亲自)")

COUNT = re.compile(r"(?P<n>[一两三四五六七八九十\d]+)\s*只")
REPEAT = re.compile(r"(?:又|再次|再度|还是)")
ANAPHORA = re.compile(r"(?:那边|那里|那儿|原地|老地方|同一地方|同一个地方)")
SPEAKER_REL = re.compile(r"(?:对岸|对面|河对岸)")
DIRECTION = re.compile(r"(上游|下游|往东|往西|朝南|朝北|向东|向西|桥东|桥西|东岸|西岸)")

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_TIME_WORD = re.compile(
    r"(?:[上下本这]?(?:周|星期|礼拜)[一二三四五六日天]?|今[儿早晚晨]?|昨[儿天日]?|明天|前天|"
    r"[上下]午|早上|晚上|凌晨|傍晚|中午|[一二两三四五六七八九十\d]+天前)"
)


def _is_name(name: str | None, gaz: Gazetteer) -> bool:
    if not name:
        return False
    if name in {"我们", "你们", "他们", "大家", "有人", "群众", "自己", "别人", "群里"}:
        return False
    if _TIME_WORD.fullmatch(name):
        return False
    if gaz.is_known_place(name):
        return False
    return True


# 纯归属分句（“是小齐跟群里说的”）：整句只有归属作用，回溯影响其他观察分句
PURE_ATTR = re.compile(
    r"^(?:是|听|据)?[一-龥]{0,6}?(?:跟群里说|群里说|说了|说的|讲过|提到|报过|报告过|上报|告诉|说)$"
)


def _find_attribution(sent: str, gaz: Gazetteer) -> str | None:
    """在句中找“是X说的/X报过”类归属，返回归一后的来源人名。"""
    for vm in REPORT_VERBS.finditer(sent):
        window = sent[max(0, vm.start() - 9):vm.start()]
        window = re.split(r"[，；]", window)[-1]
        cands = [m for m in NAME_CAND.finditer(window) if _is_name(m.group(0), gaz)]
        if cands:
            best = max(cands, key=lambda m: (m.end(), len(m.group(0))))
            return gaz.canonical_person(best.group(0))
    return None


def resolve_time(text: str, received: datetime) -> tuple[str | None, DateRange | None]:
    """把“昨儿”“上星期”“周三”等引用时间解析为日期区间。"""
    d = received.date()

    m = re.search(r"上(?:周|星期|礼拜)([一二三四五六日天])?", text)
    if m:
        monday = d - timedelta(days=d.weekday() + 7)
        if m.group(1):
            day = monday + timedelta(days=_WEEKDAY[m.group(1)])
            return m.group(0), DateRange(day, day)
        return m.group(0), DateRange(monday, monday + timedelta(days=6))

    m = re.search(r"(?<![上本这])(?:周|星期|礼拜)([一二三四五六日天])", text)
    if m:
        monday = d - timedelta(days=d.weekday())
        day = monday + timedelta(days=_WEEKDAY[m.group(1)])
        if day > d:
            day -= timedelta(days=7)
        return m.group(0), DateRange(day, day)

    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = d.year if (month, day) <= (d.month, d.day) else d.year - 1
        day = datetime(year, month, day).date()
        return m.group(0), DateRange(day, day)

    m = re.search(r"([一二两三四五六七八九十\d]+)天前", text)
    if m:
        token = m.group(1)
        n = int(token) if token.isdigit() else _CN_NUM.get(token, 0)
        day = d - timedelta(days=n)
        return m.group(0), DateRange(day, day)

    for token, delta in (("前天", -2),):
        if token in text:
            day = d + timedelta(days=delta)
            return token, DateRange(day, day)
    m = re.search(r"昨儿|昨天|昨日", text)
    if m:
        day = d - timedelta(days=1)
        return m.group(0), DateRange(day, day)
    m = re.search(r"今儿|今天|今早|今晨|今晚|刚才|凌晨", text)
    if m:
        return m.group(0), DateRange(d, d)
    return None, None


def _classify(clause: str) -> Epistemic | None:
    """分句级认识类型；非观察分句返回 None。"""
    if NOT_SEEN.search(clause):
        return Epistemic.NOT_SEEN
    if OBSERVE.search(clause) or REPORT_VERB.search(clause):
        return Epistemic.GUESS if GUESS.search(clause) else Epistemic.FIRSTHAND
    if GUESS.search(clause):
        return Epistemic.GUESS
    return None


def _count(clause: str) -> int | None:
    m = COUNT.search(clause)
    if not m:
        return None
    token = m.group("n")
    return int(token) if token.isdigit() else _CN_NUM.get(token)


def extract_statements(report: Report, gaz: Gazetteer, make_id) -> list[Statement]:
    """从报告文本辨出全部陈述（保持报告内顺序）。"""
    statements: list[Statement] = []
    prev_node: str | None = None
    last_time: tuple[str | None, DateRange | None] = (None, None)
    for sent in SENT_SPLIT.split(report.text):
        sent = sent.strip()
        if not sent:
            continue

        # 归属作用域：纯归属分句（“是小齐跟群里说的”）回溯影响整句；
        # 内嵌归属（“小周也在那边报过”）只覆盖自身及后续分句。
        clauses = [c.strip() for c in CLAUSE_SPLIT.split(sent) if c.strip()]
        pure_attr: str | None = None
        clause_attrs: dict[int, str] = {}
        for i, clause in enumerate(clauses):
            name = _find_attribution(clause, gaz)
            if not name:
                continue
            if PURE_ATTR.match(clause):
                pure_attr = name
            else:
                clause_attrs[i] = name
        attr_by_clause: dict[int, str] = {}
        if pure_attr:
            attr_by_clause = {i: pure_attr for i in range(len(clauses))}
        else:
            current: str | None = None
            for i in range(len(clauses)):
                if i in clause_attrs:
                    current = clause_attrs[i]
                if current:
                    attr_by_clause[i] = current
        if not attr_by_clause and ANON_HEARSAY.search(sent):
            attr_by_clause = {i: "不明" for i in range(len(clauses))}

        qualifiers = tuple(DIRECTION.findall(sent))
        depends = bool(SPEAKER_REL.search(sent))
        context = gaz.find(sent)

        for i, clause in enumerate(clauses):
            ep = _classify(clause)
            if ep is None:
                # 非观察分句：仍可能带时间说法，供后续分句继承
                t_raw, t_range = resolve_time(clause, report.received_at)
                if t_range is not None:
                    last_time = (t_raw, t_range)
                continue
            attr = attr_by_clause.get(i)
            if (
                attr is None
                and report.kind is ReportKind.FORWARD
                and not FIRST_PERSON.search(clause)
            ):
                attr = "不明"  # 转发件无归属标记、又非第一人称时按转述处理
            if attr is not None and ep is not Epistemic.NOT_SEEN:
                ep = Epistemic.HEARSAY

            c_raw, c_range = resolve_time(clause, report.received_at)
            if c_range is not None:
                last_time = (c_raw, c_range)
            raw = c_raw or last_time[0]
            tr = c_range or last_time[1]
            assumed = False
            if tr is None:
                day = report.received_at.date()
                tr, assumed = DateRange(day, day), True

            node = gaz.find(clause)
            if node is not None:
                node_id: str | None = node.id
            elif ANAPHORA.search(clause):
                node_id = prev_node  # “那边/那里”回指前文位置
            elif context is not None:
                node_id = context.id
            else:
                node_id = None

            chain = (attr, report.author) if ep is Epistemic.HEARSAY else (report.author,)
            statements.append(
                Statement(
                    id=make_id(),
                    report_id=report.id,
                    seq=len(statements),
                    text=clause,
                    speaker=report.author,
                    epistemic=ep,
                    source_chain=tuple(chain),
                    quoted_time_raw=raw,
                    time_range=tr,
                    time_assumed=assumed,
                    location=LocationRef(
                        node_id=node_id,
                        qualifiers=qualifiers,
                        depends_on_speaker=depends,
                        raw=clause,
                    ),
                    count=_count(clause),
                    is_repeat=bool(REPEAT.search(clause)),
                )
            )
            if node_id:
                prev_node = node_id
    return statements
