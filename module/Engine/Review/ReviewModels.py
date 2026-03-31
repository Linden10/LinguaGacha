import dataclasses
from enum import StrEnum


class ReviewVerdict(StrEnum):
    """审校结论枚举。"""

    PASS = "PASS"  # 译文准确，无需修改
    FIX = "FIX"  # 译文有问题但可修正
    FAIL = "FAIL"  # 译文存在严重问题
    PENDING = "PENDING"  # 尚未审校
    ERROR = "ERROR"  # 审校请求出错


@dataclasses.dataclass
class ReviewResult:
    """单行审校结果。"""

    item_id: int  # 对应 Item.id
    verdict: ReviewVerdict = ReviewVerdict.PENDING
    corrected: str = ""  # 校正后的译文（PASS 时为空）
    reason: str = ""  # 校正原因
    original_dst: str = ""  # 校正前的译文（用于撤销）


@dataclasses.dataclass
class GlossaryReviewResult:
    """术语表审校结果（单条）。"""

    class Verdict(StrEnum):
        KEEP = "KEEP"  # 保留不变
        FIX = "FIX"  # 修正
        REMOVE = "REMOVE"  # 移除

    src: str
    dst: str
    verdict: Verdict = Verdict.KEEP
    suggested_dst: str = ""
    reason: str = ""


@dataclasses.dataclass(frozen=True)
class ReviewHistoryEntry:
    """审校历史记录条目，用于 UI 的 Undo/Redo 功能。

    每当一条审校结果被批准（approved）且包含修正（FIX），
    就生成一条历史记录，保存修正前后的译文和对应的 Item 引用。
    """

    item_id: int  # 对应 Item.id
    src: str  # 原文（展示用）
    original_dst: str  # 修正前的译文
    corrected: str  # 修正后的译文
    verdict: str  # 审校结论字符串
    reason: str  # 修正原因


@dataclasses.dataclass(frozen=True)
class ReviewProgressSnapshot:
    """审校进度快照，用于 UI 进度更新。"""

    total_line: int = 0
    reviewed_line: int = 0
    pass_line: int = 0
    fix_line: int = 0
    fail_line: int = 0
    error_line: int = 0
