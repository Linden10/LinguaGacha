from __future__ import annotations

import json
import re
from typing import Any
from typing import Callable

from model.Item import Item
from module.Config import Config
from module.Engine.Review.ReviewModels import ReviewResult
from module.Engine.Review.ReviewModels import ReviewVerdict
from module.Engine.TaskRequestExecutor import TaskRequestExecutor
from module.Engine.TaskRequester import TaskRequester
from module.PromptBuilder import PromptBuilder
from module.PromptPathResolver import PromptPathResolver
from module.QualityRule.QualityRuleSnapshot import QualityRuleSnapshot


class ReviewTask:
    """单次审校请求：构建提示词、调用 LLM、解析审校结论。

    审校会将原文和译文同时发给 AI，连同上文和术语表，
    让 AI 判断译文质量并返回逐行结论（PASS / FIX / FAIL）。
    """

    # 审校结论 JSON 行的正则匹配
    VERDICT_PATTERN: re.Pattern[str] = re.compile(
        r'"(?:verdict|VERDICT)"\s*:\s*"(PASS|FIX|FAIL)"'
    )
    CORRECTED_PATTERN: re.Pattern[str] = re.compile(
        r'"corrected"\s*:\s*"((?:[^"\\]|\\.)*)"'
    )
    REASON_PATTERN: re.Pattern[str] = re.compile(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"')

    def __init__(
        self,
        config: Config,
        model: dict[str, Any],
        items: list[Item],
        precedings: list[Item],
        quality_snapshot: QualityRuleSnapshot | None = None,
        stop_checker: Callable[[], bool] | None = None,
        screenshot_b64: str = "",
    ) -> None:
        self.config: Config = config
        self.model: dict[str, Any] = model
        self.items: list[Item] = items
        self.precedings: list[Item] = precedings
        self.quality_snapshot: QualityRuleSnapshot | None = quality_snapshot
        self.stop_checker: Callable[[], bool] | None = stop_checker
        self.screenshot_b64: str = screenshot_b64

    def start(self) -> list[ReviewResult]:
        """执行审校请求并返回逐行审校结果。"""
        messages = self.build_messages()
        request_result = TaskRequestExecutor.execute(
            config=self.config,
            model=self.model,
            messages=messages,
            requester_factory=TaskRequester,
            stop_checker=self.stop_checker,
        )

        if request_result.exception is not None:
            # 请求出错，所有行标记为 ERROR
            return [
                ReviewResult(
                    item_id=item.id or 0,
                    verdict=ReviewVerdict.ERROR,
                    reason=str(request_result.exception),
                    original_dst=item.dst,
                )
                for item in self.items
            ]

        return self.parse_response(request_result.cleaned_response_result)

    def build_messages(self) -> list[dict[str, Any]]:
        """构建审校提示词消息列表。"""
        prompt_builder = PromptBuilder(self.config, self.quality_snapshot)

        # 构建系统提示词：前缀 + 基础规则 + 后缀
        system_parts: list[str] = []

        # 前缀
        prefix = PromptBuilder.read_prompt_text(
            PromptPathResolver.TaskType.REVIEW,
            self.config.app_language,
            "prefix.txt",
        )
        system_parts.append(prefix)

        # 基础审校规则（可被自定义提示词覆盖）
        base = self.resolve_review_prompt_base()
        system_parts.append(base)

        # 思考块（如果启用）
        if self.config.force_thinking_enable:
            thinking = PromptBuilder.read_prompt_text(
                PromptPathResolver.TaskType.REVIEW,
                self.config.app_language,
                "thinking.txt",
            )
            system_parts.append(thinking)

        # 后缀（输出格式）
        suffix = PromptBuilder.read_prompt_text(
            PromptPathResolver.TaskType.REVIEW,
            self.config.app_language,
            "suffix.txt",
        )
        system_parts.append(suffix)

        system_prompt = "\n\n".join(system_parts)

        # 替换语言占位符
        from base.BaseLanguage import BaseLanguage

        source_name = BaseLanguage.get_name_en(self.config.source_language)
        target_name = BaseLanguage.get_name_en(self.config.target_language)
        system_prompt = system_prompt.replace("{source_language}", source_name)
        system_prompt = system_prompt.replace("{target_language}", target_name)

        # 构建用户消息：上文 + 术语表 + 审校内容
        user_parts: list[str] = []

        # 上文（原文 + 译文都要）
        if self.precedings:
            preceding_src_lines = [f"{p.src}" for p in self.precedings if p.src]
            preceding_dst_lines = [f"{p.dst}" for p in self.precedings if p.dst]
            if preceding_src_lines:
                user_parts.append(
                    "Preceding context (original):\n" + "\n".join(preceding_src_lines)
                )
            if preceding_dst_lines:
                user_parts.append(
                    "Preceding context (translated):\n" + "\n".join(preceding_dst_lines)
                )

        # 术语表
        srcs = [item.src for item in self.items]
        glossary_text = prompt_builder.build_glossary(srcs)
        if glossary_text:
            user_parts.append(glossary_text)

        # 审校内容（原文和译文对照）
        review_lines: list[str] = []
        for i, item in enumerate(self.items):
            review_lines.append(f'{{"id":{i},"src":"{item.src}","dst":"{item.dst}"}}')
        user_parts.append(
            "Review the following translations:\n" + "\n".join(review_lines)
        )

        user_message = "\n\n".join(user_parts)

        # 如果有截图，使用多模态消息格式（OpenAI vision API 兼容格式）
        if self.screenshot_b64:
            user_content: list[dict[str, Any]] = [
                {"type": "text", "text": user_message},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{self.screenshot_b64}",
                    },
                },
            ]
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def resolve_review_prompt_base(self) -> str:
        """解析审校基础提示词：自定义优先，否则使用默认模板。"""
        # 检查是否有自定义审校提示词
        custom_prompt = self.get_custom_review_prompt()
        if custom_prompt:
            return custom_prompt

        return PromptBuilder.read_prompt_text(
            PromptPathResolver.TaskType.REVIEW,
            self.config.app_language,
            "base.txt",
        )

    def get_custom_review_prompt(self) -> str:
        """获取自定义审校提示词文本。"""
        virtual_id = self.config.review_custom_prompt_default_preset
        if not virtual_id:
            return ""

        try:
            return PromptPathResolver.read_preset(
                PromptPathResolver.TaskType.REVIEW, virtual_id
            )
        except Exception:
            return ""

    def parse_response(self, response: str) -> list[ReviewResult]:
        """解析 LLM 审校响应为逐行审校结果。"""
        results: list[ReviewResult] = []

        # 逐行解析 JSONLINE 格式
        for line in response.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("```"):
                continue

            result = self.parse_single_line(line)
            if result is not None:
                results.append(result)

        # 补齐缺失的结果
        result_count = len(results)
        for i in range(result_count, len(self.items)):
            results.append(
                ReviewResult(
                    item_id=self.items[i].id or 0,
                    verdict=ReviewVerdict.ERROR,
                    reason="No review result returned for this line",
                    original_dst=self.items[i].dst,
                )
            )

        # 回填 item_id 和 original_dst
        for i, result in enumerate(results):
            if i < len(self.items):
                result.item_id = self.items[i].id or 0
                result.original_dst = self.items[i].dst

        return results[: len(self.items)]

    def parse_single_line(self, line: str) -> ReviewResult | None:
        """解析单行 JSONLINE 审校结果。"""
        try:
            # 尝试 JSON 解析
            data = json.loads(line)
            if isinstance(data, dict):
                return self.extract_result_from_dict(data)
        except json.JSONDecodeError:
            pass

        # 回退到正则提取
        verdict_match = self.VERDICT_PATTERN.search(line)
        if not verdict_match:
            return None

        verdict_str = verdict_match.group(1)
        corrected = ""
        reason = ""

        corrected_match = self.CORRECTED_PATTERN.search(line)
        if corrected_match:
            corrected = corrected_match.group(1)

        reason_match = self.REASON_PATTERN.search(line)
        if reason_match:
            reason = reason_match.group(1)

        try:
            verdict = ReviewVerdict(verdict_str)
        except ValueError:
            verdict = ReviewVerdict.ERROR

        return ReviewResult(
            item_id=0,
            verdict=verdict,
            corrected=corrected,
            reason=reason,
        )

    def extract_result_from_dict(self, data: dict[str, Any]) -> ReviewResult:
        """从已解析的字典提取审校结果。"""
        # 查找 verdict 值（可能是 dict 值中的 PASS/FIX/FAIL）
        verdict_str = ""
        corrected = data.get("corrected", "")
        reason = data.get("reason", "")

        for key, value in data.items():
            if key in ("corrected", "reason"):
                continue
            if isinstance(value, str) and value in ("PASS", "FIX", "FAIL"):
                verdict_str = value
                break

        if not verdict_str:
            verdict_str = data.get("verdict", "PASS")

        try:
            verdict = ReviewVerdict(verdict_str)
        except ValueError:
            verdict = ReviewVerdict.PASS

        return ReviewResult(
            item_id=0,
            verdict=verdict,
            corrected=str(corrected) if corrected else "",
            reason=str(reason) if reason else "",
        )
