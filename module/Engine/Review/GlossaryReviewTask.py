from __future__ import annotations

import json
import re
from typing import Any
from typing import Callable

from base.BaseLanguage import BaseLanguage
from module.Config import Config
from module.Engine.Review.ReviewModels import GlossaryReviewResult
from module.Engine.TaskRequestExecutor import TaskRequestExecutor
from module.Engine.TaskRequester import TaskRequester
from module.PromptBuilder import PromptBuilder
from module.PromptPathResolver import PromptPathResolver


class GlossaryReviewTask:
    """单次术语表审校请求：构建提示词、调用 LLM、解析审校结论。

    将一批术语条目（src/dst/info）发给 AI，让 AI 判定每条是否保留、修正或移除。
    """

    # 审校结论 JSON 行的正则匹配
    VERDICT_PATTERN: re.Pattern[str] = re.compile(
        r'"(?:verdict|VERDICT)"\s*:\s*"(KEEP|FIX|REMOVE)"'
    )
    SRC_PATTERN: re.Pattern[str] = re.compile(r'"src"\s*:\s*"((?:[^"\\]|\\.)*)"')
    SUGGESTED_DST_PATTERN: re.Pattern[str] = re.compile(
        r'"suggested_dst"\s*:\s*"((?:[^"\\]|\\.)*)"'
    )
    REASON_PATTERN: re.Pattern[str] = re.compile(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"')

    def __init__(
        self,
        config: Config,
        model: dict[str, Any],
        entries: list[dict[str, Any]],
        stop_checker: Callable[[], bool] | None = None,
    ) -> None:
        self.config: Config = config
        self.model: dict[str, Any] = model
        self.entries: list[dict[str, Any]] = entries
        self.stop_checker: Callable[[], bool] | None = stop_checker

    def start(self) -> list[GlossaryReviewResult]:
        """执行审校请求并返回逐条结果。"""
        messages = self.build_messages()
        request_result = TaskRequestExecutor.execute(
            config=self.config,
            model=self.model,
            messages=messages,
            requester_factory=TaskRequester,
            stop_checker=self.stop_checker,
        )

        if request_result.exception is not None:
            # 请求出错，所有条目标记为 KEEP（不做任何修改）
            return [
                GlossaryReviewResult(
                    src=entry.get("src", ""),
                    dst=entry.get("dst", ""),
                    verdict=GlossaryReviewResult.Verdict.KEEP,
                    reason=str(request_result.exception),
                )
                for entry in self.entries
            ]

        return self.parse_response(request_result.cleaned_response_result)

    def build_messages(self) -> list[dict[str, Any]]:
        """构建术语表审校提示词消息列表。"""
        task_type = PromptPathResolver.TaskType.GLOSSARY_REVIEW

        # 构建系统提示词：前缀 + 基础规则 + 思考（可选） + 后缀
        system_parts: list[str] = []

        prefix = PromptBuilder.read_prompt_text(
            task_type, self.config.app_language, "prefix.txt"
        )
        system_parts.append(prefix)

        base = PromptBuilder.read_prompt_text(
            task_type, self.config.app_language, "base.txt"
        )
        system_parts.append(base)

        if self.config.force_thinking_enable:
            thinking = PromptBuilder.read_prompt_text(
                task_type, self.config.app_language, "thinking.txt"
            )
            system_parts.append(thinking)

        suffix = PromptBuilder.read_prompt_text(
            task_type, self.config.app_language, "suffix.txt"
        )
        system_parts.append(suffix)

        system_prompt = "\n\n".join(system_parts)

        # 替换语言占位符
        source_name = BaseLanguage.get_name_en(self.config.source_language)
        target_name = BaseLanguage.get_name_en(self.config.target_language)
        system_prompt = system_prompt.replace("{source_language}", source_name)
        system_prompt = system_prompt.replace("{target_language}", target_name)

        # 构建用户消息：术语条目列表
        entry_lines: list[str] = []
        for entry in self.entries:
            src = entry.get("src", "")
            dst = entry.get("dst", "")
            info = entry.get("info", "")
            line: dict[str, str] = {"src": src, "dst": dst}
            if info:
                line["info"] = info
            entry_lines.append(json.dumps(line, ensure_ascii=False))

        user_message = "Review the following glossary entries:\n" + "\n".join(
            entry_lines
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def parse_response(self, response: str) -> list[GlossaryReviewResult]:
        """解析 LLM 审校响应为逐条结果。"""
        # 按 src 建立索引，用于回填
        src_to_entry: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            src_to_entry[entry.get("src", "")] = entry

        parsed: list[GlossaryReviewResult] = []
        for line in response.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("```"):
                continue

            result = self.parse_single_line(line)
            if result is not None:
                parsed.append(result)

        # 将解析结果按 src 匹配回原条目顺序
        result_by_src: dict[str, GlossaryReviewResult] = {}
        for r in parsed:
            result_by_src[r.src] = r

        results: list[GlossaryReviewResult] = []
        for entry in self.entries:
            src = entry.get("src", "")
            dst = entry.get("dst", "")
            if src in result_by_src:
                results.append(result_by_src[src])
            else:
                # 未返回结果的条目默认 KEEP
                results.append(
                    GlossaryReviewResult(
                        src=src,
                        dst=dst,
                        verdict=GlossaryReviewResult.Verdict.KEEP,
                        reason="No review result returned for this entry",
                    )
                )

        return results

    def parse_single_line(self, line: str) -> GlossaryReviewResult | None:
        """解析单行 JSONLINE 审校结果。"""
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return self.extract_result_from_dict(data)
        except json.JSONDecodeError:
            pass

        # 回退到正则提取
        verdict_match = self.VERDICT_PATTERN.search(line)
        if not verdict_match:
            return None

        src = ""
        src_match = self.SRC_PATTERN.search(line)
        if src_match:
            src = src_match.group(1)

        verdict_str = verdict_match.group(1)
        suggested_dst = ""
        reason = ""

        dst_match = self.SUGGESTED_DST_PATTERN.search(line)
        if dst_match:
            suggested_dst = dst_match.group(1)

        reason_match = self.REASON_PATTERN.search(line)
        if reason_match:
            reason = reason_match.group(1)

        try:
            verdict = GlossaryReviewResult.Verdict(verdict_str)
        except ValueError:
            verdict = GlossaryReviewResult.Verdict.KEEP

        return GlossaryReviewResult(
            src=src,
            dst="",
            verdict=verdict,
            suggested_dst=suggested_dst,
            reason=reason,
        )

    @staticmethod
    def extract_result_from_dict(data: dict[str, Any]) -> GlossaryReviewResult:
        """从已解析的字典提取审校结果。"""
        src = str(data.get("src", ""))
        dst = str(data.get("dst", ""))
        suggested_dst = str(data.get("suggested_dst", ""))
        reason = str(data.get("reason", ""))
        verdict_str = str(data.get("verdict", "KEEP"))

        try:
            verdict = GlossaryReviewResult.Verdict(verdict_str)
        except ValueError:
            verdict = GlossaryReviewResult.Verdict.KEEP

        return GlossaryReviewResult(
            src=src,
            dst=dst,
            verdict=verdict,
            suggested_dst=suggested_dst,
            reason=reason,
        )
