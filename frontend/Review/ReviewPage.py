
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import ComboBox
from qfluentwidgets import FluentWindow
from qfluentwidgets import MessageBox
from qfluentwidgets import SpinBox

from base.Base import Base
from base.BaseIcon import BaseIcon
from module.Config import Config
from module.Data.DataManager import DataManager
from module.Engine.Engine import Engine
from module.Engine.TaskRunnerLifecycle import TaskRunnerLifecycle
from module.Localizer.Localizer import Localizer
from widget.CommandBarCard import CommandBarCard
from widget.SettingCard import SettingCard

# ==================== 图标常量 ====================
ICON_ACTION_START: BaseIcon = BaseIcon.PLAY
ICON_ACTION_STOP: BaseIcon = BaseIcon.CIRCLE_STOP
ICON_NAV_REVIEW: BaseIcon = BaseIcon.CLIPBOARD_CHECK


class ReviewPage(Base, QWidget):
    """AI 审校页面。

    提供审校控制面板：选择审校范围（全部文件/选定文件/失败行）、
    审批模式（手动/自动）、模型选择和进度展示。
    """

    def __init__(self, text: str, window: FluentWindow) -> None:
        super().__init__(window)
        self.setObjectName(text.replace(" ", "-"))

        self.window_ref = window
        self.is_reviewing: bool = False

        # 载入配置
        config = Config().load()

        # 主容器
        self.root = QVBoxLayout(self)
        self.root.setSpacing(8)
        self.root.setContentsMargins(24, 24, 24, 24)

        # 添加控件
        self.add_widget_head(self.root, config)
        self.add_widget_settings(self.root, config)
        self.add_widget_progress(self.root)

        # 填充
        self.root.addStretch(1)

        # 订阅事件
        self.subscribe(Base.Event.REVIEW_TASK, self.on_review_task_event)
        self.subscribe(Base.Event.REVIEW_PROGRESS, self.on_review_progress)
        self.subscribe(Base.Event.REVIEW_REQUEST_STOP, self.on_review_stop)
        self.subscribe_busy_state_events(self.on_engine_status_changed)

        # 初始化按钮状态
        self.on_engine_status_changed(
            Base.Event.REVIEW_TASK,
            {"sub_event": Base.SubEvent.DONE},
        )

    # ==================== 头部工具栏 ====================

    def add_widget_head(self, parent: QVBoxLayout, config: Config) -> None:
        """添加顶部工具栏（开始/停止审校按钮）。"""
        self.head_command_bar = CommandBarCard(parent=self)

        # 开始审校按钮（带下拉菜单选择审校范围）
        self.start_action = self.head_command_bar.add_action(
            icon=ICON_ACTION_START.qicon(),
            text=Localizer.get().review_page_start,
            triggered=self.on_start_review_all,
        )

        # 停止审校按钮
        self.stop_action = self.head_command_bar.add_action(
            icon=ICON_ACTION_STOP.qicon(),
            text=Localizer.get().review_page_stop,
            triggered=self.on_stop_review,
        )
        self.stop_action.setEnabled(False)

        parent.addWidget(self.head_command_bar)

    # ==================== 设置区域 ====================

    def add_widget_settings(self, parent: QVBoxLayout, config: Config) -> None:
        """添加审校设置面板。"""
        # 审校模型选择
        model_card = SettingCard(
            title=Localizer.get().review_page_model,
            description=Localizer.get().review_page_model_desc,
            parent=self,
        )
        self.model_combo = ComboBox(model_card)
        self.model_combo.setMinimumWidth(200)
        self.populate_model_combo(config)
        self.model_combo.currentIndexChanged.connect(self.on_model_changed)
        model_card.add_right_widget(self.model_combo)
        parent.addWidget(model_card)

        # 审批模式
        approval_card = SettingCard(
            title=Localizer.get().review_page_approval_mode,
            description=Localizer.get().review_page_approval_mode_desc,
            parent=self,
        )
        self.approval_combo = ComboBox(approval_card)
        self.approval_combo.addItems(
            [
                Localizer.get().review_page_approval_manual,
                Localizer.get().review_page_approval_auto,
                Localizer.get().review_page_approval_auto_skip,
            ]
        )
        # 设置当前值
        mode_map = {
            Config.ReviewApprovalMode.MANUAL: 0,
            Config.ReviewApprovalMode.AUTO_ACCEPT: 1,
            Config.ReviewApprovalMode.AUTO_SKIP_WARNING: 2,
        }
        self.approval_combo.setCurrentIndex(
            mode_map.get(config.review_approval_mode, 0)
        )
        self.approval_combo.currentIndexChanged.connect(self.on_approval_mode_changed)
        approval_card.add_right_widget(self.approval_combo)
        parent.addWidget(approval_card)

        # 上文行数
        preceding_card = SettingCard(
            title=Localizer.get().review_page_preceding_lines,
            description=Localizer.get().review_page_preceding_lines_desc,
            parent=self,
        )
        self.preceding_spin = SpinBox(preceding_card)
        self.preceding_spin.setRange(0, 100)
        self.preceding_spin.setValue(config.review_preceding_lines)
        self.preceding_spin.valueChanged.connect(self.on_preceding_lines_changed)
        preceding_card.add_right_widget(self.preceding_spin)
        parent.addWidget(preceding_card)

        # 单行超时
        timeout_card = SettingCard(
            title=Localizer.get().review_page_timeout,
            description=Localizer.get().review_page_timeout_desc,
            parent=self,
        )
        self.timeout_spin = SpinBox(timeout_card)
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setValue(config.review_timeout)
        self.timeout_spin.valueChanged.connect(self.on_timeout_changed)
        timeout_card.add_right_widget(self.timeout_spin)
        parent.addWidget(timeout_card)

    # ==================== 进度区域 ====================

    def add_widget_progress(self, parent: QVBoxLayout) -> None:
        """添加审校进度显示。"""
        progress_card = SettingCard(
            title=Localizer.get()
            .review_page_progress.replace("{CURRENT}", "0")
            .replace("{TOTAL}", "0"),
            description="",
            parent=self,
        )
        self.progress_card = progress_card

        # 进度详情标签
        self.progress_detail = QLabel("", progress_card)
        self.progress_detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        progress_card.add_right_widget(self.progress_detail)
        parent.addWidget(progress_card)

    # ==================== 模型下拉框填充 ====================

    def populate_model_combo(self, config: Config) -> None:
        """填充模型选择下拉框。"""
        self.model_combo.clear()
        # 第一项：使用当前激活模型
        self.model_combo.addItem(Localizer.get().auto, userData="")

        # 添加所有可用模型
        models = config.models or []
        selected_index = 0
        for i, model in enumerate(models):
            model_name = model.get("name", model.get("id", "Unknown"))
            model_id = model.get("id", "")
            self.model_combo.addItem(model_name, userData=model_id)
            if model_id == config.review_model_id:
                selected_index = i + 1  # +1 因为第一项是"自动"

        self.model_combo.setCurrentIndex(selected_index)

    # ==================== 事件处理 ====================

    def on_start_review_all(self) -> None:
        """开始审校全部文件。"""
        dm = DataManager.get()
        if not dm.is_loaded():
            self.emit(
                Base.Event.TOAST,
                {
                    "type": Base.ToastType.WARNING,
                    "message": Localizer.get().alert_project_not_loaded,
                },
            )
            return

        # 获取所有已翻译条目
        items = dm.get_all_items()
        review_items = [
            item
            for item in items
            if item.status
            in (
                Base.ProjectStatus.PROCESSED,
                Base.ProjectStatus.PROCESSED_IN_PAST,
                Base.ProjectStatus.ERROR,
            )
        ]

        if not review_items:
            TaskRunnerLifecycle.emit_no_items_warning(self)
            return

        # 确认对话框
        message_box = MessageBox(
            Localizer.get().confirm,
            Localizer.get().review_page_scope_all,
            self.window_ref,
        )
        message_box.yesButton.setText(Localizer.get().confirm)
        message_box.cancelButton.setText(Localizer.get().cancel)

        if not message_box.exec():
            return

        self.emit(
            Base.Event.REVIEW_TASK,
            {
                "sub_event": Base.SubEvent.REQUEST,
                "items": review_items,
            },
        )

    def on_stop_review(self) -> None:
        """停止审校。"""
        self.emit(
            Base.Event.REVIEW_REQUEST_STOP,
            {"sub_event": Base.SubEvent.REQUEST},
        )

    def on_review_task_event(self, event: Base.Event, data: dict) -> None:
        """响应审校任务生命周期事件。"""
        sub_event = data.get("sub_event")

        if sub_event == Base.SubEvent.RUN:
            self.is_reviewing = True
            self.update_buttons()

        elif sub_event in (Base.SubEvent.DONE, Base.SubEvent.ERROR):
            self.is_reviewing = False
            self.update_buttons()
            final_status = data.get("final_status", "")
            if final_status == "SUCCESS":
                self.emit(
                    Base.Event.TOAST,
                    {
                        "type": Base.ToastType.SUCCESS,
                        "message": Localizer.get().review_page_done,
                    },
                )

    def on_review_progress(self, event: Base.Event, data: dict) -> None:
        """响应审校进度更新。"""
        total = data.get("total_line", 0)
        reviewed = data.get("reviewed_line", 0)
        pass_count = data.get("pass_line", 0)
        fix_count = data.get("fix_line", 0)
        fail_count = data.get("fail_line", 0)
        error_count = data.get("error_line", 0)

        # 更新进度标题
        progress_text = (
            Localizer.get()
            .review_page_progress.replace("{CURRENT}", str(reviewed))
            .replace("{TOTAL}", str(total))
        )
        self.progress_card.setTitle(progress_text)

        # 更新详情标签
        detail = f"✅ {pass_count}  🔧 {fix_count}  ❌ {fail_count}  ⚠️ {error_count}"
        self.progress_detail.setText(detail)

    def on_review_stop(self, event: Base.Event, data: dict) -> None:
        """响应审校停止事件。"""
        sub_event = data.get("sub_event")
        if sub_event == Base.SubEvent.RUN:
            self.update_buttons()

    def on_engine_status_changed(self, event: Base.Event, data: dict) -> None:
        """响应引擎状态变化（锁定/解锁控件）。"""
        if event in Base.RESET_PROGRESS_EVENTS and not Base.is_terminal_reset_event(
            event, data
        ):
            return
        self.update_buttons()

    def update_buttons(self) -> None:
        """根据引擎状态更新按钮可用性。"""
        status = Engine.get().get_status()
        is_busy = Base.is_engine_busy(status)
        is_reviewing = status == Base.TaskStatus.REVIEWING
        is_stopping = status == Base.TaskStatus.STOPPING

        self.start_action.setEnabled(not is_busy)
        self.stop_action.setEnabled(is_reviewing and not is_stopping)

        # 审校过程中禁用设置
        self.model_combo.setEnabled(not is_busy)
        self.approval_combo.setEnabled(not is_busy)
        self.preceding_spin.setEnabled(not is_busy)
        self.timeout_spin.setEnabled(not is_busy)

    # ==================== 设置变更回调 ====================

    def on_model_changed(self, index: int) -> None:
        """审校模型选择变更。"""
        model_id = self.model_combo.itemData(index) or ""
        config = Config().load()
        config.review_model_id = model_id
        config.save()

    def on_approval_mode_changed(self, index: int) -> None:
        """审批模式变更。"""
        mode_map = {
            0: Config.ReviewApprovalMode.MANUAL,
            1: Config.ReviewApprovalMode.AUTO_ACCEPT,
            2: Config.ReviewApprovalMode.AUTO_SKIP_WARNING,
        }
        config = Config().load()
        config.review_approval_mode = mode_map.get(
            index, Config.ReviewApprovalMode.MANUAL
        )
        config.save()

    def on_preceding_lines_changed(self, value: int) -> None:
        """上文行数变更。"""
        config = Config().load()
        config.review_preceding_lines = value
        config.save()

    def on_timeout_changed(self, value: int) -> None:
        """单行超时变更。"""
        config = Config().load()
        config.review_timeout = value
        config.save()
