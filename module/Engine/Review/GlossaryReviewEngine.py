from __future__ import annotations

import math
import threading
import time
from typing import Any

from base.Base import Base
from base.LogManager import LogManager
from module.Config import Config
from module.Engine.Engine import Engine
from module.Engine.Review.GlossaryReviewTask import GlossaryReviewTask
from module.Engine.Review.ReviewModels import GlossaryReviewResult
from module.Engine.TaskRunnerLifecycle import TaskRunnerLifecycle
from module.Localizer.Localizer import Localizer


class GlossaryReviewEngine(Base):
    """术语表 AI 审校引擎：管理术语审校任务的生命周期。

    从 UI 层接收术语条目列表，按批调度 GlossaryReviewTask，
    汇总结果，通过事件总线向 UI 推送进度和终态。
    """

    # 审校自动重试上限
    AUTO_RETRY_LIMIT: int = 3

    def __init__(self) -> None:
        super().__init__()

        # 停止标记
        self.stop_requested: bool = False
        self.lock: threading.Lock = threading.Lock()

        # 审校结果存储（内存中，UI 读取用）
        self.results: list[GlossaryReviewResult] = []

        # 注册事件
        self.subscribe(Base.Event.GLOSSARY_REVIEW_TASK, self.review_event)
        self.subscribe(Base.Event.GLOSSARY_REVIEW_REQUEST_STOP, self.stop_event)

    def should_stop(self) -> bool:
        """检查是否收到停止请求。"""
        with self.lock:
            return self.stop_requested

    def mark_stop_requested(self) -> None:
        """标记停止请求。"""
        with self.lock:
            self.stop_requested = True

    def get_results(self) -> list[GlossaryReviewResult]:
        """获取当前所有审校结果（线程安全）。"""
        with self.lock:
            return list(self.results)

    def review_event(self, event: Base.Event, data: dict) -> None:
        """响应术语表审校任务事件（入口）。"""
        sub_event = data.get("sub_event")
        if sub_event == Base.SubEvent.REQUEST:
            self.review_request(data)

    def stop_event(self, event: Base.Event, data: dict) -> None:
        """响应停止术语表审校请求。"""
        sub_event = data.get("sub_event")
        if sub_event == Base.SubEvent.REQUEST:
            TaskRunnerLifecycle.request_stop(
                self,
                stop_event=Base.Event.GLOSSARY_REVIEW_REQUEST_STOP,
                mark_stop_requested=self.mark_stop_requested,
            )

    def review_request(self, data: dict) -> None:
        """处理术语表审校请求。"""
        entries: list[dict[str, Any]] = data.get("entries", [])
        if not entries:
            self.emit(
                Base.Event.TOAST,
                {
                    "type": Base.ToastType.WARNING,
                    "message": Localizer.get().glossary_review_no_entries,
                },
            )
            return

        # 重置状态
        with self.lock:
            self.stop_requested = False
            self.results = []

        # 启动后台任务
        TaskRunnerLifecycle.start_background_run(
            self,
            busy_status=Base.TaskStatus.REVIEWING,
            task_event=Base.Event.GLOSSARY_REVIEW_TASK,
            mode=Base.TranslationMode.NEW,
            worker=lambda: self.run_review(entries),
        )

    def run_review(self, entries: list[dict[str, Any]]) -> None:
        """后台线程：执行术语表审校任务主循环（按批）。"""
        final_status = "FAILED"

        try:
            config = Config().load()

            # 解析审校使用的模型
            model = self.resolve_model(config)
            if model is None:
                self.emit(
                    Base.Event.TOAST,
                    {
                        "type": Base.ToastType.WARNING,
                        "message": Localizer.get().alert_no_active_model,
                    },
                )
                return

            # 确定批大小和重试上限
            batch_size = max(1, config.glossary_review_batch_size)
            max_retries = config.max_round
            if max_retries <= 0:
                max_retries = self.AUTO_RETRY_LIMIT

            total_batches = math.ceil(len(entries) / batch_size)
            reviewed_batches = 0

            # 按批处理
            for batch_idx in range(total_batches):
                if self.should_stop():
                    final_status = "STOPPED"
                    break

                start = batch_idx * batch_size
                end = min(start + batch_size, len(entries))
                batch_entries = entries[start:end]

                # 执行审校（支持重试）
                batch_results = self.review_batch_with_retry(
                    config=config,
                    model=model,
                    entries=batch_entries,
                    max_retries=max_retries,
                )

                # 汇总结果
                with self.lock:
                    self.results.extend(batch_results)

                reviewed_batches += 1

                # 推送进度
                self.emit(
                    Base.Event.GLOSSARY_REVIEW_PROGRESS,
                    {
                        "current_batch": reviewed_batches,
                        "total_batches": total_batches,
                        "results": [self.result_to_dict(r) for r in batch_results],
                    },
                )
            else:
                final_status = "SUCCESS"

        except Exception as e:
            LogManager.get().error("Glossary review task failed", e)
            final_status = "FAILED"
        finally:
            Engine.get().set_status(Base.TaskStatus.IDLE)
            TaskRunnerLifecycle.emit_terminal_toast(self, final_status=final_status)
            TaskRunnerLifecycle.emit_task_done(
                self,
                task_event=Base.Event.GLOSSARY_REVIEW_TASK,
                final_status=final_status,
            )

    def review_batch_with_retry(
        self,
        *,
        config: Config,
        model: dict[str, Any],
        entries: list[dict[str, Any]],
        max_retries: int,
    ) -> list[GlossaryReviewResult]:
        """审校一批术语条目，支持重试。"""
        for attempt in range(max_retries):
            if self.should_stop():
                return [
                    GlossaryReviewResult(
                        src=e.get("src", ""),
                        dst=e.get("dst", ""),
                        verdict=GlossaryReviewResult.Verdict.KEEP,
                        reason="Review stopped by user",
                    )
                    for e in entries
                ]

            task = GlossaryReviewTask(
                config=config,
                model=model,
                entries=entries,
                stop_checker=self.should_stop,
            )

            try:
                results = task.start()
                # 只在所有条目都未被解析（全部是"未返回结果"的兜底值）时才重试，
                # AI 合法地判定全部 KEEP 并附带 reason 属于正常结果，不应重试
                all_fallback = all(
                    r.verdict == GlossaryReviewResult.Verdict.KEEP
                    and r.reason == "No review result returned for this entry"
                    for r in results
                )
                if not all_fallback:
                    return results

                # 全部条目均未解析到有效结果，可能是响应格式异常
                if attempt < max_retries - 1:
                    LogManager.get().warning(
                        f"Glossary review attempt {attempt + 1} returned no parsed results, retrying..."
                    )
                    time.sleep(1)
                    continue

                return results
            except Exception as e:
                if attempt < max_retries - 1:
                    LogManager.get().warning(
                        f"Glossary review attempt {attempt + 1} exception, retrying...",
                        e,
                    )
                    time.sleep(1)
                    continue

                return [
                    GlossaryReviewResult(
                        src=entry.get("src", ""),
                        dst=entry.get("dst", ""),
                        verdict=GlossaryReviewResult.Verdict.KEEP,
                        reason=str(e),
                    )
                    for entry in entries
                ]

        # 不应到达此处
        return [
            GlossaryReviewResult(
                src=e.get("src", ""),
                dst=e.get("dst", ""),
                verdict=GlossaryReviewResult.Verdict.KEEP,
                reason="Max retries exceeded",
            )
            for e in entries
        ]

    @staticmethod
    def resolve_model(config: Config) -> dict[str, Any] | None:
        """解析审校使用的模型：优先审校专用模型，否则使用激活模型。"""
        if config.review_model_id:
            model = config.get_model(config.review_model_id)
            if model is not None:
                return model
        return config.get_active_model()

    @staticmethod
    def result_to_dict(result: GlossaryReviewResult) -> dict[str, str]:
        """将审校结果转为字典，用于事件传输。"""
        return {
            "src": result.src,
            "dst": result.dst,
            "verdict": result.verdict.value,
            "suggested_dst": result.suggested_dst,
            "reason": result.reason,
        }
