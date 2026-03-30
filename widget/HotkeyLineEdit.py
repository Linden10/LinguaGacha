from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtGui import QKeyEvent

from widget.CustomLineEdit import CustomLineEdit

# 特殊键名映射（Qt 键码 → 发送给 ffmpeg / 系统 API 的名称）
SPECIAL_KEY_NAMES: dict[int, str] = {
    Qt.Key.Key_Return: "Enter",
    Qt.Key.Key_Enter: "Enter",
    Qt.Key.Key_Space: "Space",
    Qt.Key.Key_Tab: "Tab",
    Qt.Key.Key_Escape: "Escape",
    Qt.Key.Key_Backspace: "Backspace",
    Qt.Key.Key_Delete: "Delete",
    Qt.Key.Key_Up: "Up",
    Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Left: "Left",
    Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_PageUp: "PageUp",
    Qt.Key.Key_PageDown: "PageDown",
    Qt.Key.Key_F1: "F1",
    Qt.Key.Key_F2: "F2",
    Qt.Key.Key_F3: "F3",
    Qt.Key.Key_F4: "F4",
    Qt.Key.Key_F5: "F5",
    Qt.Key.Key_F6: "F6",
    Qt.Key.Key_F7: "F7",
    Qt.Key.Key_F8: "F8",
    Qt.Key.Key_F9: "F9",
    Qt.Key.Key_F10: "F10",
    Qt.Key.Key_F11: "F11",
    Qt.Key.Key_F12: "F12",
    Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Pause: "Pause",
}


class HotkeyLineEdit(CustomLineEdit):
    """按键捕获输入框：聚焦后按下键盘即可录入热键组合。

    支持修饰键（Ctrl / Alt / Shift）与普通键的组合，
    结果以 "Ctrl+Shift+A" 格式写入文本框并发出 hotkey_changed 信号。
    """

    hotkey_changed: Signal = Signal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()

        # 忽略单独的修饰键按下
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            return

        modifiers = event.modifiers()
        parts: list[str] = []

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")

        # 解析主键名：优先匹配特殊键表，否则取事件文本
        key_name = SPECIAL_KEY_NAMES.get(key) or event.text().strip().upper()
        if not key_name:
            # 无法识别的键，忽略
            return

        parts.append(key_name)
        hotkey_text = "+".join(parts)

        self.setText(hotkey_text)
        self.hotkey_changed.emit(hotkey_text)
