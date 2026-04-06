import json
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from base.LogManager import LogManager

# 会话文件名，存放在 .lg 工程文件同级目录
SESSION_FILE_NAME: str = ".lg_review_session.json"


@dataclass
class ReviewSessionState:
    """可序列化的审校会话快照，用于跨重启恢复。"""

    # 原始审校条目的 item_id 列表（按审校顺序）
    item_ids: list[int] = field(default_factory=list)

    # 已审校数量（从 0 开始）
    reviewed_count: int = 0

    # 统计计数
    pass_count: int = 0
    fix_count: int = 0
    fail_count: int = 0
    error_count: int = 0

    # 文件范围（空 = 全部）
    selected_files: list[str] = field(default_factory=list)

    # 输出日志条目 [(verdict, text), ...]
    output_entries: list[tuple[str, str]] = field(default_factory=list)

    # 失败行 ID（用于自动重试）
    failed_item_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 安全的字典。"""
        return {
            "item_ids": self.item_ids,
            "reviewed_count": self.reviewed_count,
            "pass_count": self.pass_count,
            "fix_count": self.fix_count,
            "fail_count": self.fail_count,
            "error_count": self.error_count,
            "selected_files": self.selected_files,
            "output_entries": self.output_entries,
            "failed_item_ids": self.failed_item_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewSessionState":
        """从字典反序列化。"""
        raw_entries = data.get("output_entries", [])
        valid_entries: list[tuple[str, str]] = []
        skipped = 0
        for e in raw_entries:
            if isinstance(e, (list, tuple)) and len(e) == 2:
                valid_entries.append(tuple(e))
            else:
                skipped += 1
        if skipped > 0:
            LogManager.get().warning(
                f"Skipped {skipped} malformed output entries during review session restore"
            )

        return cls(
            item_ids=data.get("item_ids", []),
            reviewed_count=data.get("reviewed_count", 0),
            pass_count=data.get("pass_count", 0),
            fix_count=data.get("fix_count", 0),
            fail_count=data.get("fail_count", 0),
            error_count=data.get("error_count", 0),
            selected_files=data.get("selected_files", []),
            output_entries=valid_entries,
            failed_item_ids=data.get("failed_item_ids", []),
        )


def get_session_path(lg_path: str) -> str:
    """根据 .lg 工程路径推导审校会话文件路径。"""
    return os.path.join(os.path.dirname(lg_path), SESSION_FILE_NAME)


def save_session(lg_path: str, state: ReviewSessionState) -> None:
    """将审校会话状态写入工程目录下的 JSON 文件。"""
    path = get_session_path(lg_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
    except Exception as e:
        LogManager.get().warning("Failed to save review session state", e)


def load_session(lg_path: str) -> ReviewSessionState | None:
    """从工程目录加载审校会话状态，不存在或损坏时返回 None。"""
    path = get_session_path(lg_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        state = ReviewSessionState.from_dict(data)
        # 基本有效性校验
        if not state.item_ids or state.reviewed_count < 0:
            return None
        if state.reviewed_count >= len(state.item_ids):
            # 会话已完成，不需要恢复
            return None
        return state
    except Exception as e:
        LogManager.get().warning("Failed to load review session state", e)
        return None


def delete_session(lg_path: str) -> None:
    """删除审校会话文件（会话完成或重置后调用）。"""
    path = get_session_path(lg_path)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        # 删除失败无害，下次启动时会被覆盖或忽略
        pass
