from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import Action
from qfluentwidgets import ComboBox
from qfluentwidgets import FluentWindow
from qfluentwidgets import ListWidget
from qfluentwidgets import MessageBox
from qfluentwidgets import PlainTextEdit
from qfluentwidgets import ProgressRing
from qfluentwidgets import PushButton
from qfluentwidgets import SwitchButton

from base.Base import Base
from base.BaseIcon import BaseIcon
from frontend.Translation.DashboardCard import DashboardCard
from model.Item import Item
from module.Config import Config
from module.Data.DataManager import DataManager
from module.Engine.Engine import Engine
from module.Engine.TaskRunnerLifecycle import TaskRunnerLifecycle
from module.Localizer.Localizer import Localizer
from widget.CommandBarCard import CommandBarCard
from widget.CustomLineEdit import CustomLineEdit
from widget.HotkeyLineEdit import HotkeyLineEdit
from widget.SettingCard import SettingCard

# ==================== 图标常量 ====================
ICON_ACTION_START: BaseIcon = BaseIcon.PLAY
ICON_ACTION_STOP: BaseIcon = BaseIcon.CIRCLE_STOP
ICON_ACTION_APPROVE: BaseIcon = BaseIcon.CHECK
ICON_ACTION_DENY: BaseIcon = BaseIcon.CROSS
ICON_ACTION_RETRY: BaseIcon = BaseIcon.REFRESH_CW
ICON_ACTION_ASK: BaseIcon = BaseIcon.MESSAGE_CIRCLE_QUESTION
ICON_NAV_REVIEW: BaseIcon = BaseIcon.CLIPBOARD_CHECK

# 进度环最大值
RING_MAX_VALUE: int = 10000
SCOPE_ALL: int = 0
SCOPE_FILE: int = 1
SCOPE_FAILED: int = 2


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

        # 输出日志：累积每行审校结果的格式化文本
        self.output_lines: list[str] = []

        # 载入配置
        config = Config().load()

        # 主容器
        self.container = QVBoxLayout(self)
        self.container.setSpacing(8)
        self.container.setContentsMargins(24, 24, 24, 24)

        # 添加控件
        self.add_widget_head(self.container, config)
        self.add_widget_output(self.container)
        self.add_widget_scope(self.container)
        self.add_widget_capture(self.container, config)
        self.add_widget_foot(self.container, config)

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
            title=Localizer.get().review_page_error,
            value="0",
            unit="Line",
        )
        self.error_card.setFixedSize(140, 140)

        head_hbox.addWidget(self.pass_card)
        head_hbox.addWidget(self.fix_card)
        head_hbox.addWidget(self.fail_card)
        head_hbox.addWidget(self.error_card)
        head_hbox.addStretch(1)

        parent.addWidget(head_container)

    # ==================== 输出窗口 ====================

    def add_widget_output(self, parent: QVBoxLayout) -> None:
        """添加 AI 输出展示区域和询问输入框。"""
        # 输出文本区域（只读）
        self.output_text = PlainTextEdit(self)
        self.output_text.setReadOnly(True)
        self.output_text.setPlaceholderText(Localizer.get().review_page_desc)
        parent.addWidget(self.output_text, 1)

        # 询问输入框
        self.inquiry_input = CustomLineEdit(self)
        self.inquiry_input.setPlaceholderText(
            Localizer.get().review_page_inquiry_placeholder
        )
        self.inquiry_input.setVisible(False)
        parent.addWidget(self.inquiry_input)

    # ==================== 审校范围 ====================

    def add_widget_scope(self, parent: QVBoxLayout) -> None:
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
        """添加底部命令栏：开始/停止 + 审批操作按钮。"""
        self.command_bar = CommandBarCard()
        self.command_bar.set_minimum_width(640)

        # 开始审校按钮
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

        self.ask_action = self.command_bar.add_action(
            Action(
                ICON_ACTION_ASK,
                Localizer.get().review_page_ask_ai,
                self.command_bar,
                triggered=self.on_ask_ai,
            )
        )
        self.ask_action.setEnabled(False)

        self.command_bar.add_stretch(1)
        parent.addWidget(self.command_bar)

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

        # 清空输出并开始
        self.output_lines = []
        self.output_text.clear()

        self.emit(
            Base.Event.REVIEW_TASK,
            {
                "sub_event": Base.SubEvent.REQUEST,
                "items": review_items,
            },
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

    def on_stop_review(self) -> None:
        """停止审校。"""
        self.emit(
            Base.Event.REVIEW_REQUEST_STOP,
            {"sub_event": Base.SubEvent.REQUEST},
        )

    # ==================== 审批操作占位 ====================

    def on_approve(self) -> None:
        """通过当前审校建议，通知引擎继续。"""
        if not self.awaiting_approval:
            return
        self.awaiting_approval = False
        self.update_buttons()
        self.emit(
            Base.Event.REVIEW_USER_DECISION,
            {"decision": "approve"},
        )

    def on_deny(self) -> None:
        """拒绝当前审校建议，跳过本行。"""
        if not self.awaiting_approval:
            return
        self.awaiting_approval = False
        self.update_buttons()
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
        self.emit(
            Base.Event.REVIEW_USER_DECISION,
            {"decision": "retry"},
        )

    def on_ask_ai(self) -> None:
        """切换询问输入框的可见性，聚焦输入框。"""
        visible = not self.inquiry_input.isVisible()
        self.inquiry_input.setVisible(visible)
        if visible:
            self.inquiry_input.setFocus()

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
        total = data.get("total_line", 0)
        reviewed = data.get("reviewed_line", 0)
        pass_count = data.get("pass_line", 0)
        fix_count = data.get("fix_line", 0)
        fail_count = data.get("fail_line", 0)
        error_count = data.get("error_line", 0)
        awaiting = data.get("awaiting_approval", False)
        result_data = data.get("result")

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
                # 待批结果不追加到永久日志，仅在末尾展示
                self.awaiting_approval = True
                self.update_buttons()
                # 空行分隔已完成日志和待批项，使其视觉上更突出
                output = "\n".join(self.output_lines + ["", line])
            else:
                self.output_lines.append(line)
                output = "\n".join(self.output_lines)
            self.output_text.setPlainText(output)
            # 滚动到底部
            scrollbar = self.output_text.verticalScrollBar()
            if scrollbar:
                scrollbar.setValue(scrollbar.maximum())
        else:
            # 回退：仅显示进度行
            progress_text = (
                Localizer.get()
                .review_page_progress.replace("{CURRENT}", str(reviewed))
                .replace("{TOTAL}", str(total))
            )
            self.output_text.setPlainText(progress_text)

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
        """工程卸载后清空文件列表和输出。"""
        self.all_file_paths = []
        self.selected_files = []
        self.update_file_button_text()
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

        # 审批按钮仅在等待用户审批时启用
        can_approve = is_reviewing and not is_stopping and self.awaiting_approval
        self.approve_action.setEnabled(can_approve)
        self.deny_action.setEnabled(can_approve)
        self.retry_action.setEnabled(can_approve)
        self.ask_action.setEnabled(is_reviewing and not is_stopping)

        # 审校过程中禁用设置
        self.scope_combo.setEnabled(not is_busy)
        self.file_select_button.setEnabled(not is_busy)
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
        self.capture_window_edit.setText(selected_title)
        self.on_capture_window_changed()

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
