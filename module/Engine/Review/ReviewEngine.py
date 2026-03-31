from __future__ import annotations

import threading
import time
from typing import Any

from base.Base import Base
from base.LogManager import LogManager
from model.Item import Item
from module.Config import Config
from module.Data.DataManager import DataManager
from module.Engine.Engine import Engine
from module.Engine.Review.ReviewModels import ReviewProgressSnapshot
from module.Engine.Review.ReviewModels import ReviewResult
from module.Engine.Review.ReviewModels import ReviewVerdict
from module.Engine.Review.ReviewTask import ReviewTask
from module.Engine.TaskRunnerLifecycle import TaskRunnerLifecycle
from module.GameCapture.GameCapture import GameCapture
from module.Localizer.Localizer import Localizer
from module.QualityRule.QualityRuleSnapshot import QualityRuleSnapshot


class ReviewEngine(Base):
    """AI 审校引擎：管理审校任务的生命周期。

    从数据层加载条目，按批调度 ReviewTask，汇总结果，
    通过事件总线向 UI 推送进度和终态。
    """

    # 审校自动重试上限（当 config.max_round <= 0 时的兜底）
    AUTO_RETRY_LIMIT: int = 3
    # 发送热键后等待游戏画面更新的延迟（秒）
    HOTKEY_SETTLE_DELAY: float = 0.5

    # 用户审批决定常量
    DECISION_APPROVE: str = "approve"
    DECISION_SKIP: str = "skip"
    DECISION_DENY: str = "deny"
    DECISION_RETRY: str = "retry"

    def __init__(self) -> None:
        super().__init__()

        # 停止标记
        self.stop_requested: bool = False
        self.lock: threading.Lock = threading.Lock()

        # 审校结果存储（内存中，UI 读取用）
        self.results: list[ReviewResult] = []
        self.progress: ReviewProgressSnapshot = ReviewProgressSnapshot()

        # 手动审批同步机制：引擎线程阻塞等待用户决定
        self.approval_event: threading.Event = threading.Event()
        self.user_decision: str = ""

        # 用户发起的即时暂停标记：下一条结果强制走手动审批
        self.pause_next: bool = False

        # 注册事件
        self.subscribe(Base.Event.REVIEW_TASK, self.review_event)
        self.subscribe(Base.Event.REVIEW_REQUEST_STOP, self.stop_event)
        self.subscribe(Base.Event.REVIEW_USER_DECISION, self.on_user_decision)
        self.subscribe(Base.Event.REVIEW_PAUSE_NEXT, self.on_pause_next)

    def get_results(self) -> list[ReviewResult]:
        """获取当前所有审校结果（线程安全）。"""
        with self.lock:
            return list(self.results)

    def get_progress(self) -> ReviewProgressSnapshot:
        """获取当前进度快照（线程安全）。"""
        with self.lock:
            return self.progress

    def should_stop(self) -> bool:
        """检查是否收到停止请求。"""
        with self.lock:
            return self.stop_requested

    def mark_stop_requested(self) -> None:
        """标记停止请求，同时唤醒可能阻塞在审批等待中的引擎线程。"""
        with self.lock:
            self.stop_requested = True
        self.approval_event.set()

    def on_user_decision(self, event: Base.Event, data: dict) -> None:
        """响应用户审批决定（approve/deny/retry），唤醒引擎线程。"""
        decision = data.get("decision", "")
        with self.lock:
            self.user_decision = decision
        self.approval_event.set()

    def on_pause_next(self, event: Base.Event, data: dict) -> None:
        """响应用户即时暂停请求：下一条审校结果强制走手动审批。"""
        with self.lock:
            self.pause_next = True

    def review_event(self, event: Base.Event, data: dict) -> None:
        """响应审校任务事件（入口）。"""
        sub_event = data.get("sub_event")
        if sub_event == Base.SubEvent.REQUEST:
            self.review_request(data)

    def stop_event(self, event: Base.Event, data: dict) -> None:
        """响应停止审校请求。"""
        sub_event = data.get("sub_event")
        if sub_event == Base.SubEvent.REQUEST:
            TaskRunnerLifecycle.request_stop(
                self,
                stop_event=Base.Event.REVIEW_REQUEST_STOP,
                mark_stop_requested=self.mark_stop_requested,
            )

    def review_request(self, data: dict) -> None:
        """处理审校请求。"""
        dm = DataManager.get()
        if not TaskRunnerLifecycle.ensure_project_loaded(self, dm=dm):
            return

        # 获取待审校条目
        items = data.get("items", [])
        if not items:
            TaskRunnerLifecycle.emit_no_items_warning(self)
            return

        # 重置状态
        with self.lock:
            self.stop_requested = False
            self.results = []
            self.progress = ReviewProgressSnapshot(total_line=len(items))
            self.pause_next = False

        # 启动后台任务
        TaskRunnerLifecycle.start_background_run(
            self,
            busy_status=Base.TaskStatus.REVIEWING,
            task_event=Base.Event.REVIEW_TASK,
            mode=Base.TranslationMode.NEW,
            worker=lambda: self.run_review(items),
        )

    def run_review(self, items: list[Item]) -> None:
        """后台线程：执行审校任务主循环。"""
        final_status = "FAILED"
        capturer: GameCapture | None = None

        try:
            config = Config().load()

            # 解析审校使用的模型
            model = self.resolve_review_model(config)
            if model is None:
                self.emit(
                    Base.Event.TOAST,
                    {
                        "type": Base.ToastType.WARNING,
                        "message": Localizer.get().alert_no_active_model,
                    },
                )
                return

            # 创建质量规则快照
            quality_snapshot = QualityRuleSnapshot.capture()

            # 确定重试上限
            max_retries = config.max_round
            if max_retries <= 0:
                max_retries = self.AUTO_RETRY_LIMIT

            approval_mode = config.review_approval_mode

            # 初始化游戏窗口捕获（仅截图模式在逐行循环中自动触发）
            capture_enabled = (
                config.review_capture_enable
                and config.review_capture_window
                and GameCapture.is_available()
            )
            if capture_enabled:
                capturer = GameCapture()

            # 逐条审校（每条携带上文）
            total = len(items)
            reviewed = 0
            pass_count = 0
            fix_count = 0
            fail_count = 0
            error_count = 0

            # 使用 while 而非 for：支持重试时不推进索引（continue 回到同一条目）
            i = 0
            while i < total:
                if self.should_stop():
                    final_status = "STOPPED"
                    break

                item = items[i]

                # 收集上文
                preceding_count = config.review_preceding_lines
                start_idx = max(0, i - preceding_count)
                precedings = items[start_idx:i]

                # 自动推进游戏并捕获截图
                screenshot_b64 = ""
                if capture_enabled and capturer is not None:
                    if (
                        config.review_capture_auto_advance
                        and config.review_capture_hotkey
                    ):
                        capturer.send_hotkey(
                            config.review_capture_window,
                            config.review_capture_hotkey,
                        )
                        time.sleep(self.HOTKEY_SETTLE_DELAY)

                    if config.review_capture_mode == Config.CaptureMode.IMAGE:
                        screenshot_b64 = capturer.capture_screenshot(
                            config.review_capture_window
                        )

                # 执行审校（支持重试）
                result = self.review_single_item_with_retry(
                    config=config,
                    model=model,
                    item=item,
                    precedings=precedings,
                    quality_snapshot=quality_snapshot,
                    max_retries=max_retries,
                    screenshot_b64=screenshot_b64,
                )

                # 根据审批模式决定是否等待用户操作
                decision = self.resolve_approval(
                    approval_mode,
                    result,
                    item,
                    total,
                    reviewed + 1,
                )

                # 用户/自动决定为"重试"时不推进索引，重新审校同一条
                if decision == self.DECISION_RETRY:
                    continue

                # 若被批准且有修正，则写回数据层；跳过时不应用修正
                if decision == self.DECISION_APPROVE:
                    self.apply_fix_if_needed(result, item)

                # 跳过视为通过：用户确认原文无需修改，按 PASS 统计
                skipped = decision == self.DECISION_SKIP

                # 汇总结果
                with self.lock:
                    self.results.append(result)

                reviewed += 1
                if skipped or result.verdict == ReviewVerdict.PASS:
                    pass_count += 1
                elif result.verdict == ReviewVerdict.FIX:
                    fix_count += 1
                elif result.verdict == ReviewVerdict.FAIL:
                    fail_count += 1
                else:
                    error_count += 1

                # 更新进度（不含待批信息，表示该行已完成）
                snapshot = ReviewProgressSnapshot(
                    total_line=total,
                    reviewed_line=reviewed,
                    pass_line=pass_count,
                    fix_line=fix_count,
                    fail_line=fail_count,
                    error_line=error_count,
                )
                with self.lock:
                    self.progress = snapshot

                self.emit(
                    Base.Event.REVIEW_PROGRESS,
                    {
                        "total_line": total,
                        "reviewed_line": reviewed,
                        "pass_line": pass_count,
                        "fix_line": fix_count,
                        "fail_line": fail_count,
                        "error_line": error_count,
                        "result": {
                            "item_id": result.item_id,
                            "verdict": str(result.verdict),
                            "corrected": result.corrected,
                            "reason": result.reason,
                            "original_dst": result.original_dst,
                            "src": item.src,
                        },
                        "approved": decision == self.DECISION_APPROVE,
                    },
                )

                i += 1
            else:
                final_status = "SUCCESS"

        except Exception as e:
            LogManager.get().error("Review task failed", e)
            final_status = "FAILED"
        finally:
            Engine.get().set_status(Base.TaskStatus.IDLE)
            TaskRunnerLifecycle.emit_terminal_toast(self, final_status=final_status)
            TaskRunnerLifecycle.emit_task_done(
                self,
                task_event=Base.Event.REVIEW_TASK,
                final_status=final_status,
            )

    # ==================== 审批决策 ====================

    def resolve_approval(
        self,
        approval_mode: str,
        result: ReviewResult,
        item: Item,
        total: int,
        current: int,
    ) -> str:
        """根据审批模式决定当前结果的处理方式。

        MANUAL: FIX/FAIL 结果阻塞等待用户决定。
        AUTO_ACCEPT: 所有结果自动批准。
        AUTO_PAUSE_ON_FAIL: FIX 自动批准，FAIL 阻塞等待用户决定。
        pause_next: 无论模式，下一条强制走手动审批（一次性标记）。
        PASS/ERROR 在所有模式下自动通过，不阻塞。
        """
        # 检查一次性暂停标记（用户在自动模式下点了 Pause）
        force_pause = False
        with self.lock:
            if self.pause_next:
                self.pause_next = False
                force_pause = True

        if result.verdict in (ReviewVerdict.PASS, ReviewVerdict.ERROR):
            # PASS 和 ERROR 在所有模式下都自动通过
            # 即使 force_pause 也不阻塞这两种无操作性的结果
            return self.DECISION_APPROVE

        # force_pause 对 FIX/FAIL 生效
        if force_pause:
            return self.wait_for_user_decision(result, item, total, current)

        needs_manual = False

        if approval_mode == Config.ReviewApprovalMode.MANUAL:
            needs_manual = True
        elif approval_mode == Config.ReviewApprovalMode.AUTO_PAUSE_ON_FAIL:
            # FIX 自动接受，FAIL 需要人工
            if result.verdict == ReviewVerdict.FAIL:
                needs_manual = True

        if not needs_manual:
            return self.DECISION_APPROVE

        # 阻塞等待用户决定
        return self.wait_for_user_decision(result, item, total, current)

    def wait_for_user_decision(
        self,
        result: ReviewResult,
        item: Item,
        total: int,
        current: int,
    ) -> str:
        """向 UI 发送待批事件，然后阻塞等待用户通过 REVIEW_USER_DECISION 回复。"""
        # 发送带有 awaiting_approval 标记的进度事件，UI 据此展示待批状态
        self.emit(
            Base.Event.REVIEW_PROGRESS,
            {
                "total_line": total,
                "reviewed_line": current - 1,
                "awaiting_approval": True,
                "result": {
                    "item_id": result.item_id,
                    "verdict": str(result.verdict),
                    "corrected": result.corrected,
                    "reason": result.reason,
                    "original_dst": result.original_dst,
                    "src": item.src,
                },
            },
        )

        # 阻塞直到用户决定或停止请求
        with self.lock:
            self.user_decision = ""
            self.approval_event.clear()

        while not self.should_stop():
            if self.approval_event.wait(timeout=0.5):
                break

        with self.lock:
            decision = self.user_decision

        if self.should_stop():
            return self.DECISION_DENY

        if decision in (
            self.DECISION_APPROVE,
            self.DECISION_SKIP,
            self.DECISION_DENY,
            self.DECISION_RETRY,
        ):
            return decision

        return self.DECISION_DENY

    def apply_fix_if_needed(self, result: ReviewResult, item: Item) -> None:
        """若结果为 FIX 且有修正文本，将修正写回数据层。

        注意：会直接修改 item.dst，使后续上文引用的也是修正后的译文。
        """
        if result.verdict != ReviewVerdict.FIX or not result.corrected:
            return

        item.dst = result.corrected
        try:
            DataManager.get().save_item(item)
        except Exception as e:
            LogManager.get().warning(
                f"Failed to apply review fix for item {result.item_id}", e
            )

    def review_single_item_with_retry(
        self,
        *,
        config: Config,
        model: dict[str, Any],
        item: Item,
        precedings: list[Item],
        quality_snapshot: QualityRuleSnapshot | None,
        max_retries: int,
        screenshot_b64: str = "",
    ) -> ReviewResult:
        """审校单条条目，支持重试。"""
        for attempt in range(max_retries):
            if self.should_stop():
                return ReviewResult(
                    item_id=item.id or 0,
                    verdict=ReviewVerdict.ERROR,
                    reason="Review stopped by user",
                    original_dst=item.dst,
                )

            task = ReviewTask(
                config=config,
                model=model,
                items=[item],
                precedings=precedings,
                quality_snapshot=quality_snapshot,
                stop_checker=self.should_stop,
                screenshot_b64=screenshot_b64,
            )

            try:
                results = task.start()
                if results and results[0].verdict != ReviewVerdict.ERROR:
                    return results[0]

                # ERROR 结果：如果还有重试次数就继续
                if attempt < max_retries - 1:
                    LogManager.get().warning(
                        f"Review attempt {attempt + 1} failed for item {item.id}, retrying..."
                    )
                    time.sleep(1)  # 短暂等待后重试
                    continue

                return (
                    results[0]
                    if results
                    else ReviewResult(
                        item_id=item.id or 0,
                        verdict=ReviewVerdict.ERROR,
                        reason="All review attempts failed",
                        original_dst=item.dst,
                    )
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    LogManager.get().warning(
                        f"Review attempt {attempt + 1} exception for item {item.id}, retrying...",
                        e,
                    )
                    time.sleep(1)
                    continue

                return ReviewResult(
                    item_id=item.id or 0,
                    verdict=ReviewVerdict.ERROR,
                    reason=str(e),
                    original_dst=item.dst,
                )

        # 不应到达此处
        return ReviewResult(
            item_id=item.id or 0,
            verdict=ReviewVerdict.ERROR,
            reason="Max retries exceeded",
            original_dst=item.dst,
        )

    def resolve_review_model(self, config: Config) -> dict[str, Any] | None:
        """解析审校使用的模型：优先审校专用模型，否则使用激活模型。"""
        if config.review_model_id:
            model = config.get_model(config.review_model_id)
            if model is not None:
                return model

        return config.get_active_model()
