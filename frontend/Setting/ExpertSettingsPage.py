from PySide6.QtCore import Qt
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLayout
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from typing import Callable
from qfluentwidgets import Action
from qfluentwidgets import FluentWindow
from qfluentwidgets import PushButton
from qfluentwidgets import RoundMenu
from qfluentwidgets import SingleDirectionScrollArea
from qfluentwidgets import SpinBox
from qfluentwidgets import SwitchButton

from base.Base import Base
from base.BaseIcon import BaseIcon
from module.Config import Config
from module.Localizer.Localizer import Localizer
from widget.CustomLineEdit import CustomLineEdit
from widget.SettingCard import SettingCard


class ExpertSettingsPage(Base, QWidget):
    def __init__(self, text: str, window: FluentWindow) -> None:
        super().__init__(window)
        self.setObjectName(text.replace(" ", "-"))

        # 载入并保存默认配置
        config = Config().load().save()

        # 设置容器
        self.root = QVBoxLayout(self)
        self.root.setSpacing(8)
        self.root.setContentsMargins(6, 24, 6, 24)  # 左、上、右、下

        # 创建滚动区域的内容容器
        scroll_area_vbox_widget = QWidget()
        scroll_area_vbox = QVBoxLayout(scroll_area_vbox_widget)
        scroll_area_vbox.setContentsMargins(18, 0, 18, 0)

        # 创建滚动区域
        scroll_area = SingleDirectionScrollArea(orient=Qt.Orientation.Vertical)
        scroll_area.setWidget(scroll_area_vbox_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.enableTransparentBackground()

        # 将滚动区域添加到父布局
        self.root.addWidget(scroll_area)

        # 添加控件
        self.add_widget_response_check_settings(scroll_area_vbox, config, window)
        self.add_widget_preceding_lines_threshold(scroll_area_vbox, config, window)
        self.add_widget_preceding_context_mode(scroll_area_vbox, config, window)
        self.add_widget_clean_ruby(scroll_area_vbox, config, window)
        self.add_widget_deduplication_in_trans(scroll_area_vbox, config, window)
        self.add_widget_deduplication_in_bilingual(scroll_area_vbox, config, window)
        self.add_widget_write_translated_name_fields_to_file(
            scroll_area_vbox, config, window
        )
        self.add_widget_auto_process_prefix_suffix_preserved_text(
            scroll_area_vbox, config, window
        )
        self.add_widget_ffmpeg_path(scroll_area_vbox, config, window)

        # 填充
        scroll_area_vbox.addStretch(1)

    # 结果检查规则设置
    def add_widget_response_check_settings(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        menu = RoundMenu(parent=window)

        action_check_similarity = Action(
            Localizer.get().expert_settings_page_response_check_similarity, self
        )
        action_check_similarity.setCheckable(True)
        menu.addAction(action_check_similarity)

        action_check_kana = Action(
            Localizer.get().expert_settings_page_response_check_kana_residue, self
        )
        action_check_kana.setCheckable(True)
        menu.addAction(action_check_kana)

        action_check_hangeul = Action(
            Localizer.get().expert_settings_page_response_check_hangeul_residue, self
        )
        action_check_hangeul.setCheckable(True)
        menu.addAction(action_check_hangeul)

        def sync_action_checked(config: Config) -> None:
            action_check_kana.setChecked(config.check_kana_residue)
            action_check_hangeul.setChecked(config.check_hangeul_residue)
            action_check_similarity.setChecked(config.check_similarity)

            action_check_kana.setIcon(
                BaseIcon.CIRCLE_CHECK if config.check_kana_residue else BaseIcon.CIRCLE
            )
            action_check_hangeul.setIcon(
                BaseIcon.CIRCLE_CHECK
                if config.check_hangeul_residue
                else BaseIcon.CIRCLE
            )
            action_check_similarity.setIcon(
                BaseIcon.CIRCLE_CHECK if config.check_similarity else BaseIcon.CIRCLE
            )

        def on_check_kana_triggered() -> None:
            config = Config().load()
            config.check_kana_residue = action_check_kana.isChecked()
            config.save()
            sync_action_checked(config)

        def on_check_hangeul_triggered() -> None:
            config = Config().load()
            config.check_hangeul_residue = action_check_hangeul.isChecked()
            config.save()
            sync_action_checked(config)

        def on_check_similarity_triggered() -> None:
            config = Config().load()
            config.check_similarity = action_check_similarity.isChecked()
            config.save()
            sync_action_checked(config)

        def before_show_menu() -> None:
            config = Config().load()
            sync_action_checked(config)

        action_check_kana.triggered.connect(lambda checked: on_check_kana_triggered())
        action_check_hangeul.triggered.connect(
            lambda checked: on_check_hangeul_triggered()
        )
        action_check_similarity.triggered.connect(
            lambda checked: on_check_similarity_triggered()
        )

        card = SettingCard(
            title=Localizer.get().expert_settings_page_response_check_settings,
            description=Localizer.get().expert_settings_page_response_check_settings_desc,
            parent=self,
        )
        menu_button = PushButton(
            Localizer.get().expert_settings_page_response_check_settings_button
        )
        menu_button.clicked.connect(
            lambda checked=False: self.show_menu_for_button(
                menu_button, menu, before_show_menu
            )
        )
        card.add_right_widget(menu_button)
        sync_action_checked(config)

        parent.addWidget(card)

    # 参考上文行数阈值
    def add_widget_preceding_lines_threshold(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        def value_changed(spin_box: SpinBox) -> None:
            config = Config().load()
            config.preceding_lines_threshold = spin_box.value()
            config.save()

        card = SettingCard(
            title=Localizer.get().expert_settings_page_preceding_lines_threshold,
            description=Localizer.get().expert_settings_page_preceding_lines_threshold_desc,
            parent=self,
        )
        spin_box = SpinBox(card)
        spin_box.setRange(0, 9999999)
        spin_box.setValue(config.preceding_lines_threshold)
        spin_box.valueChanged.connect(lambda value: value_changed(spin_box))
        card.add_right_widget(spin_box)
        parent.addWidget(card)

    # 参考上文内容模式
    def add_widget_preceding_context_mode(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        menu = RoundMenu(parent=window)

        action_original = Action(
            Localizer.get().expert_settings_page_preceding_context_mode_original, self
        )
        action_original.setCheckable(True)
        menu.addAction(action_original)

        action_translated = Action(
            Localizer.get().expert_settings_page_preceding_context_mode_translated, self
        )
        action_translated.setCheckable(True)
        menu.addAction(action_translated)

        action_both = Action(
            Localizer.get().expert_settings_page_preceding_context_mode_both, self
        )
        action_both.setCheckable(True)
        menu.addAction(action_both)

        def sync_action_checked(config: Config) -> None:
            mode = config.preceding_context_mode
            action_original.setChecked(mode == Config.PrecedingContextMode.ORIGINAL)
            action_translated.setChecked(mode == Config.PrecedingContextMode.TRANSLATED)
            action_both.setChecked(mode == Config.PrecedingContextMode.BOTH)
            action_original.setIcon(
                BaseIcon.CIRCLE_CHECK
                if mode == Config.PrecedingContextMode.ORIGINAL
                else BaseIcon.CIRCLE
            )
            action_translated.setIcon(
                BaseIcon.CIRCLE_CHECK
                if mode == Config.PrecedingContextMode.TRANSLATED
                else BaseIcon.CIRCLE
            )
            action_both.setIcon(
                BaseIcon.CIRCLE_CHECK
                if mode == Config.PrecedingContextMode.BOTH
                else BaseIcon.CIRCLE
            )

        def on_mode_selected(mode: str) -> None:
            config = Config().load()
            config.preceding_context_mode = mode
            config.save()
            sync_action_checked(config)

        def before_show_menu() -> None:
            config = Config().load()
            sync_action_checked(config)

        action_original.triggered.connect(
            lambda checked: on_mode_selected(Config.PrecedingContextMode.ORIGINAL)
        )
        action_translated.triggered.connect(
            lambda checked: on_mode_selected(Config.PrecedingContextMode.TRANSLATED)
        )
        action_both.triggered.connect(
            lambda checked: on_mode_selected(Config.PrecedingContextMode.BOTH)
        )

        card = SettingCard(
            title=Localizer.get().expert_settings_page_preceding_context_mode,
            description=Localizer.get().expert_settings_page_preceding_context_mode_desc,
            parent=self,
        )
        menu_button = PushButton(
            Localizer.get().expert_settings_page_preceding_context_mode_button
        )
        menu_button.clicked.connect(
            lambda checked=False: self.show_menu_for_button(
                menu_button, menu, before_show_menu
            )
        )
        card.add_right_widget(menu_button)
        sync_action_checked(config)

        parent.addWidget(card)

    # 清理原文中的注音文本
    def add_widget_clean_ruby(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        def checked_changed(button: SwitchButton) -> None:
            config = Config().load()
            config.clean_ruby = button.isChecked()
            config.save()

        card = SettingCard(
            title=Localizer.get().expert_settings_page_clean_ruby,
            description=Localizer.get().expert_settings_page_clean_ruby_desc,
            parent=self,
        )
        switch_button = SwitchButton(card)
        switch_button.setOnText("")
        switch_button.setOffText("")
        switch_button.setChecked(config.clean_ruby)
        switch_button.checkedChanged.connect(
            lambda checked: checked_changed(switch_button)
        )
        card.add_right_widget(switch_button)
        parent.addWidget(card)

    # T++ 项目文件中对重复文本去重
    def add_widget_deduplication_in_trans(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        def checked_changed(button: SwitchButton) -> None:
            config = Config().load()
            config.deduplication_in_trans = button.isChecked()
            config.save()

        card = SettingCard(
            title=Localizer.get().expert_settings_page_deduplication_in_trans,
            description=Localizer.get().expert_settings_page_deduplication_in_trans_desc,
            parent=self,
        )
        switch_button = SwitchButton(card)
        switch_button.setOnText("")
        switch_button.setOffText("")
        switch_button.setChecked(config.deduplication_in_trans)
        switch_button.checkedChanged.connect(
            lambda checked: checked_changed(switch_button)
        )
        card.add_right_widget(switch_button)
        parent.addWidget(card)

    # 双语输出文件中原文与译文一致的文本只输出一次
    def add_widget_deduplication_in_bilingual(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        def checked_changed(button: SwitchButton) -> None:
            config = Config().load()
            config.deduplication_in_bilingual = button.isChecked()
            config.save()

        card = SettingCard(
            title=Localizer.get().expert_settings_page_deduplication_in_bilingual,
            description=Localizer.get().expert_settings_page_deduplication_in_bilingual_desc,
            parent=self,
        )
        switch_button = SwitchButton(card)
        switch_button.setOnText("")
        switch_button.setOffText("")
        switch_button.setChecked(config.deduplication_in_bilingual)
        switch_button.checkedChanged.connect(
            lambda checked: checked_changed(switch_button)
        )
        card.add_right_widget(switch_button)
        parent.addWidget(card)

    # 将姓名字段译文写入译文文件
    def add_widget_write_translated_name_fields_to_file(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        def checked_changed(button: SwitchButton) -> None:
            config = Config().load()
            config.write_translated_name_fields_to_file = button.isChecked()
            config.save()

        card = SettingCard(
            title=Localizer.get().expert_settings_page_write_translated_name_fields_to_file,
            description=Localizer.get().expert_settings_page_write_translated_name_fields_to_file_desc,
            parent=self,
        )
        switch_button = SwitchButton(card)
        switch_button.setOnText("")
        switch_button.setOffText("")
        switch_button.setChecked(config.write_translated_name_fields_to_file)
        switch_button.checkedChanged.connect(
            lambda checked: checked_changed(switch_button)
        )
        card.add_right_widget(switch_button)
        parent.addWidget(card)

    # 自动移除前后缀代码段
    def add_widget_auto_process_prefix_suffix_preserved_text(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        def checked_changed(button: SwitchButton) -> None:
            config = Config().load()
            config.auto_process_prefix_suffix_preserved_text = button.isChecked()
            config.save()

        card = SettingCard(
            title=Localizer.get().expert_settings_page_auto_process_prefix_suffix_preserved_text,
            description=Localizer.get().expert_settings_page_auto_process_prefix_suffix_preserved_text_desc,
            parent=self,
        )
        switch_button = SwitchButton(card)
        switch_button.setOnText("")
        switch_button.setOffText("")
        switch_button.setChecked(config.auto_process_prefix_suffix_preserved_text)
        switch_button.checkedChanged.connect(
            lambda checked: checked_changed(switch_button)
        )
        card.add_right_widget(switch_button)
        parent.addWidget(card)

    def show_menu_for_button(
        self,
        button: PushButton,
        menu: RoundMenu,
        before_show: Callable[[], None],
    ) -> None:
        # 把菜单触发逻辑集中到一个入口，避免每处重复实现坐标计算。
        before_show()
        global_pos = button.mapToGlobal(QPoint(0, button.height()))
        menu.exec(global_pos)

    # FFmpeg 路径设置
    def add_widget_ffmpeg_path(
        self, parent: QLayout, config: Config, window: FluentWindow
    ) -> None:
        del window

        card = SettingCard(
            title=Localizer.get().expert_settings_page_ffmpeg_path,
            description=Localizer.get().expert_settings_page_ffmpeg_path_desc,
            parent=self,
        )

        # 输入框 + 浏览按钮组合
        container = QWidget(card)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        ffmpeg_edit = CustomLineEdit(container)
        ffmpeg_edit.setMinimumWidth(200)
        ffmpeg_edit.setText(config.ffmpeg_path)
        ffmpeg_edit.setPlaceholderText(
            Localizer.get().expert_settings_page_ffmpeg_path_placeholder
        )

        def on_editing_finished() -> None:
            current_config = Config().load()
            current_config.ffmpeg_path = ffmpeg_edit.text().strip()
            current_config.save()

        ffmpeg_edit.editingFinished.connect(on_editing_finished)

        browse_button = PushButton(Localizer.get().select_file, container)

        def on_browse() -> None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                Localizer.get().select_file,
                "",
                "ffmpeg (*)",
            )
            if isinstance(path, str) and path:
                # setText 不会触发 editingFinished 信号，需手动调用保存
                ffmpeg_edit.setText(path)
                on_editing_finished()

        browse_button.clicked.connect(on_browse)

        layout.addWidget(ffmpeg_edit)
        layout.addWidget(browse_button)
        card.add_right_widget(container)
        parent.addWidget(card)
