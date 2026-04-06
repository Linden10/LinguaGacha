import re

from PySide6.QtCore import QPoint
from PySide6.QtCore import QTimer
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import Action
from qfluentwidgets import ComboBox
from qfluentwidgets import FluentWindow
from qfluentwidgets import SingleDirectionScrollArea
from qfluentwidgets import ListWidget
from qfluentwidgets import MenuAnimationType
from qfluentwidgets import MessageBox
from qfluentwidgets import PlainTextEdit
from qfluentwidgets import ProgressRing
from qfluentwidgets import PushButton
from qfluentwidgets import RoundMenu
from qfluentwidgets import SpinBox
from qfluentwidgets import SwitchButton
from qfluentwidgets import ToolTipFilter
from qfluentwidgets import ToolTipPosition
from qfluentwidgets import TransparentToolButton

from base.Base import Base
from base.BaseIcon import BaseIcon
from base.LogManager import LogManager
from frontend.Translation.DashboardCard import DashboardCard
from model.Item import Item
from module.Config import Config
from module.Data.DataManager import DataManager
from module.Engine.Engine import Engine
from module.Engine.Review.ReviewModels import ReviewHistoryEntry
from module.Engine.TaskRunnerLifecycle import TaskRunnerLifecycle
from module.Localizer.Localizer import Localizer
from widget.CommandBarCard import CommandBarCard
from widget.CustomLineEdit import CustomLineEdit
from widget.HotkeyLineEdit import HotkeyLineEdit
from widget.SettingCard import SettingCard

# ==================== 图标常量 ====================
ICON_ACTION_START: BaseIcon = BaseIcon.PLAY
ICON_ACTION_CONTINUE: BaseIcon = BaseIcon.ROTATE_CW
ICON_ACTION_STOP: BaseIcon = BaseIcon.CIRCLE_STOP
ICON_ACTION_PAUSE: BaseIcon = BaseIcon.CIRCLE_PAUSE
ICON_ACTION_APPROVE: BaseIcon = BaseIcon.CHECK
ICON_ACTION_SKIP: BaseIcon = BaseIcon.SKIP_FORWARD
ICON_ACTION_DENY: BaseIcon = BaseIcon.CROSS
ICON_ACTION_RETRY: BaseIcon = BaseIcon.REFRESH_CW
ICON_ACTION_ASK: BaseIcon = BaseIcon.MESSAGE_CIRCLE_QUESTION
ICON_ACTION_SEND: BaseIcon = BaseIcon.SEND_HORIZONTAL
ICON_ACTION_RESET: BaseIcon = BaseIcon.RECYCLE
ICON_ACTION_RESET_FAILED: BaseIcon = BaseIcon.PAINTBRUSH
ICON_ACTION_RESET_ALL: BaseIcon = BaseIcon.BRUSH_CLEANING
ICON_NAV_REVIEW: BaseIcon = BaseIcon.CLIPBOARD_CHECK
ICON_HISTORY: BaseIcon = BaseIcon.HISTORY
ICON_HISTORY_UNDO: BaseIcon = BaseIcon.UNDO
ICON_HISTORY_REDO: BaseIcon = BaseIcon.REDO
ICON_HISTORY_SAVE: BaseIcon = BaseIcon.SAVE

# 进度环最大值
RING_MAX_VALUE: int = 10000
SCOPE_ALL: int = 0
SCOPE_FILE: int = 1
SCOPE_FAILED: int = 2
LINE_PREVIEW_MAX_LENGTH: int = 60

# 输出过滤索引
FILTER_ALL: int = 0
FILTER_PASS: int = 1
FILTER_FIX: int = 2
FILTER_FAIL: int = 3
FILTER_ERROR: int = 4

FILTER_INDEX_TO_VERDICT: dict[int, str] = {
    FILTER_PASS: "PASS",
    FILTER_FIX: "FIX",
    FILTER_FAIL: "FAIL",
    FILTER_ERROR: "ERROR",
}

# 自动重试默认上限（当 config.max_round <= 0 时的兜底）
AUTO_RETRY_DEFAULT_LIMIT: int = 3


class ReviewPage(Base, QWidget):
    """AI 审校页面。

    提供审校控制面板，包含进度环、统计卡片、AI 输出窗口、
    游戏窗口捕获设置和底部操作工具栏。
    """

    def __init__(self, text: str, window: FluentWindow) -> None:
        super().__init__(window)
        self.setObjectName(text.replace(" ", "-"))

        self.window_ref = window
        self.is_reviewing: bool = False
        self.awaiting_approval: bool = False
        self.awaiting_result_line: str = ""

        # 起始行索引（0 表示从头开始）
        self.starting_line_index: int = 0

        # 审校会话跟踪（用于 Continue / Reset）
        self.review_items: list[Item] = []
        self.reviewed_count: int = 0

        # 输出日志：累积每行审校结果的格式化文本，附带 verdict 用于过滤
        self.output_entries: list[tuple[str, str]] = []

        # Ask AI 对话记录：当前待批条目的问答行，显示在待批结果下方
        self.inquiry_lines: list[str] = []

        # Ask AI 提取的备选 dst（用于选择后覆盖原始修正）
        self.dst_alternatives: list[str] = []

        # 历史面板：跟踪已批准的修正，支持 Undo/Redo
        self.history_entries: list[ReviewHistoryEntry] = []
        self.undo_stack: list[ReviewHistoryEntry] = []

        # 自动重试失败行追踪
        self.failed_item_ids: set[int] = set()
        self.auto_retry_count: int = 0

        # UI 刷新节流：高速审校时累积进度事件，定时批量刷新，避免 UI 冻结
        self.refresh_pending: bool = False
        self.refresh_timer: QTimer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(100)  # 100ms 批量刷新间隔
        self.refresh_timer.timeout.connect(self.flush_pending_refresh)

        # 载入配置
        config = Config().load()

        # 主容器
        self.container = QVBoxLayout(self)
        self.container.setSpacing(8)
        self.container.setContentsMargins(24, 24, 24, 24)

        # 滚动区域：head / output / scope / capture 放入滚动容器，避免 Game Capture 展开时挤压输出区
        scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)

        scroll_area = SingleDirectionScrollArea(orient=Qt.Orientation.Vertical)
        scroll_area.setWidget(scroll_content)
        scroll_area.setWidgetResizable(True)
        scroll_area.enableTransparentBackground()
        self.container.addWidget(scroll_area, 1)

        # 添加控件到滚动区域
        self.add_widget_head(self.scroll_layout, config)
        self.add_widget_output(self.scroll_layout, config)
        self.add_widget_scope(self.scroll_layout, config)
        self.add_widget_capture(self.scroll_layout, config)
        self.scroll_layout.addStretch(1)

        # 底部工具栏固定在滚动区域外
        self.add_widget_foot(self.container, config)

        # 订阅事件
        self.subscribe(Base.Event.REVIEW_TASK, self.on_review_task_event)
        self.subscribe(Base.Event.REVIEW_PROGRESS, self.on_review_progress)
        self.subscribe(Base.Event.REVIEW_REQUEST_STOP, self.on_review_stop)
        self.subscribe(Base.Event.PROJECT_LOADED, self.on_project_loaded)
        self.subscribe(Base.Event.PROJECT_UNLOADED, self.on_project_unloaded)
        self.subscribe_busy_state_events(self.on_engine_status_changed)

        # 实时任务数更新定时器
        self.task_update_timer = QTimer(self)
        self.task_update_timer.timeout.connect(self.update_task_card)
        self.task_update_timer.start(250)

        # 初始化按钮状态
        self.on_engine_status_changed(
            Base.Event.REVIEW_TASK,
            {"sub_event": Base.SubEvent.DONE},
        )

    # ==================== 头部：进度环 + 统计卡片 ====================

    def add_widget_head(self, parent: QVBoxLayout, config: Config) -> None:
        """添加头部区域：进度环（左侧）和统计卡片（右侧）。"""
        head_container = QWidget(self)
        head_hbox = QHBoxLayout(head_container)
        head_hbox.setContentsMargins(0, 0, 0, 0)
        head_hbox.setSpacing(8)

        # 进度环
        self.ring = ProgressRing()
        self.ring.setRange(0, RING_MAX_VALUE)
        self.ring.setValue(0)
        self.ring.setTextVisible(True)
        self.ring.setStrokeWidth(12)
        self.ring.setFixedSize(140, 140)
        self.ring.setFormat(Localizer.get().review_page_status_idle)

        ring_vbox_container = QWidget()
        ring_vbox = QVBoxLayout(ring_vbox_container)
        ring_vbox.addStretch(1)
        ring_vbox.addWidget(self.ring)
        head_hbox.addWidget(ring_vbox_container)

        head_hbox.addSpacing(8)

        # 统计卡片
        self.pass_card = DashboardCard(
            parent=self,
            title=Localizer.get().review_page_line_pass,
            value="0",
            unit="Line",
        )
        self.pass_card.setFixedSize(140, 140)

        self.fix_card = DashboardCard(
            parent=self,
            title=Localizer.get().review_page_line_fix,
            value="0",
            unit="Line",
        )
        self.fix_card.setFixedSize(140, 140)

        self.fail_card = DashboardCard(
            parent=self,
            title=Localizer.get().review_page_line_fail,
            value="0",
            unit="Line",
        )
        self.fail_card.setFixedSize(140, 140)

        self.error_card = DashboardCard(
            parent=self,
            title=Localizer.get().review_page_line_error,
            value="0",
            unit="Line",
        )
        self.error_card.setFixedSize(140, 140)

        head_hbox.addWidget(self.pass_card)
        head_hbox.addWidget(self.fix_card)
        head_hbox.addWidget(self.fail_card)
        head_hbox.addWidget(self.error_card)

        # 实时任务数卡片
        self.task_card = DashboardCard(
            parent=self,
            title=Localizer.get().translation_page_card_task,
            value="0",
            unit="Task",
        )
        self.task_card.setFixedSize(140, 140)
        head_hbox.addWidget(self.task_card)

        head_hbox.addStretch(1)

        parent.addWidget(head_container)

    def update_task_card(self) -> None:
        """更新实时任务数卡片。"""
        task = Engine.get().get_request_in_flight_count()
        self.task_card.set_value(str(task))

    # ==================== 输出窗口 + 历史面板 ====================

    def add_widget_output(self, parent: QVBoxLayout, config: Config) -> None:
        """添加 AI 输出展示区域（左）和可折叠历史面板（右），以及询问输入框。"""

        # 输出日志过滤栏
        filter_bar = QWidget(self)
        filter_hbox = QHBoxLayout(filter_bar)
        filter_hbox.setContentsMargins(0, 0, 0, 0)
        filter_hbox.setSpacing(8)

        self.output_filter_combo = ComboBox(self)
        loc = Localizer.get()
        self.output_filter_combo.addItems(
            [
                loc.review_page_filter_all,
                loc.review_page_filter_pass,
                loc.review_page_filter_fix,
                loc.review_page_filter_fail,
                loc.review_page_filter_error,
            ]
        )
        self.output_filter_combo.setCurrentIndex(0)
        self.output_filter_combo.currentIndexChanged.connect(
            self.on_output_filter_changed
        )
        self.output_filter_combo.setMinimumWidth(120)
        filter_hbox.addWidget(self.output_filter_combo)

        filter_hbox.addStretch(1)

        # 审批模式选择器（从专家设置移至此处，便于快速切换）
        self.approval_mode_combo = ComboBox(self)
        self.approval_mode_combo.addItems(
            [
                loc.review_page_approval_manual,
                loc.review_page_approval_auto,
                loc.review_page_approval_auto_skip,
            ]
        )
        mode_map = {
            Config.ReviewApprovalMode.MANUAL: 0,
            Config.ReviewApprovalMode.AUTO_ACCEPT: 1,
            Config.ReviewApprovalMode.AUTO_PAUSE_ON_FAIL: 2,
        }
        self.approval_mode_combo.setCurrentIndex(
            mode_map.get(config.review_approval_mode, 0)
        )
        self.approval_mode_combo.currentIndexChanged.connect(
            self.on_approval_mode_changed
        )
        self.approval_mode_combo.setMinimumWidth(160)
        filter_hbox.addWidget(self.approval_mode_combo)

        # 自动审批模式下行间延迟（秒）
        self.auto_delay_spin = SpinBox(self)
        self.auto_delay_spin.setRange(0, 60)
        self.auto_delay_spin.setValue(int(config.review_auto_delay))
        self.auto_delay_spin.setSuffix(f"  {loc.review_page_auto_delay}")
        self.auto_delay_spin.valueChanged.connect(self.on_auto_delay_changed)
        self.auto_delay_spin.setMinimumWidth(140)
        filter_hbox.addWidget(self.auto_delay_spin)

        parent.addWidget(filter_bar)

        # 水平容器：输出区 + 历史面板
        output_hbox_container = QWidget(self)
        output_hbox = QHBoxLayout(output_hbox_container)
        output_hbox.setContentsMargins(0, 0, 0, 0)
        output_hbox.setSpacing(0)

        # 输出文本区域（只读）
        self.output_text = PlainTextEdit(self)
        self.output_text.setReadOnly(True)
        self.output_text.setPlaceholderText(Localizer.get().review_page_desc)
        self.output_text.setMinimumHeight(280)
        output_hbox.addWidget(self.output_text, 1)

        # 折叠/展开按钮（竖条，位于输出区和历史面板之间）
        self.history_toggle_button = TransparentToolButton(BaseIcon.CHEVRON_RIGHT, self)
        self.history_toggle_button.setFixedWidth(24)
        self.history_toggle_button.clicked.connect(self.on_toggle_history_panel)
        self.history_toggle_button.setToolTip(Localizer.get().review_page_history)
        output_hbox.addWidget(self.history_toggle_button)

        # 历史面板（默认隐藏）
        self.history_panel = QWidget(self)
        history_vbox = QVBoxLayout(self.history_panel)
        history_vbox.setContentsMargins(8, 0, 0, 0)
        history_vbox.setSpacing(4)

        # 历史面板列表
        self.history_list = ListWidget(self.history_panel)
        self.history_list.setMinimumWidth(320)
        self.history_list.setMaximumWidth(480)
        history_vbox.addWidget(self.history_list, 1)

        # 历史面板操作按钮行
        history_btn_container = QWidget(self.history_panel)
        history_btn_hbox = QHBoxLayout(history_btn_container)
        history_btn_hbox.setContentsMargins(0, 0, 0, 0)
        history_btn_hbox.setSpacing(4)

        self.history_undo_button = PushButton(
            ICON_HISTORY_UNDO, Localizer.get().review_page_history_undo
        )
        self.history_undo_button.clicked.connect(self.on_history_undo)
        self.history_undo_button.setEnabled(False)
        history_btn_hbox.addWidget(self.history_undo_button)

        self.history_redo_button = PushButton(
            ICON_HISTORY_REDO, Localizer.get().review_page_history_redo
        )
        self.history_redo_button.clicked.connect(self.on_history_redo)
        self.history_redo_button.setEnabled(False)
        history_btn_hbox.addWidget(self.history_redo_button)

        self.history_save_button = PushButton(
            ICON_HISTORY_SAVE, Localizer.get().review_page_history_save
        )
        self.history_save_button.clicked.connect(self.on_history_save)
        self.history_save_button.setEnabled(False)
        history_btn_hbox.addWidget(self.history_save_button)

        history_vbox.addWidget(history_btn_container)

        self.history_panel.setVisible(False)
        output_hbox.addWidget(self.history_panel)

        parent.addWidget(output_hbox_container, 1)

        # 询问输入区域：输入框 + 发送按钮（水平布局）
        self.inquiry_container = QWidget(self)
        inquiry_hbox = QHBoxLayout(self.inquiry_container)
        inquiry_hbox.setContentsMargins(0, 0, 0, 0)
        inquiry_hbox.setSpacing(8)

        self.inquiry_input = PlainTextEdit(self.inquiry_container)
        self.inquiry_input.setPlaceholderText(
            Localizer.get().review_page_inquiry_placeholder
        )
        self.inquiry_input.setMaximumHeight(60)
        self.inquiry_input.installEventFilter(self)
        inquiry_hbox.addWidget(self.inquiry_input, 1)

        self.inquiry_send_button = PushButton(
            ICON_ACTION_SEND,
            Localizer.get().review_page_inquiry_send,
            self.inquiry_container,
        )
        self.inquiry_send_button.clicked.connect(self.on_inquiry_submit)
        inquiry_hbox.addWidget(self.inquiry_send_button)

        self.inquiry_container.setVisible(False)
        parent.addWidget(self.inquiry_container)

        # Ask AI 备选 dst 选择器：仅在 Ask AI 提取到新的 dst 时显示
        self.dst_selector_container = QWidget(self)
        dst_hbox = QHBoxLayout(self.dst_selector_container)
        dst_hbox.setContentsMargins(0, 0, 0, 0)
        dst_hbox.setSpacing(8)

        self.dst_selector_combo = ComboBox(self.dst_selector_container)
        self.dst_selector_combo.setMinimumWidth(400)
        dst_hbox.addWidget(self.dst_selector_combo, 1)

        self.dst_selector_container.setVisible(False)
        parent.addWidget(self.dst_selector_container)

    def eventFilter(self, obj: object, event: object) -> bool:
        """拦截询问输入框的 Enter 键：Enter 发送，Shift+Enter 换行。"""
        if obj is self.inquiry_input and isinstance(event, QKeyEvent):
            if event.type() == QKeyEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        # Shift+Enter：插入换行（默认行为）
                        return False
                    # Enter：发送
                    self.on_inquiry_submit()
                    return True
        return super().eventFilter(obj, event)

    # ==================== 审校范围 ====================

    def add_widget_scope(self, parent: QVBoxLayout, config: Config) -> None:
        """添加审校范围选择（全部文件/选定文件/失败行）和文件选择按钮。"""
        scope_card = SettingCard(
            title=Localizer.get().review_page_scope,
            description=Localizer.get().review_page_scope_desc,
            parent=self,
        )

        # 文件选择按钮（仅"选定文件"范围下可见）
        self.file_select_button = PushButton(
            Localizer.get().review_page_select_files, scope_card
        )
        self.file_select_button.setMinimumWidth(160)
        self.file_select_button.clicked.connect(self.on_select_files_clicked)
        self.file_select_button.setVisible(False)

        # 范围选择
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

        scope_card.add_right_widget(self.file_select_button)
        scope_card.add_right_widget(self.scope_combo)
        parent.addWidget(scope_card)

        # 起始行选择卡片
        starting_line_card = SettingCard(
            title=Localizer.get().review_page_starting_line,
            description=Localizer.get().review_page_starting_line_desc,
            parent=self,
        )
        self.starting_line_button = PushButton(
            Localizer.get().review_page_starting_line_from_beginning,
            starting_line_card,
        )
        self.starting_line_button.setMinimumWidth(160)
        self.starting_line_button.clicked.connect(self.on_select_starting_line_clicked)
        starting_line_card.add_right_widget(self.starting_line_button)
        parent.addWidget(starting_line_card)

        # 自动重试失败行开关
        auto_retry_card = SettingCard(
            title=Localizer.get().review_page_auto_retry_failed,
            description=Localizer.get().review_page_auto_retry_failed_desc,
            parent=self,
        )
        self.auto_retry_switch = SwitchButton(self)
        self.auto_retry_switch.setChecked(config.review_auto_retry_failed)
        self.auto_retry_switch.checkedChanged.connect(self.on_auto_retry_changed)
        auto_retry_card.add_right_widget(self.auto_retry_switch)
        parent.addWidget(auto_retry_card)

        # 已选文件列表（内部状态）
        self.selected_files: list[str] = []
        self.all_file_paths: list[str] = []

        # 尝试填充文件列表
        self.populate_file_list()

    def populate_file_list(self) -> None:
        """从当前工程获取可用文件列表。"""
        self.all_file_paths = []
        self.selected_files = []
        dm = DataManager.get()
        if not dm.is_loaded():
            self.update_file_button_text()
            return

        items = dm.get_all_items()
        file_paths: set[str] = set()
        for item in items:
            fp = item.get_file_path()
            if fp:
                file_paths.add(fp)

        self.all_file_paths = sorted(file_paths)
        self.update_file_button_text()

    def on_select_files_clicked(self) -> None:
        """打开文件多选对话框。"""
        if not self.all_file_paths:
            self.emit(
                Base.Event.TOAST,
                {
                    "type": Base.ToastType.WARNING,
                    "message": Localizer.get().review_page_no_file_selected,
                },
            )
            return

        dialog = MessageBox(
            Localizer.get().review_page_scope_file,
            "",
            self.window_ref,
        )
        dialog.yesButton.setText(Localizer.get().confirm)
        dialog.cancelButton.setText(Localizer.get().cancel)

        # 添加可勾选的文件列表
        file_list = ListWidget(dialog)
        file_list.setMinimumHeight(300)
        for fp in self.all_file_paths:
            list_item = QListWidgetItem(fp)
            list_item.setFlags(list_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if fp in self.selected_files:
                list_item.setCheckState(Qt.CheckState.Checked)
            else:
                list_item.setCheckState(Qt.CheckState.Unchecked)
            file_list.addItem(list_item)

        dialog.textLayout.addWidget(file_list)

        if not dialog.exec():
            return

        # 收集选中的文件
        self.selected_files = []
        for i in range(file_list.count()):
            list_item = file_list.item(i)
            if list_item and list_item.checkState() == Qt.CheckState.Checked:
                self.selected_files.append(self.all_file_paths[i])

        self.update_file_button_text()

    def update_file_button_text(self) -> None:
        """更新文件选择按钮上的文案。"""
        count = len(self.selected_files)
        if count == 0:
            self.file_select_button.setText(Localizer.get().review_page_select_files)
        else:
            self.file_select_button.setText(
                Localizer.get().review_page_files_selected.replace(
                    "{COUNT}", str(count)
                )
            )

    def on_scope_changed(self, index: int) -> None:
        """审校范围变更：控制文件选择按钮可见性。"""
        self.file_select_button.setVisible(index == SCOPE_FILE)

    def on_auto_retry_changed(self, checked: bool) -> None:
        """自动重试开关变更。"""
        config = Config().load()
        config.review_auto_retry_failed = checked
        config.save()

    def on_output_filter_changed(self, index: int) -> None:
        """输出日志过滤条件变更，重建显示内容。"""
        self.refresh_output_display(immediate=True)

    def on_approval_mode_changed(self, index: int) -> None:
        """审批模式切换，保存到配置。审校过程中实时生效。"""
        reverse_map = {
            0: Config.ReviewApprovalMode.MANUAL,
            1: Config.ReviewApprovalMode.AUTO_ACCEPT,
            2: Config.ReviewApprovalMode.AUTO_PAUSE_ON_FAIL,
        }
        config = Config().load()
        config.review_approval_mode = reverse_map.get(
            index, Config.ReviewApprovalMode.MANUAL
        )
        config.save()

    def on_auto_delay_changed(self, value: int) -> None:
        """行间延迟变更，保存到配置。审校过程中实时生效。"""
        config = Config().load()
        config.review_auto_delay = float(value)
        config.save()

    def build_filtered_output(self) -> str:
        """根据当前过滤条件，从 output_entries 构建显示文本。"""
        filter_index = self.output_filter_combo.currentIndex()
        if filter_index == FILTER_ALL:
            # 全部：显示所有条目
            return "\n".join(text for _, text in self.output_entries)

        target_verdict = FILTER_INDEX_TO_VERDICT.get(filter_index, "")

        filtered = [text for v, text in self.output_entries if v == target_verdict]
        return "\n".join(filtered)

    def refresh_output_display(self, *, immediate: bool = False) -> None:
        """请求刷新输出文本区域。

        高速审校时事件可能在极短时间内连续到达，每次都全量重建文本会导致 UI 冻结。
        默认走节流路径：标记 pending 后由 refresh_timer 统一刷新，保证至少 100ms 间隔。
        待批/Ask AI 等交互性事件使用 immediate=True 绕过节流，确保用户立即看到结果。
        """
        if immediate or self.awaiting_approval:
            self.do_refresh_output_display()
            return

        # 标记待刷新，定时器触发后统一执行
        self.refresh_pending = True
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()

    def flush_pending_refresh(self) -> None:
        """定时器回调：执行被节流的输出刷新。"""
        if self.refresh_pending:
            self.refresh_pending = False
            self.do_refresh_output_display()

    def do_refresh_output_display(self) -> None:
        """实际重建并刷新输出文本区域，保持统一的显示顺序：
        [已完成结果] → [待批结果] → [Ask AI 对话]
        """
        parts: list[str] = [self.build_filtered_output()]

        # 待批结果显示在已完成结果之后
        if self.awaiting_approval and self.awaiting_result_line:
            parts.append(self.awaiting_result_line)

        # Ask AI 对话显示在待批结果之后
        if self.inquiry_lines:
            parts.append("\n".join(self.inquiry_lines))

        self.output_text.setPlainText("\n\n".join(p for p in parts if p))
        self.scroll_output_to_bottom()

    def scroll_output_to_bottom(self) -> None:
        """将输出文本区域滚动到底部（延迟执行以确保布局更新完成）。"""

        def do_scroll() -> None:
            scrollbar = self.output_text.verticalScrollBar()
            if scrollbar:
                scrollbar.setValue(scrollbar.maximum())

        QTimer.singleShot(0, do_scroll)

    @staticmethod
    def extract_dst_from_answer(answer: str) -> list[str]:
        """从 Ask AI 回答中提取 dst: 行作为备选翻译。

        匹配引擎提示词要求的格式 "dst: <corrected translation>"。
        """
        results: list[str] = []
        for match in re.finditer(r"^\s*dst:\s*(.+)", answer, re.MULTILINE):
            value = match.group(1).strip()
            if value:
                results.append(value)
        return results

    def refresh_dst_selector(self) -> None:
        """根据当前 dst_alternatives 刷新选择器内容并显示。"""
        if not self.dst_alternatives:
            self.hide_dst_selector()
            return

        self.dst_selector_combo.clear()
        loc = Localizer.get()
        for i, dst in enumerate(self.dst_alternatives):
            label = loc.review_page_dst_option.replace("{INDEX}", str(i + 1)).replace(
                "{TEXT}", dst
            )
            self.dst_selector_combo.addItem(label)
        # 默认选中最新的备选
        self.dst_selector_combo.setCurrentIndex(len(self.dst_alternatives) - 1)
        self.dst_selector_container.setVisible(True)

    def hide_dst_selector(self) -> None:
        """隐藏备选 dst 选择器。"""
        self.dst_selector_container.setVisible(False)
        self.dst_selector_combo.clear()

    def get_selected_dst(self) -> str:
        """获取用户在选择器中选中的备选 dst，若无则返回空字符串。"""
        if not self.dst_selector_container.isVisible():
            return ""
        index = self.dst_selector_combo.currentIndex()
        if 0 <= index < len(self.dst_alternatives):
            return self.dst_alternatives[index]
        return ""

    def on_select_starting_line_clicked(self) -> None:
        """打开起始行选择对话框，展示可审校条目列表供用户选择。

        当审校范围为"选定文件"时，仅展示已选文件中的条目。
        """
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

        all_items = dm.get_all_items()

        # 当范围为"选定文件"时，仅展示已选文件中的条目
        scope_index = self.scope_combo.currentIndex()
        if scope_index == SCOPE_FILE and self.selected_files:
            selected_set = set(self.selected_files)
            all_items = [
                item for item in all_items if item.get_file_path() in selected_set
            ]

        items = self.filter_translated_items(all_items)
        if not items:
            TaskRunnerLifecycle.emit_no_items_warning(self)
            return

        dialog = MessageBox(
            Localizer.get().review_page_starting_line,
            "",
            self.window_ref,
        )
        dialog.yesButton.setText(Localizer.get().confirm)
        dialog.cancelButton.setText(Localizer.get().cancel)

        line_list = ListWidget(dialog)
        line_list.setMinimumHeight(500)
        line_list.setMinimumWidth(600)
        for i, item in enumerate(items):
            # 截断过长文本以保持列表可读
            max_len = LINE_PREVIEW_MAX_LENGTH
            src = (item.src[:max_len] + " …") if len(item.src) > max_len else item.src
            dst = (item.dst[:max_len] + " …") if len(item.dst) > max_len else item.dst
            label = f"{i + 1}.  {src}  →  {dst}"
            line_list.addItem(label)

        # 选中当前起始行
        if self.starting_line_index < line_list.count():
            line_list.setCurrentRow(self.starting_line_index)
        else:
            line_list.setCurrentRow(0)

        dialog.textLayout.addWidget(line_list)

        if not dialog.exec():
            return

        row = line_list.currentRow()
        if row < 0:
            return

        self.starting_line_index = row
        self.update_starting_line_button_text()

    def update_starting_line_button_text(self) -> None:
        """根据当前选择更新起始行按钮文案。"""
        if self.starting_line_index <= 0:
            self.starting_line_button.setText(
                Localizer.get().review_page_starting_line_from_beginning
            )
        else:
            self.starting_line_button.setText(
                Localizer.get().review_page_starting_line_selected.replace(
                    "{LINE}", str(self.starting_line_index + 1)
                )
            )

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
                Localizer.get().review_page_capture_video_audio,
                Localizer.get().review_page_capture_audio,
            ]
        )
        capture_mode_map = {
            Config.CaptureMode.IMAGE: 0,
            Config.CaptureMode.VIDEO: 1,
            Config.CaptureMode.VIDEO_AUDIO: 2,
            Config.CaptureMode.AUDIO: 3,
        }
        self.capture_mode_combo.setCurrentIndex(
            capture_mode_map.get(config.review_capture_mode, 0)
        )
        self.capture_mode_combo.currentIndexChanged.connect(
            self.on_capture_mode_changed
        )
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
        self.capture_window_select_button = PushButton(
            Localizer.get().review_page_capture_window_select, window_card
        )
        self.capture_window_select_button.clicked.connect(self.on_select_window_clicked)
        window_card.add_right_widget(self.capture_window_select_button)
        window_card.add_right_widget(self.capture_window_edit)
        parent.addWidget(window_card)
        self.capture_window_card = window_card

        # 热键（按键捕获）
        hotkey_card = SettingCard(
            title=Localizer.get().review_page_capture_hotkey,
            description=Localizer.get().review_page_capture_hotkey_desc,
            parent=self,
        )
        self.capture_hotkey_edit = HotkeyLineEdit(hotkey_card)
        self.capture_hotkey_edit.setMinimumWidth(160)
        self.capture_hotkey_edit.setReadOnly(False)
        self.capture_hotkey_edit.setText(config.review_capture_hotkey)
        self.capture_hotkey_edit.setPlaceholderText(
            Localizer.get().review_page_capture_hotkey_placeholder
        )
        self.capture_hotkey_edit.hotkey_changed.connect(
            lambda _: self.on_capture_hotkey_changed()
        )
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

    # ==================== 底部工具栏 ====================

    def add_widget_foot(self, parent: QVBoxLayout, config: Config) -> None:
        """添加底部命令栏：开始/停止 + 重置 + 审批操作按钮 + Ask AI 独立按钮。"""
        self.command_bar = CommandBarCard()
        self.command_bar.set_minimum_width(960)

        # 开始/继续审校按钮
        self.start_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_START,
                Localizer.get().review_page_start,
                self.command_bar,
                triggered=self.on_start_review,
            )
        )

        # 停止审校按钮
        self.stop_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_STOP,
                Localizer.get().review_page_stop,
                self.command_bar,
                triggered=self.on_stop_review,
            )
        )
        self.stop_action.setEnabled(False)

        # 即时暂停按钮：自动审批模式下临时暂停下一行
        self.pause_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_PAUSE,
                Localizer.get().review_page_pause,
                self.command_bar,
                triggered=self.on_pause_next,
            )
        )
        self.pause_action.setEnabled(False)

        self.command_bar.add_separator()

        # 重置按钮（弹出菜单：重置失败行 / 重置全部）
        self.reset_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_RESET,
                Localizer.get().review_page_reset,
                self.command_bar,
                triggered=self.on_reset_clicked,
            )
        )
        self.reset_action.installEventFilter(
            ToolTipFilter(self.reset_action, 300, ToolTipPosition.TOP)
        )
        self.reset_action.setToolTip(Localizer.get().review_page_reset_tooltip)
        self.reset_action.setEnabled(False)

        self.command_bar.add_separator()

        # 审批操作按钮
        self.approve_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_APPROVE,
                Localizer.get().review_page_approve,
                self.command_bar,
                triggered=self.on_approve,
            )
        )
        self.approve_action.setEnabled(False)

        self.skip_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_SKIP,
                Localizer.get().review_page_skip,
                self.command_bar,
                triggered=self.on_skip,
            )
        )
        self.skip_action.setEnabled(False)

        self.deny_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_DENY,
                Localizer.get().review_page_deny,
                self.command_bar,
                triggered=self.on_deny,
            )
        )
        self.deny_action.setEnabled(False)

        self.retry_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_RETRY,
                Localizer.get().review_page_retry,
                self.command_bar,
                triggered=self.on_retry,
            )
        )
        self.retry_action.setEnabled(False)

        self.command_bar.add_stretch(1)

        # Ask AI 独立按钮：放在 CommandBar 之外，避免被自动溢出隐藏到 "..." 菜单
        self.ask_ai_button = PushButton(
            ICON_ACTION_ASK, Localizer.get().review_page_ask_ai
        )
        self.ask_ai_button.setEnabled(False)
        self.ask_ai_button.clicked.connect(self.on_ask_ai)
        self.command_bar.add_widget(self.ask_ai_button)

        parent.addWidget(self.command_bar)

    # ==================== 审校启动 ====================

    def on_start_review(self) -> None:
        """根据当前范围选择启动审校。支持 Continue 模式恢复未完成的会话。"""
        # 如果有未完成的会话，继续从上次断点
        if self.has_review_progress():
            remaining = self.review_items[self.reviewed_count :]
            if not remaining:
                TaskRunnerLifecycle.emit_no_items_warning(self)
                return

            # 已审校的条目作为后续条目的上文
            context_items = self.review_items[: self.reviewed_count]
            self.emit(
                Base.Event.REVIEW_TASK,
                {
                    "sub_event": Base.SubEvent.REQUEST,
                    "items": remaining,
                    "context_items": context_items,
                },
            )
            return

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
            # 审校选定文件的已翻译条目（支持多选）
            if not self.selected_files:
                self.emit(
                    Base.Event.TOAST,
                    {
                        "type": Base.ToastType.WARNING,
                        "message": Localizer.get().review_page_no_file_selected,
                    },
                )
                return
            selected_set = set(self.selected_files)
            file_items = [
                item for item in items if item.get_file_path() in selected_set
            ]
            review_items = self.filter_translated_items(file_items)
            scope_desc = Localizer.get().review_page_scope_file

        elif scope_index == SCOPE_FAILED:
            # 仅审校失败 / 错误行
            review_items = [
                item for item in items if item.status == Base.ProjectStatus.ERROR
            ]
            scope_desc = Localizer.get().review_page_scope_failed

        else:
            return

        if not review_items:
            TaskRunnerLifecycle.emit_no_items_warning(self)
            return

        # 应用起始行偏移，并保留上文条目供引擎使用
        context_items: list[Item] = []
        if self.starting_line_index > 0:
            # 取起始行之前的条目作为上文（不超过合理上限）
            context_items = review_items[: self.starting_line_index]
            review_items = review_items[self.starting_line_index :]
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

        # 清空输出并开始新会话
        self.output_entries = []
        self.output_text.clear()
        self.review_items = review_items
        self.reviewed_count = 0
        self.failed_item_ids = set()
        self.auto_retry_count = 0

        self.emit(
            Base.Event.REVIEW_TASK,
            {
                "sub_event": Base.SubEvent.REQUEST,
                "items": review_items,
                "context_items": context_items,
            },
        )

    def has_review_progress(self) -> bool:
        """当前是否有未完成的审校会话。"""
        return (
            len(self.review_items) > 0
            and self.reviewed_count > 0
            and self.reviewed_count < len(self.review_items)
        )

    @staticmethod
    def filter_translated_items(items: list[Item]) -> list[Item]:
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

    def start_review_items(self, items: list[Item]) -> None:
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

    def auto_retry_failed_items(self) -> None:
        """自动重试失败的审校行。"""
        dm = DataManager.get()
        if not dm.is_loaded():
            return

        items = dm.get_all_items()
        item_id_set = set(self.failed_item_ids)
        retry_items = [item for item in items if (item.id or 0) in item_id_set]
        if not retry_items:
            return

        self.failed_item_ids.clear()
        self.review_items = retry_items
        self.reviewed_count = 0

        self.emit(
            Base.Event.TOAST,
            {
                "type": Base.ToastType.INFO,
                "message": Localizer.get().review_page_auto_retry_started.replace(
                    "{COUNT}", str(len(retry_items))
                ),
            },
        )

        self.emit(
            Base.Event.REVIEW_TASK,
            {
                "sub_event": Base.SubEvent.REQUEST,
                "items": retry_items,
            },
        )

    def on_stop_review(self) -> None:
        """停止审校。"""
        self.emit(
            Base.Event.REVIEW_REQUEST_STOP,
            {"sub_event": Base.SubEvent.REQUEST},
        )

    # ==================== 审批操作占位 ====================

    def on_approve(self) -> None:
        """通过当前审校建议，通知引擎继续。若用户选择了备选 dst 则一并传递。"""
        if not self.awaiting_approval:
            return
        self.awaiting_approval = False
        self.update_buttons()
        decision_data: dict[str, str] = {"decision": "approve"}
        selected = self.get_selected_dst()
        if selected:
            decision_data["custom_dst"] = selected
        self.clear_inquiry_state()
        self.emit(Base.Event.REVIEW_USER_DECISION, decision_data)

    def on_skip(self) -> None:
        """跳过当前行，不应用任何修改，视为通过。"""
        if not self.awaiting_approval:
            return
        self.awaiting_approval = False
        self.update_buttons()
        self.clear_inquiry_state()
        self.emit(
            Base.Event.REVIEW_USER_DECISION,
            {"decision": "skip"},
        )

    def on_deny(self) -> None:
        """拒绝当前审校建议，跳过本行。"""
        if not self.awaiting_approval:
            return
        self.awaiting_approval = False
        self.update_buttons()
        self.clear_inquiry_state()
        self.emit(
            Base.Event.REVIEW_USER_DECISION,
            {"decision": "deny"},
        )

    def on_retry(self) -> None:
        """重试当前行的审校。"""
        if not self.awaiting_approval:
            return
        self.awaiting_approval = False
        self.update_buttons()
        self.clear_inquiry_state()
        self.emit(
            Base.Event.REVIEW_USER_DECISION,
            {"decision": "retry"},
        )

    def on_pause_next(self) -> None:
        """请求暂停下一行审校以进行手动审批。"""
        self.emit(Base.Event.REVIEW_PAUSE_NEXT, {})

    def on_ask_ai(self) -> None:
        """切换询问输入区域的可见性，聚焦输入框。"""
        visible = not self.inquiry_container.isVisible()
        self.inquiry_container.setVisible(visible)
        if visible:
            self.inquiry_input.setFocus()

    def on_inquiry_submit(self) -> None:
        """发送询问内容给 AI 引擎。"""
        text = self.inquiry_input.toPlainText().strip()
        if not text:
            return

        # 将问题追加到 Ask AI 对话记录（显示在待批结果下方）
        loc = Localizer.get()
        q_line = loc.review_page_inquiry_question.replace("{TEXT}", text)
        self.inquiry_lines.append(q_line)

        self.refresh_output_display(immediate=True)

        # 清空输入框
        self.inquiry_input.clear()

        # 发送 inquiry 决定给引擎
        self.emit(
            Base.Event.REVIEW_USER_DECISION,
            {"decision": "inquiry", "text": text},
        )

    def clear_inquiry_state(self) -> None:
        """清空 Ask AI 对话记录和备选 dst（审批操作后调用）。"""
        self.inquiry_lines = []
        self.dst_alternatives = []
        self.hide_dst_selector()
        self.inquiry_container.setVisible(False)

    def on_reset_clicked(self) -> None:
        """弹出重置菜单：重置失败行 / 重置全部。"""

        def confirm_and_reset(message: str, reset_failed_only: bool) -> None:
            message_box = MessageBox(
                Localizer.get().alert,
                message,
                self.window_ref,
            )
            message_box.yesButton.setText(Localizer.get().confirm)
            message_box.cancelButton.setText(Localizer.get().cancel)
            if message_box.exec():
                if reset_failed_only:
                    self.do_reset_failed()
                else:
                    self.do_reset_all()

        menu = RoundMenu("", self.reset_action)
        menu.addAction(
            Action(
                ICON_ACTION_RESET_FAILED,
                Localizer.get().review_page_reset_failed,
                triggered=lambda: confirm_and_reset(
                    Localizer.get().review_page_alert_reset_failed,
                    reset_failed_only=True,
                ),
            )
        )
        menu.addSeparator()
        menu.addAction(
            Action(
                ICON_ACTION_RESET_ALL,
                Localizer.get().review_page_reset_all,
                triggered=lambda: confirm_and_reset(
                    Localizer.get().review_page_alert_reset_all,
                    reset_failed_only=False,
                ),
            )
        )
        global_pos = self.reset_action.mapToGlobal(QPoint(0, 0))
        menu.exec(global_pos, ani=True, aniType=MenuAnimationType.PULL_UP)

    def do_reset_failed(self) -> None:
        """重置失败行：回退 reviewed_count 使 Continue 可重新审校出错的行。

        由于出错行可能散布在已审校区间中，这里将 reviewed_count 重置为 0，
        让 Continue 从头重审全部条目。已通过/已修正的行会被引擎自动跳过或快速重审。
        """
        self.reviewed_count = 0
        self.error_card.set_value("0")
        self.emit(
            Base.Event.TOAST,
            {
                "type": Base.ToastType.SUCCESS,
                "message": Localizer.get().review_page_reset_failed,
            },
        )
        self.update_buttons()

    def do_reset_all(self) -> None:
        """重置全部：清空整个审校会话。"""
        self.review_items = []
        self.reviewed_count = 0
        self.output_entries = []
        self.output_text.clear()
        self.history_entries = []
        self.undo_stack = []
        self.failed_item_ids = set()
        self.auto_retry_count = 0
        self.inquiry_lines = []
        self.dst_alternatives = []
        self.hide_dst_selector()
        self.refresh_history_list()
        self.clear_stats()
        self.update_buttons()

    # ==================== 历史面板 ====================

    def on_toggle_history_panel(self) -> None:
        """切换历史面板的可见性。"""
        visible = not self.history_panel.isVisible()
        self.history_panel.setVisible(visible)
        # 切换箭头方向：展开时指左（可收起），收起时指右（可展开）
        if visible:
            self.history_toggle_button.setIcon(BaseIcon.CHEVRON_LEFT)
        else:
            self.history_toggle_button.setIcon(BaseIcon.CHEVRON_RIGHT)

    def add_history_entry(self, entry: ReviewHistoryEntry) -> None:
        """将一条已批准的修正添加到历史记录，清空 redo 栈。"""
        self.history_entries.append(entry)
        self.undo_stack.clear()
        self.refresh_history_list()
        self.update_history_buttons()

    @staticmethod
    def truncate_preview(text: str, max_len: int) -> str:
        """将文本截断到指定长度，超出部分用省略号替代。"""
        if len(text) > max_len:
            return text[:max_len] + "…"
        return text

    def refresh_history_list(self) -> None:
        """根据当前 history_entries 刷新历史列表控件。"""
        self.history_list.clear()
        if not self.history_entries:
            self.history_list.addItem(Localizer.get().review_page_history_empty)
            return

        for entry in self.history_entries:
            # FIX 结果显示 old → new 对比；其他仅显示译文摘要
            if entry.verdict == "FIX" and entry.corrected:
                old_preview = self.truncate_preview(entry.original_dst, 30)
                new_preview = self.truncate_preview(entry.corrected, 30)
                label = f"[{entry.verdict}] {old_preview} → {new_preview}"
            else:
                dst_preview = self.truncate_preview(entry.original_dst, 40)
                label = f"[{entry.verdict}] {dst_preview}"
            self.history_list.addItem(label)

    def update_history_buttons(self) -> None:
        """更新历史面板按钮可用性。"""
        has_history = len(self.history_entries) > 0
        has_undo = len(self.undo_stack) > 0

        self.history_undo_button.setEnabled(has_history)
        self.history_redo_button.setEnabled(has_undo)
        self.history_save_button.setEnabled(has_history)

    def on_history_undo(self) -> None:
        """撤销最近一条已批准的修正：还原 item.dst 为 original_dst。"""
        if not self.history_entries:
            return

        entry = self.history_entries.pop()
        self.undo_stack.append(entry)

        # 还原数据层中的译文
        try:
            dm = DataManager.get()
            if dm.is_loaded():
                items = dm.get_all_items()
                for item in items:
                    if (item.id or 0) == entry.item_id:
                        item.dst = entry.original_dst
                        dm.save_item(item)
                        break
        except Exception as e:
            LogManager.get().warning(
                f"Failed to undo review fix for item {entry.item_id}", e
            )

        self.refresh_history_list()
        self.update_history_buttons()

        self.emit(
            Base.Event.TOAST,
            {
                "type": Base.ToastType.SUCCESS,
                "message": Localizer.get().review_page_history_undo_success,
            },
        )

    def on_history_redo(self) -> None:
        """重做最近一条撤销的修正：重新应用 corrected 到 item.dst。"""
        if not self.undo_stack:
            return

        entry = self.undo_stack.pop()
        self.history_entries.append(entry)

        # 重新应用修正到数据层
        if entry.corrected:
            try:
                dm = DataManager.get()
                if dm.is_loaded():
                    items = dm.get_all_items()
                    for item in items:
                        if (item.id or 0) == entry.item_id:
                            item.dst = entry.corrected
                            dm.save_item(item)
                            break
            except Exception as e:
                LogManager.get().warning(
                    f"Failed to redo review fix for item {entry.item_id}", e
                )

        self.refresh_history_list()
        self.update_history_buttons()

        self.emit(
            Base.Event.TOAST,
            {
                "type": Base.ToastType.SUCCESS,
                "message": Localizer.get().review_page_history_redo_success,
            },
        )

    def on_history_save(self) -> None:
        """立即保存所有历史修正行到 .lg 文件（通过 DataManager 持久化）。

        DataManager.save_item() 已在 apply_fix_if_needed 中调用过，
        此操作确认当前所有修正已被持久化，并给用户一个明确反馈。
        """
        if not self.history_entries:
            return

        count = 0
        try:
            dm = DataManager.get()
            if dm.is_loaded():
                items = dm.get_all_items()
                item_map = {(item.id or 0): item for item in items}
                for entry in self.history_entries:
                    item = item_map.get(entry.item_id)
                    if item is not None:
                        dm.save_item(item)
                        count += 1
        except Exception as e:
            LogManager.get().warning("Failed to save review history", e)

        self.emit(
            Base.Event.TOAST,
            {
                "type": Base.ToastType.SUCCESS,
                "message": Localizer.get().review_page_history_save_success.replace(
                    "{COUNT}", str(count)
                ),
            },
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
            self.awaiting_approval = False
            self.update_buttons()
            final_status = data.get("final_status", "")

            # 自动重试失败行
            if final_status == "SUCCESS" and self.failed_item_ids:
                config = Config().load()
                if config.review_auto_retry_failed:
                    max_retries = config.max_round or AUTO_RETRY_DEFAULT_LIMIT
                    if self.auto_retry_count < max_retries:
                        self.auto_retry_count += 1
                        self.auto_retry_failed_items()
                        return

            if final_status == "SUCCESS":
                self.emit(
                    Base.Event.TOAST,
                    {
                        "type": Base.ToastType.SUCCESS,
                        "message": Localizer.get().review_page_done,
                    },
                )

    def set_ring_status(self, status_text: str) -> None:
        """更新进度环的状态文字和百分比。"""
        percent = self.ring.value() / RING_MAX_VALUE
        self.ring.setFormat(f"{status_text}\n{percent * 100:.2f}%")

    def on_review_progress(self, event: Base.Event, data: dict) -> None:
        """响应审校进度更新，将每行审校结果追加到输出区域。"""
        # 处理 Ask AI 查询响应（不含常规进度字段）
        inquiry_response = data.get("inquiry_response")
        if inquiry_response:
            self.handle_inquiry_response(inquiry_response)
            return

        total = data.get("total_line", 0)
        reviewed = data.get("reviewed_line", 0)
        pass_count = data.get("pass_line", 0)
        fix_count = data.get("fix_line", 0)
        fail_count = data.get("fail_line", 0)
        error_count = data.get("error_line", 0)
        awaiting = data.get("awaiting_approval", False)
        result_data = data.get("result")

        # 跟踪审校会话进度（用于 Continue 功能）
        if not awaiting and reviewed > 0:
            self.reviewed_count = len(self.review_items) - total + reviewed

        # 更新进度环
        percent = reviewed / max(1, total)
        self.ring.setValue(int(percent * RING_MAX_VALUE))
        self.set_ring_status(Localizer.get().review_page_status_reviewing)

        # 更新统计卡片
        self.pass_card.set_value(str(pass_count))
        self.fix_card.set_value(str(fix_count))
        self.fail_card.set_value(str(fail_count))
        self.error_card.set_value(str(error_count))

        # 构建审校结果行并追加到输出
        if result_data:
            line = self.format_result_line(
                result_data,
                reviewed,
                total,
                awaiting,
            )
            if awaiting:
                # 新的待批条目：清空上一条的 Ask AI 对话和备选 dst
                self.inquiry_lines = []
                self.dst_alternatives = []
                self.hide_dst_selector()

                # 待批结果不追加到永久日志，仅在末尾展示
                self.awaiting_approval = True
                self.awaiting_result_line = line
                self.update_buttons()
            else:
                verdict = result_data.get("verdict", "")
                self.output_entries.append((verdict, line))

                # 跟踪失败行（用于自动重试）
                if verdict in ("FAIL", "ERROR"):
                    item_id = result_data.get("item_id", 0)
                    if item_id:
                        self.failed_item_ids.add(item_id)

                # 已批准的 FIX/PASS 结果加入历史面板
                approved = data.get("approved", False)
                if approved and verdict in ("FIX", "PASS"):
                    entry = ReviewHistoryEntry(
                        item_id=result_data.get("item_id", 0),
                        src=result_data.get("src", ""),
                        original_dst=result_data.get("original_dst", ""),
                        corrected=result_data.get("corrected", ""),
                        verdict=verdict,
                        reason=result_data.get("reason", ""),
                    )
                    self.add_history_entry(entry)
            self.refresh_output_display()
        else:
            # 回退：仅显示进度行
            progress_text = (
                Localizer.get()
                .review_page_progress.replace("{CURRENT}", str(reviewed))
                .replace("{TOTAL}", str(total))
            )
            self.output_text.setPlainText(progress_text)
            self.scroll_output_to_bottom()

    @staticmethod
    def format_result_line(
        result_data: dict,
        reviewed: int,
        total: int,
        awaiting: bool,
    ) -> str:
        """将单条审校结果格式化为可读文本行。"""
        loc = Localizer.get()
        verdict = result_data.get("verdict", "")
        corrected = result_data.get("corrected", "")
        reason = result_data.get("reason", "")
        original_dst = result_data.get("original_dst", "")
        src = result_data.get("src", "")

        # 映射 verdict 到本地化标签
        verdict_label_map = {
            "PASS": loc.review_page_line_pass,
            "FIX": loc.review_page_line_fix,
            "FAIL": loc.review_page_line_fail,
            "ERROR": loc.review_page_line_error,
        }
        verdict_label = verdict_label_map.get(verdict, verdict)

        # 行首：[verdict] Line X/Y
        header = f"[{verdict_label}] {reviewed}/{total}"
        if awaiting:
            header += f"  ⏳ {loc.review_page_awaiting}"

        parts: list[str] = [header]

        # 原文摘要
        if src:
            parts.append(f"  src: {src}")

        # FIX 结果：展示修正前后对比
        if verdict == "FIX" and corrected:
            parts.append(f"  old: {original_dst}")
            parts.append(f"  new: {corrected}")
        elif original_dst:
            parts.append(f"  dst: {original_dst}")

        # 原因
        if reason:
            parts.append(f"  {loc.review_page_result_reason}: {reason}")

        return "\n".join(parts)

    def handle_inquiry_response(self, inquiry_response: dict) -> None:
        """将 Ask AI 的回答追加到对话区域，并解析可能的备选 dst。"""
        loc = Localizer.get()
        answer = inquiry_response.get("answer", "")
        a_line = loc.review_page_inquiry_answer.replace("{TEXT}", answer)
        self.inquiry_lines.append(a_line)

        # 从 AI 回答中提取 dst: 行作为备选翻译
        suggested = self.extract_dst_from_answer(answer)
        if suggested:
            for dst in suggested:
                if dst not in self.dst_alternatives:
                    self.dst_alternatives.append(dst)
            self.refresh_dst_selector()

        self.refresh_output_display(immediate=True)

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
        self.populate_file_list()

    def on_project_unloaded(self, event: Base.Event, data: dict) -> None:
        """工程卸载后清空文件列表、审校会话和输出。"""
        self.all_file_paths = []
        self.selected_files = []
        self.starting_line_index = 0
        self.review_items = []
        self.reviewed_count = 0
        self.output_entries = []
        self.history_entries = []
        self.undo_stack = []
        self.failed_item_ids = set()
        self.auto_retry_count = 0
        self.inquiry_lines = []
        self.dst_alternatives = []
        self.hide_dst_selector()
        self.refresh_history_list()
        self.update_file_button_text()
        self.update_starting_line_button_text()
        self.output_text.clear()
        self.clear_stats()

    def clear_stats(self) -> None:
        """重置统计卡片和进度环。"""
        self.ring.setValue(0)
        self.ring.setFormat(Localizer.get().review_page_status_idle)
        self.pass_card.set_value("0")
        self.fix_card.set_value("0")
        self.fail_card.set_value("0")
        self.error_card.set_value("0")

    def update_buttons(self) -> None:
        """根据引擎状态更新按钮可用性。"""
        status = Engine.get().get_status()
        is_busy = Base.is_engine_busy(status)
        is_reviewing = status == Base.TaskStatus.REVIEWING
        is_stopping = status == Base.TaskStatus.STOPPING

        self.start_action.setEnabled(not is_busy)
        self.stop_action.setEnabled(is_reviewing and not is_stopping)

        # 动态切换开始/继续按钮文案和图标
        if self.has_review_progress():
            self.start_action.setText(Localizer.get().review_page_continue)
            self.start_action.setIcon(ICON_ACTION_CONTINUE)
        else:
            self.start_action.setText(Localizer.get().review_page_start)
            self.start_action.setIcon(ICON_ACTION_START)

        # 重置按钮：空闲且有会话数据时可用
        self.reset_action.setEnabled(not is_busy and len(self.review_items) > 0)

        # 暂停按钮：审校进行中且未在等待审批时可用（让用户在自动模式下临时暂停）
        self.pause_action.setEnabled(
            is_reviewing and not is_stopping and not self.awaiting_approval
        )

        # 审批按钮仅在等待用户审批时启用
        can_approve = is_reviewing and not is_stopping and self.awaiting_approval
        self.approve_action.setEnabled(can_approve)
        self.skip_action.setEnabled(can_approve)
        self.deny_action.setEnabled(can_approve)
        self.retry_action.setEnabled(can_approve)
        self.ask_ai_button.setEnabled(is_reviewing and not is_stopping)

        # 审校过程中禁用设置
        self.scope_combo.setEnabled(not is_busy)
        self.file_select_button.setEnabled(not is_busy)
        self.starting_line_button.setEnabled(not is_busy)
        self.auto_retry_switch.setEnabled(not is_busy)
        self.capture_switch.setEnabled(not is_busy)
        self.capture_mode_combo.setEnabled(not is_busy)
        self.capture_window_edit.setEnabled(not is_busy)
        self.capture_hotkey_edit.setEnabled(not is_busy)
        self.capture_auto_switch.setEnabled(not is_busy)

        # 更新进度环状态文字
        if is_stopping:
            self.set_ring_status(Localizer.get().review_page_status_stopping)
        elif not is_reviewing and not is_busy:
            # 保留最终百分比
            if self.ring.value() > 0:
                self.set_ring_status(Localizer.get().review_page_status_idle)

    # ==================== 设置变更回调 ====================

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
            2: Config.CaptureMode.VIDEO_AUDIO,
            3: Config.CaptureMode.AUDIO,
        }
        config = Config().load()
        config.review_capture_mode = mode_map.get(index, Config.CaptureMode.IMAGE)
        config.save()

    def on_capture_window_changed(self) -> None:
        """游戏窗口标题变更。"""
        config = Config().load()
        config.review_capture_window = self.capture_window_edit.text().strip()
        config.save()

    def on_select_window_clicked(self) -> None:
        """弹出对话框列出当前可见窗口，用户选择后填充窗口标题。"""
        from module.GameCapture.GameCapture import GameCapture

        windows = GameCapture.list_windows()
        if not windows:
            self.emit(
                Base.Event.TOAST,
                {
                    "type": Base.ToastType.WARNING,
                    "message": Localizer.get().review_page_capture_window_none,
                },
            )
            return

        dialog = MessageBox(
            Localizer.get().review_page_capture_window_select,
            "",
            self.window_ref,
        )
        dialog.yesButton.setText(Localizer.get().confirm)
        dialog.cancelButton.setText(Localizer.get().cancel)

        window_list = ListWidget(dialog)
        window_list.setMinimumHeight(300)
        for title, pid in windows:
            window_list.addItem(f"{title}  (PID: {pid})")
        window_list.setCurrentRow(0)

        dialog.textLayout.addWidget(window_list)

        if not dialog.exec():
            return

        row = window_list.currentRow()
        if row < 0 or row >= len(windows):
            return

        selected_title = windows[row][0]
        selected_pid = windows[row][1]
        self.capture_window_edit.setText(selected_title)
        # 同时保存 PID（热键发送优先使用 PID 定位窗口）
        config = Config().load()
        config.review_capture_window = selected_title
        config.review_capture_window_pid = selected_pid
        config.save()

    def on_capture_hotkey_changed(self) -> None:
        """热键变更（文本编辑完成或键盘捕获时统一触发）。"""
        config = Config().load()
        config.review_capture_hotkey = self.capture_hotkey_edit.text().strip()
        config.save()

    def on_capture_auto_advance_changed(self, checked: bool) -> None:
        """自动推进开关变更。"""
        config = Config().load()
        config.review_capture_auto_advance = checked
        config.save()
