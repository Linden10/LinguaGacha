from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import Action
from qfluentwidgets import ComboBox
from qfluentwidgets import FluentWindow
from qfluentwidgets import MessageBox
from qfluentwidgets import SpinBox
from qfluentwidgets import SwitchButton

from base.Base import Base
from base.BaseIcon import BaseIcon
from module.Config import Config
from module.Data.DataManager import DataManager
from module.Engine.Engine import Engine
from module.Engine.TaskRunnerLifecycle import TaskRunnerLifecycle
from module.GameCapture.GameCapture import GameCapture
from module.Localizer.Localizer import Localizer
from widget.CommandBarCard import CommandBarCard
from widget.CustomLineEdit import CustomLineEdit
from widget.SettingCard import SettingCard

# ==================== 图标常量 ====================
ICON_ACTION_START: BaseIcon = BaseIcon.PLAY
ICON_ACTION_STOP: BaseIcon = BaseIcon.CIRCLE_STOP
ICON_NAV_REVIEW: BaseIcon = BaseIcon.CLIPBOARD_CHECK

# 审校范围索引
SCOPE_ALL: int = 0
SCOPE_FILE: int = 1
SCOPE_FAILED: int = 2


class ReviewPage(Base, QWidget):
    """AI 审校页面。

    提供审校控制面板：选择审校范围（全部文件/选定文件/失败行）、
    审批模式（手动/自动）、模型选择、游戏窗口捕获和进度展示。
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
        self.add_widget_scope(self.root)
        self.add_widget_settings(self.root, config)
        self.add_widget_capture(self.root, config)
        self.add_widget_progress(self.root)

        # 填充
        self.root.addStretch(1)

        # 订阅事件
        self.subscribe(Base.Event.REVIEW_TASK, self.on_review_task_event)
        self.subscribe(Base.Event.REVIEW_PROGRESS, self.on_review_progress)
        self.subscribe(Base.Event.REVIEW_REQUEST_STOP, self.on_review_stop)
        self.subscribe(Base.Event.PROJECT_LOADED, self.on_project_loaded)
        self.subscribe(Base.Event.PROJECT_UNLOADED, self.on_project_unloaded)
        self.subscribe_busy_state_events(self.on_engine_status_changed)

        # 初始化按钮状态
        self.on_engine_status_changed(
            Base.Event.REVIEW_TASK,
            {"sub_event": Base.SubEvent.DONE},
        )

    # ==================== 头部工具栏 ====================

    def add_widget_head(self, parent: QVBoxLayout, config: Config) -> None:
        """添加顶部工具栏（开始/停止审校按钮）。"""
        self.head_command_bar = CommandBarCard()

        # 开始审校按钮
        self.start_action = self.head_command_bar.add_action(
            Action(
                ICON_ACTION_START,
                Localizer.get().review_page_start,
                self.head_command_bar,
                triggered=self.on_start_review,
            )
        )

        # 停止审校按钮
        self.stop_action = self.head_command_bar.add_action(
            Action(
                ICON_ACTION_STOP,
                Localizer.get().review_page_stop,
                self.head_command_bar,
                triggered=self.on_stop_review,
            )
        )
        self.stop_action.setEnabled(False)

        parent.addWidget(self.head_command_bar)

    # ==================== 审校范围 ====================

    def add_widget_scope(self, parent: QVBoxLayout) -> None:
        """添加审校范围选择（全部文件/选定文件/失败行）和文件下拉框。"""
        # 范围选择
        scope_card = SettingCard(
            title=Localizer.get().review_page_scope,
            description=Localizer.get().review_page_scope_desc,
            parent=self,
        )
        self.scope_combo = ComboBox(scope_card)
        self.scope_combo.addItems(
            [
                Localizer.get().review_page_scope_all,
                Localizer.get().review_page_scope_file,
                Localizer.get().review_page_scope_failed,
            ]
        )
        self.scope_combo.setCurrentIndex(SCOPE_ALL)
        self.scope_combo.currentIndexChanged.connect(self.on_scope_changed)
        scope_card.add_right_widget(self.scope_combo)
        parent.addWidget(scope_card)

        # 文件选择（仅"选定文件"范围下可见）
        file_card = SettingCard(
            title=Localizer.get().review_page_scope_file,
            description=Localizer.get().review_page_scope_file_desc,
            parent=self,
        )
        self.file_combo = ComboBox(file_card)
        self.file_combo.setMinimumWidth(260)
        file_card.add_right_widget(self.file_combo)
        parent.addWidget(file_card)

        self.file_card = file_card
        self.file_card.setVisible(False)

        # 尝试填充文件列表
        self.populate_file_combo()

    def populate_file_combo(self) -> None:
        """从当前工程填充文件下拉框。"""
        self.file_combo.clear()
        dm = DataManager.get()
        if not dm.is_loaded():
            return

        items = dm.get_all_items()
        file_paths: set[str] = set()
        for item in items:
            fp = item.get_file_path()
            if fp:
                file_paths.add(fp)

        for path in sorted(file_paths):
            self.file_combo.addItem(path, userData=path)

    def on_scope_changed(self, index: int) -> None:
        """审校范围变更：控制文件下拉框可见性。"""
        self.file_card.setVisible(index == SCOPE_FILE)

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

    # ==================== 游戏窗口捕获区域 ====================

    def add_widget_capture(self, parent: QVBoxLayout, config: Config) -> None:
        """添加游戏窗口捕获设置。"""
        # 启用开关
        capture_card = SettingCard(
            title=Localizer.get().review_page_capture_enable,
            description=Localizer.get().review_page_capture_enable_desc,
            parent=self,
        )
        self.capture_switch = SwitchButton(capture_card)
        self.capture_switch.setChecked(config.review_capture_enable)
        self.capture_switch.checkedChanged.connect(self.on_capture_enable_changed)
        capture_card.add_right_widget(self.capture_switch)
        parent.addWidget(capture_card)

        # 捕获模式
        mode_card = SettingCard(
            title=Localizer.get().review_page_capture_mode,
            description=Localizer.get().review_page_capture_mode_desc,
            parent=self,
        )
        self.capture_mode_combo = ComboBox(mode_card)
        self.capture_mode_combo.addItems(
            [
                Localizer.get().review_page_capture_image,
                Localizer.get().review_page_capture_video,
                Localizer.get().review_page_capture_audio,
            ]
        )
        capture_mode_map = {
            Config.CaptureMode.IMAGE: 0,
            Config.CaptureMode.VIDEO: 1,
            Config.CaptureMode.AUDIO: 2,
        }
        self.capture_mode_combo.setCurrentIndex(
            capture_mode_map.get(config.review_capture_mode, 0)
        )
        self.capture_mode_combo.currentIndexChanged.connect(self.on_capture_mode_changed)
        mode_card.add_right_widget(self.capture_mode_combo)
        parent.addWidget(mode_card)
        self.capture_mode_card = mode_card

        # 游戏窗口标题
        window_card = SettingCard(
            title=Localizer.get().review_page_capture_window,
            description=Localizer.get().review_page_capture_window_desc,
            parent=self,
        )
        self.capture_window_edit = CustomLineEdit(window_card)
        self.capture_window_edit.setMinimumWidth(200)
        self.capture_window_edit.setText(config.review_capture_window)
        self.capture_window_edit.editingFinished.connect(self.on_capture_window_changed)
        window_card.add_right_widget(self.capture_window_edit)
        parent.addWidget(window_card)
        self.capture_window_card = window_card

        # 热键
        hotkey_card = SettingCard(
            title=Localizer.get().review_page_capture_hotkey,
            description=Localizer.get().review_page_capture_hotkey_desc,
            parent=self,
        )
        self.capture_hotkey_edit = CustomLineEdit(hotkey_card)
        self.capture_hotkey_edit.setMinimumWidth(120)
        self.capture_hotkey_edit.setText(config.review_capture_hotkey)
        self.capture_hotkey_edit.editingFinished.connect(self.on_capture_hotkey_changed)
        hotkey_card.add_right_widget(self.capture_hotkey_edit)
        parent.addWidget(hotkey_card)
        self.capture_hotkey_card = hotkey_card

        # 自动推进
        auto_card = SettingCard(
            title=Localizer.get().review_page_capture_auto_advance,
            description=Localizer.get().review_page_capture_auto_advance_desc,
            parent=self,
        )
        self.capture_auto_switch = SwitchButton(auto_card)
        self.capture_auto_switch.setChecked(config.review_capture_auto_advance)
        self.capture_auto_switch.checkedChanged.connect(
            self.on_capture_auto_advance_changed
        )
        auto_card.add_right_widget(self.capture_auto_switch)
        parent.addWidget(auto_card)
        self.capture_auto_card = auto_card

        # 根据开关初始化可见性
        self.update_capture_visibility(config.review_capture_enable)

    def update_capture_visibility(self, enabled: bool) -> None:
        """根据捕获开关控制子设置项的可见性。"""
        self.capture_mode_card.setVisible(enabled)
        self.capture_window_card.setVisible(enabled)
        self.capture_hotkey_card.setVisible(enabled)
        self.capture_auto_card.setVisible(enabled)

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

    # ==================== 审校启动 ====================

    def on_start_review(self) -> None:
        """根据当前范围选择启动审校。"""
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

        scope_index = self.scope_combo.currentIndex()
        items = dm.get_all_items()

        if scope_index == SCOPE_ALL:
            # 审校全部已翻译条目
            review_items = self.filter_translated_items(items)
            scope_desc = Localizer.get().review_page_scope_all

        elif scope_index == SCOPE_FILE:
            # 审校选定文件的已翻译条目
            selected_file = self.file_combo.currentData()
            if not selected_file:
                self.emit(
                    Base.Event.TOAST,
                    {
                        "type": Base.ToastType.WARNING,
                        "message": Localizer.get().review_page_no_file_selected,
                    },
                )
                return
            file_items = [
                item for item in items if item.get_file_path() == selected_file
            ]
            review_items = self.filter_translated_items(file_items)
            scope_desc = Localizer.get().review_page_scope_file

        elif scope_index == SCOPE_FAILED:
            # 仅审校失败 / 错误行
            review_items = [
                item
                for item in items
                if item.status == Base.ProjectStatus.ERROR
            ]
            scope_desc = Localizer.get().review_page_scope_failed

        else:
            return

        if not review_items:
            TaskRunnerLifecycle.emit_no_items_warning(self)
            return

        # 确认对话框
        message_box = MessageBox(
            Localizer.get().confirm,
            scope_desc,
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

    @staticmethod
    def filter_translated_items(items: list) -> list:
        """筛选已翻译（可审校）的条目。"""
        return [
            item
            for item in items
            if item.status
            in (
                Base.ProjectStatus.PROCESSED,
                Base.ProjectStatus.PROCESSED_IN_PAST,
                Base.ProjectStatus.ERROR,
            )
        ]

    def start_review_items(self, items: list) -> None:
        """从外部（如校对页右键菜单）直接启动审校。"""
        if not items:
            TaskRunnerLifecycle.emit_no_items_warning(self)
            return

        self.emit(
            Base.Event.REVIEW_TASK,
            {
                "sub_event": Base.SubEvent.REQUEST,
                "items": items,
            },
        )

    def on_stop_review(self) -> None:
        """停止审校。"""
        self.emit(
            Base.Event.REVIEW_REQUEST_STOP,
            {"sub_event": Base.SubEvent.REQUEST},
        )

    # ==================== 事件处理 ====================

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

    def on_project_loaded(self, event: Base.Event, data: dict) -> None:
        """工程加载后刷新文件列表。"""
        self.populate_file_combo()

    def on_project_unloaded(self, event: Base.Event, data: dict) -> None:
        """工程卸载后清空文件列表。"""
        self.file_combo.clear()

    def update_buttons(self) -> None:
        """根据引擎状态更新按钮可用性。"""
        status = Engine.get().get_status()
        is_busy = Base.is_engine_busy(status)
        is_reviewing = status == Base.TaskStatus.REVIEWING
        is_stopping = status == Base.TaskStatus.STOPPING

        self.start_action.setEnabled(not is_busy)
        self.stop_action.setEnabled(is_reviewing and not is_stopping)

        # 审校过程中禁用设置
        self.scope_combo.setEnabled(not is_busy)
        self.file_combo.setEnabled(not is_busy)
        self.model_combo.setEnabled(not is_busy)
        self.approval_combo.setEnabled(not is_busy)
        self.preceding_spin.setEnabled(not is_busy)
        self.timeout_spin.setEnabled(not is_busy)
        self.capture_switch.setEnabled(not is_busy)
        self.capture_mode_combo.setEnabled(not is_busy)
        self.capture_window_edit.setEnabled(not is_busy)
        self.capture_hotkey_edit.setEnabled(not is_busy)
        self.capture_auto_switch.setEnabled(not is_busy)

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

    def on_capture_enable_changed(self, checked: bool) -> None:
        """捕获开关变更。"""
        config = Config().load()
        config.review_capture_enable = checked
        config.save()
        self.update_capture_visibility(checked)

    def on_capture_mode_changed(self, index: int) -> None:
        """捕获模式变更。"""
        mode_map = {
            0: Config.CaptureMode.IMAGE,
            1: Config.CaptureMode.VIDEO,
            2: Config.CaptureMode.AUDIO,
        }
        config = Config().load()
        config.review_capture_mode = mode_map.get(index, Config.CaptureMode.IMAGE)
        config.save()

    def on_capture_window_changed(self) -> None:
        """游戏窗口标题变更。"""
        config = Config().load()
        config.review_capture_window = self.capture_window_edit.text().strip()
        config.save()

    def on_capture_hotkey_changed(self) -> None:
        """热键变更。"""
        config = Config().load()
        config.review_capture_hotkey = self.capture_hotkey_edit.text().strip()
        config.save()

    def on_capture_auto_advance_changed(self, checked: bool) -> None:
        """自动推进开关变更。"""
        config = Config().load()
        config.review_capture_auto_advance = checked
        config.save()
