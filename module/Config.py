import dataclasses
import os
import shutil
import threading
from enum import StrEnum
from typing import Any
from typing import ClassVar
from typing import Self

from base.BasePath import BasePath
from base.BaseLanguage import BaseLanguage
from base.LogManager import LogManager
from module.Localizer.Localizer import Localizer
from module.ModelManager import ModelManager
from module.Utils.JSONTool import JSONTool


@dataclasses.dataclass
class Config:
    CONFIG_FILE_NAME: ClassVar[str] = "config.json"
    MODEL_TYPE_SORT_ORDER: ClassVar[dict[str, int]] = {
        "PRESET": 0,
        "CUSTOM_GOOGLE": 1,
        "CUSTOM_OPENAI": 2,
        "CUSTOM_ANTHROPIC": 3,
    }

    class Theme(StrEnum):
        DARK = "DARK"
        LIGHT = "LIGHT"

    class ProjectSaveMode(StrEnum):
        MANUAL = "MANUAL"
        SOURCE = "SOURCE"
        FIXED = "FIXED"

    class PrecedingContextMode(StrEnum):
        ORIGINAL = "ORIGINAL"  # 仅用原文作上文
        TRANSLATED = "TRANSLATED"  # 仅用译文作上文
        BOTH = "BOTH"  # 原文与译文同时作上文

    class ReviewApprovalMode(StrEnum):
        MANUAL = "MANUAL"  # 逐行人工审批
        AUTO_ACCEPT = "AUTO_ACCEPT"  # 自动接受 AI 建议
        AUTO_PAUSE_ON_FAIL = (
            "AUTO_SKIP_WARNING"  # 自动接受 FIX，FAIL 时暂停等待人工审批
        )

    class CaptureMode(StrEnum):
        IMAGE = "IMAGE"  # 截图
        VIDEO = "VIDEO"  # 录制视频（无音频）
        VIDEO_AUDIO = "VIDEO_AUDIO"  # 录制视频（含音频）
        AUDIO = "AUDIO"  # 录制音频

    # Application
    theme: str = Theme.LIGHT
    app_language: BaseLanguage.Enum = BaseLanguage.Enum.ZH

    # ModelPage - 模型管理系统
    activate_model_id: str = ""
    models: list[dict[str, Any]] | None = None

    # AppSettingsPage
    expert_mode: bool = False
    proxy_url: str = ""
    proxy_enable: bool = False
    scale_factor: str = ""

    # BasicSettingsPage
    # 配置文件持久化为字符串，因此运行时也允许 str（例如 target_language="ZH"）。
    # 仅 source_language 支持 BaseLanguage.ALL（关闭语言过滤），target_language 不支持 ALL。
    source_language: BaseLanguage.Enum | str = BaseLanguage.Enum.JA
    target_language: BaseLanguage.Enum | str = BaseLanguage.Enum.ZH
    project_save_mode: str = ProjectSaveMode.MANUAL
    project_fixed_path: str = ""
    output_folder_open_on_finish: bool = False
    request_timeout: int = 120
    max_round: int = 0

    # ExpertSettingsPage
    preceding_lines_threshold: int = 0
    preceding_context_mode: str = PrecedingContextMode.ORIGINAL
    clean_ruby: bool = False
    deduplication_in_trans: bool = True
    deduplication_in_bilingual: bool = True
    check_kana_residue: bool = True
    check_hangeul_residue: bool = True
    check_similarity: bool = True
    write_translated_name_fields_to_file: bool = True
    auto_process_prefix_suffix_preserved_text: bool = True
    ffmpeg_path: str = ""  # 自定义 ffmpeg 路径，留空则使用系统 PATH

    # LaboratoryPage
    force_thinking_enable: bool = True
    mtool_optimizer_enable: bool = False

    # ReviewPage — AI 审校
    review_model_id: str = ""  # 审校使用的模型 ID，空表示使用当前激活模型
    review_approval_mode: str = ReviewApprovalMode.MANUAL
    review_preceding_lines: int = 10  # 审校时携带的上文行数
    review_timeout: int = 120  # 单行审校超时（秒）
    review_export_report_enable: bool = False  # 是否导出审校报告
    review_export_report_path: str = ""  # 审校报告导出路径，空表示应用根目录
    review_rate_limit: int = 0  # 审校 API 调用频率限制（每分钟），0 为不限制
    review_auto_retry_failed: bool = False  # 审校完成后自动重试失败行

    # ReviewPage — 术语表审校
    glossary_review_approval_mode: str = ReviewApprovalMode.MANUAL
    glossary_review_batch_size: int = 50  # 术语表审校每批条目数

    # ReviewPage — 自定义审校提示词
    review_custom_prompt_default_preset: str = ""

    # ReviewPage — 游戏窗口捕获
    review_capture_enable: bool = False  # 是否启用游戏窗口捕获
    review_capture_mode: str = CaptureMode.IMAGE  # 捕获模式（截图 / 录像 / 录音）
    review_capture_window: str = ""  # 游戏窗口标题（截图用）
    review_capture_window_pid: str = ""  # 游戏窗口进程 ID（热键发送用，比标题更可靠）
    review_capture_hotkey: str = ""  # 发送给游戏窗口的热键（如 Enter、Space）
    review_capture_auto_advance: bool = False  # 每行审校前自动发送热键推进游戏

    # GlossaryPage
    glossary_default_preset: str = ""

    # TextPreservePage
    text_preserve_default_preset: str = ""

    # TextReplacementPage
    pre_translation_replacement_default_preset: str = ""
    post_translation_replacement_default_preset: str = ""

    # CustomPromptPage
    translation_custom_prompt_default_preset: str = ""
    analysis_custom_prompt_default_preset: str = ""

    # 最近打开的工程列表 [{"path": "...", "name": "...", "updated_at": "..."}]
    recent_projects: list[dict[str, str]] = dataclasses.field(default_factory=list)

    # 类属性
    CONFIG_LOCK: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def get_default_path(cls) -> str:
        """统一返回默认配置文件路径，避免读写两边各自拼接。"""

        return os.path.join(BasePath.get_user_data_root_dir(), cls.CONFIG_FILE_NAME)

    @classmethod
    def resolve_path(cls, path: str | None) -> str:
        """把外部可选路径统一收口，减少 load/save 各自处理默认值。"""

        if path is None:
            return cls.get_default_path()
        return path

    @classmethod
    def get_legacy_default_paths(cls) -> list[str]:
        """收口旧版默认配置位置，便于启动时做一次性迁移。"""

        data_dir = BasePath.get_data_dir()
        app_dir = BasePath.get_app_dir()

        # 迁移优先级必须与 main 分支旧默认读取规则一致：
        # 1. 便携/只读安装场景优先沿用 data_dir/config.json。
        # 2. 普通桌面场景优先沿用 resource/config.json。
        # 3. app_dir/config.json 仅作为更早历史残留的兜底来源。
        if os.path.normcase(os.path.normpath(data_dir)) != os.path.normcase(
            os.path.normpath(app_dir)
        ):
            candidate_paths: list[str] = [
                os.path.join(data_dir, cls.CONFIG_FILE_NAME),
                os.path.join(BasePath.get_resource_dir(), cls.CONFIG_FILE_NAME),
                os.path.join(app_dir, cls.CONFIG_FILE_NAME),
            ]
        else:
            candidate_paths = [
                os.path.join(BasePath.get_resource_dir(), cls.CONFIG_FILE_NAME),
                os.path.join(data_dir, cls.CONFIG_FILE_NAME),
                os.path.join(app_dir, cls.CONFIG_FILE_NAME),
            ]

        unique_paths: list[str] = []
        seen_paths: set[str] = set()

        for path in candidate_paths:
            normalized_path = os.path.normcase(os.path.normpath(path))
            if normalized_path in seen_paths:
                continue

            seen_paths.add(normalized_path)
            unique_paths.append(path)

        return unique_paths

    @classmethod
    def migrate_default_config_if_needed(cls, target_path: str) -> None:
        """把旧默认位置的配置复制到 userdata，后续优先读取新位置。"""

        if os.path.isfile(target_path):
            return

        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        for source_path in cls.get_legacy_default_paths():
            if not os.path.isfile(source_path):
                continue

            shutil.copyfile(source_path, target_path)
            return

    def load(self, path: str | None = None) -> Self:
        path = __class__.resolve_path(path)

        with __class__.CONFIG_LOCK:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.isfile(path):
                    config: Any = JSONTool.load_file(path)
                    if isinstance(config, dict):
                        for k, v in config.items():
                            if hasattr(self, k):
                                setattr(self, k, v)
            except Exception as e:
                LogManager.get().error(f"{Localizer.get().log_read_file_fail}", e)

        return self

    def save(self, path: str | None = None) -> Self:
        path = __class__.resolve_path(path)

        # 按分类排序: 预设 - Google - OpenAI - Claude
        if self.models:

            def get_sort_key(model: dict[str, Any]) -> int:
                return __class__.MODEL_TYPE_SORT_ORDER.get(model.get("type", ""), 99)

            self.models.sort(key=get_sort_key)

        with __class__.CONFIG_LOCK:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as writer:
                    writer.write(JSONTool.dumps(dataclasses.asdict(self), indent=4))
            except Exception as e:
                LogManager.get().error(f"{Localizer.get().log_write_file_fail}", e)

        return self

    # 重置专家模式
    def reset_expert_settings(self) -> None:
        # ExpertSettingsPage
        self.preceding_lines_threshold: int = 0
        self.preceding_context_mode: str = Config.PrecedingContextMode.ORIGINAL
        self.clean_ruby: bool = True
        self.deduplication_in_trans: bool = True
        self.deduplication_in_bilingual: bool = True
        self.check_kana_residue: bool = True
        self.check_hangeul_residue: bool = True
        self.check_similarity: bool = True
        self.write_translated_name_fields_to_file: bool = True
        self.auto_process_prefix_suffix_preserved_text: bool = True

    # 初始化模型管理器
    def initialize_models(self) -> int:
        """初始化模型列表，如果没有则从预设复制。返回已被迁移的失效预设模型数量。"""
        manager = ModelManager.get()
        # 设置 UI 语言以确定预设目录
        manager.set_app_language(self.app_language)
        self.models, migrated_count = manager.initialize_models(self.models or [])
        manager.set_models(self.models)
        # 如果没有激活模型，设置为第一个
        if not self.activate_model_id and self.models:
            self.activate_model_id = self.models[0].get("id", "")
        manager.set_active_model_id(self.activate_model_id)
        return migrated_count

    # 获取模型配置
    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """根据 ID 获取模型配置字典"""
        for model in self.models or []:
            if model.get("id") == model_id:
                return model
        return None

    # 更新模型配置
    def set_model(self, model_data: dict[str, Any]) -> None:
        """更新模型配置"""
        models = self.models or []
        model_id = model_data.get("id")
        for i, model in enumerate(models):
            if model.get("id") == model_id:
                models[i] = model_data
                break

        self.models = models
        # 同步到 ModelManager
        ModelManager.get().set_models(models)

    # 获取激活的模型
    def get_active_model(self) -> dict[str, Any] | None:
        """获取当前激活的模型配置"""
        if self.activate_model_id:
            model = self.get_model(self.activate_model_id)
            if model:
                return model
        # 如果没有或找不到，返回第一个
        if self.models:
            return self.models[0]
        return None

    # 设置激活的模型
    def set_active_model_id(self, model_id: str) -> None:
        """设置激活的模型 ID"""
        self.activate_model_id = model_id
        ModelManager.get().set_active_model_id(model_id)

    # 同步模型数据到 ModelManager
    def sync_models_to_manager(self) -> None:
        """将 Config 中的 models 同步到 ModelManager"""
        manager = ModelManager.get()
        manager.set_models(self.models or [])
        manager.set_active_model_id(self.activate_model_id)

    # 从 ModelManager 同步模型数据
    def sync_models_from_manager(self) -> None:
        """从 ModelManager 同步数据到 Config"""
        manager = ModelManager.get()
        self.models = manager.get_models_as_dict()
        self.activate_model_id = manager.activate_model_id

    # ========== 最近打开的工程 ==========
    def add_recent_project(self, path: str, name: str) -> None:
        """添加最近打开的工程"""
        from datetime import datetime

        # 移除已存在的同路径条目
        self.recent_projects = [
            p for p in self.recent_projects if p.get("path") != path
        ]

        # 添加到开头
        self.recent_projects.insert(
            0,
            {
                "path": path,
                "name": name,
                "updated_at": datetime.now().isoformat(),
            },
        )

        # 保留最近 10 个
        self.recent_projects = self.recent_projects[:10]

    def remove_recent_project(self, path: str) -> None:
        """移除最近打开的工程"""
        self.recent_projects = [
            p for p in self.recent_projects if p.get("path") != path
        ]
