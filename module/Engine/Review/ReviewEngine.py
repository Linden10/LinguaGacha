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
from module.Localizer.Localizer import Localizer
from module.QualityRule.QualityRuleSnapshot import QualityRuleSnapshot


class ReviewEngine(Base):
    """AI 审校引擎：管理审校任务的生命周期。

    从数据层加载条目，按批调度 ReviewTask，汇总结果，
    通过事件总线向 UI 推送进度和终态。
    """

    # 审校自动重试上限（当 config.max_round <= 0 时的兜底）
    AUTO_RETRY_LIMIT: int = 3

    def __init__(self) -> None:
        super().__init__()

        # 停止标记
        self.stop_requested: bool = False
        self.lock: threading.Lock = threading.Lock()

        # 审校结果存储（内存中，UI 读取用）
        self.results: list[ReviewResult] = []
        self.progress: ReviewProgressSnapshot = ReviewProgressSnapshot()

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
        """标记停止请求。"""
        with self.lock:
            self.stop_requested = True

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

        try:
            config = Config().load()
            dm = DataManager.get()

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
            quality_snapshot = QualityRuleSnapshot.capture(dm)

            # 确定重试上限
            max_retries = config.max_round
            if max_retries <= 0:
                max_retries = self.AUTO_RETRY_LIMIT

            # 逐条审校（每条携带上文）
            total = len(items)
            reviewed = 0
            pass_count = 0
            fix_count = 0
            fail_count = 0
            error_count = 0

            for i, item in enumerate(items):
                if self.should_stop():
                    final_status = "STOPPED"
                    break

                # 收集上文
                preceding_count = config.review_preceding_lines
                start_idx = max(0, i - preceding_count)
                precedings = items[start_idx:i]

                # 执行审校（支持重试）
                result = self.review_single_item_with_retry(
                    config=config,
                    model=model,
                    item=item,
                    precedings=precedings,
                    quality_snapshot=quality_snapshot,
                    max_retries=max_retries,
                )

                # 汇总结果
                with self.lock:
                    self.results.append(result)

                reviewed += 1
                if result.verdict == ReviewVerdict.PASS:
                    pass_count += 1
                elif result.verdict == ReviewVerdict.FIX:
                    fix_count += 1
                elif result.verdict == ReviewVerdict.FAIL:
                    fail_count += 1
                else:
                    error_count += 1

                # 更新进度
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
                    },
                )
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

    def review_single_item_with_retry(
        self,
        *,
        config: Config,
        model: dict[str, Any],
        item: Item,
        precedings: list[Item],
        quality_snapshot: QualityRuleSnapshot | None,
        max_retries: int,
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
