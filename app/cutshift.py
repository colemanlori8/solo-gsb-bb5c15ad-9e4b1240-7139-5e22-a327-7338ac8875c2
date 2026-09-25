"""删段后字幕接轴。

用户给出若干要从视频中删除的时间区间（毫秒精度），把字幕从原时间轴接到
删除后的新时间轴上：

1. 校验删除区间（起止必须为合法时间码且结束严格晚于开始），按开始时间排序后
   把**重叠或首尾相接**（前段结束 == 后段开始）的区间合并为一段；
2. 字幕与删除区间都按**半开区间** ``[start, end)`` 处理，逐条裁切：

   * 完全落入删除区间的字幕**丢弃**；
   * 跨越删除区间的字幕**拆成多个保留片段**（正文不变，各自重新编号）；
   * 与删除区间**恰好相接**的字幕完整保留——字幕结束恰为某段删除开始、或
     字幕开始恰为某段删除结束时，该字幕不裁不丢；

3. 每个保留片段整体**前移** ``[0, 片段开始)`` 内累计被删除的毫秒数（片段
   内部不含删除点，故片段时长不变）；删除起点为 0 时，其后的内容自然前移
   到 0，恰好相接的边界在接轴后仍然相接；
4. 全部保留片段按原顺序**从 1 连续编号**输出，并给出原序号到新序号的
   对应表（被丢弃的字幕对应空的新序号列表）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .srt import Cue
from .timecode import format_timecode, parse_timecode


class CutShiftError(ValueError):
    """删除区间参数不合法。"""


@dataclass(frozen=True)
class Cut:
    start: int
    end: int


@dataclass(frozen=True)
class Fragment:
    """一个保留片段：原时间轴区间与接轴后的新时间轴区间（两者时长相同）。"""

    original_start: int
    original_end: int
    new_start: int
    new_end: int


@dataclass(frozen=True)
class CueShift:
    """一条字幕的裁切结果；``fragments`` 为空表示该字幕被完全删除。"""

    cue_order: int
    fragments: tuple[Fragment, ...]

    @property
    def kept(self) -> bool:
        return bool(self.fragments)


def merge_cuts(cuts: list[Cut]) -> list[Cut]:
    """重叠或首尾相接的删除区间求并集，结果按开始时间升序且两两不相接。"""
    ordered = sorted(cuts, key=lambda c: (c.start, c.end))
    merged: list[Cut] = []
    for cut in ordered:
        if merged and cut.start <= merged[-1].end:
            prev = merged[-1]
            merged[-1] = Cut(prev.start, max(prev.end, cut.end))
        else:
            merged.append(cut)
    return merged


def parse_cuts(raw: list[tuple[str, str]]) -> list[Cut]:
    """解析并校验删除区间，返回合并后的区间列表（入参为空时返回空列表）。"""
    cuts: list[Cut] = []
    for pos, (start_text, end_text) in enumerate(raw, start=1):
        try:
            start_ms = parse_timecode(start_text)
        except ValueError as exc:
            raise CutShiftError(f"第 {pos} 个删除区间的开始时间无效：{exc}") from exc
        try:
            end_ms = parse_timecode(end_text)
        except ValueError as exc:
            raise CutShiftError(f"第 {pos} 个删除区间的结束时间无效：{exc}") from exc
        if end_ms <= start_ms:
            raise CutShiftError(
                f"第 {pos} 个删除区间无效：结束时间 {format_timecode(end_ms)}"
                f" 必须严格晚于开始时间 {format_timecode(start_ms)}。"
            )
        cuts.append(Cut(start_ms, end_ms))
    return merge_cuts(cuts)


def deleted_before(t: int, cuts: list[Cut]) -> int:
    """半开区间 ``[0, t)`` 内被删除的总毫秒数（``cuts`` 须已合并排序）。"""
    total = 0
    for cut in cuts:
        if cut.start >= t:
            break
        total += min(t, cut.end) - cut.start
    return total


def kept_fragments(start: int, end: int, cuts: list[Cut]) -> list[tuple[int, int]]:
    """把半开区间 ``[start, end)`` 中未被删除的部分切成若干保留片段。

    边界恰好相接不算相交：``end`` 等于某段删除的开始、或 ``start`` 等于某段
    删除的结束时，整条区间完整保留。
    """
    fragments: list[tuple[int, int]] = []
    cur = start
    for cut in cuts:
        if cut.end <= cur:
            continue  # 该删除区间整体在当前位置之前
        if cut.start >= end:
            break  # 之后的删除区间都不再相交
        if cut.start > cur:
            fragments.append((cur, cut.start))
        cur = cut.end
        if cur >= end:
            break
    if cur < end:
        fragments.append((cur, end))
    return fragments


def shift_cues(cues: list[Cue], cuts: list[Cut]) -> list[CueShift]:
    """逐条裁切字幕，并把保留片段前移到删除后的新时间轴。

    片段内部不含删除点，``deleted_before`` 在整个片段上为常数，故片段时长
    不变；同一字幕拆出的多个片段在新时间轴上首尾相接。
    """
    shifts: list[CueShift] = []
    for pos, cue in enumerate(cues, start=1):
        fragments: list[Fragment] = []
        for frag_start, frag_end in kept_fragments(cue.start, cue.end, cuts):
            shift = deleted_before(frag_start, cuts)
            fragments.append(
                Fragment(
                    original_start=frag_start,
                    original_end=frag_end,
                    new_start=frag_start - shift,
                    new_end=frag_end - shift,
                )
            )
        shifts.append(CueShift(cue_order=pos, fragments=tuple(fragments)))
    return shifts
