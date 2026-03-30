from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time

from base.LogManager import LogManager


class GameCapture:
    """游戏窗口捕获工具：通过 ffmpeg 捕获游戏画面（截图/录像/录音）。

    使用 ffmpeg 实现跨平台的低 CPU / 低内存捕获，支持三种模式：
    - IMAGE:  单帧截图，返回 PNG 的 base64 编码
    - VIDEO:  持续录像，保存为临时 mp4 文件
    - AUDIO:  持续录音，保存为临时 wav 文件
    """

    # ffmpeg 截图的默认超时（秒）
    SCREENSHOT_TIMEOUT: int = 10
    # 截图默认最大宽度（避免过大图片消耗过多 tokens）
    MAX_IMAGE_WIDTH: int = 1280

    def __init__(self) -> None:
        self.recording_process: subprocess.Popen[bytes] | None = None
        self.recording_path: str = ""

    # ==================== 公共接口 ====================

    @staticmethod
    def resolve_ffmpeg_path() -> str:
        """解析 ffmpeg 可执行文件路径：优先使用配置中的自定义路径，否则查找系统 PATH。"""
        from module.Config import Config

        config = Config().load()
        custom_path = config.ffmpeg_path.strip()
        if custom_path:
            return custom_path
        return shutil.which("ffmpeg") or "ffmpeg"

    @staticmethod
    def is_available() -> bool:
        """检查 ffmpeg 是否可用。"""
        from module.Config import Config

        config = Config().load()
        custom_path = config.ffmpeg_path.strip()
        if custom_path:
            return os.path.isfile(custom_path)
        return shutil.which("ffmpeg") is not None

    def capture_screenshot(self, window_title: str) -> str:
        """捕获指定窗口的截图，返回 PNG 的 base64 编码字符串。

        如果捕获失败则返回空字符串。
        """
        if not self.is_available():
            LogManager.get().warning("ffmpeg not found, screenshot capture disabled")
            return ""

        if not window_title:
            LogManager.get().warning("No window title specified for capture")
            return ""

        try:
            cmd = self.build_screenshot_cmd(window_title)
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self.SCREENSHOT_TIMEOUT,
            )

            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")
                LogManager.get().warning(f"ffmpeg screenshot failed: {stderr[:200]}")
                return ""

            png_data = result.stdout
            if not png_data:
                LogManager.get().warning("ffmpeg returned empty screenshot data")
                return ""

            return base64.b64encode(png_data).decode("ascii")

        except subprocess.TimeoutExpired:
            LogManager.get().warning("ffmpeg screenshot timed out")
            return ""
        except Exception as e:
            LogManager.get().warning("Screenshot capture failed", e)
            return ""

    def start_recording(self, window_title: str, mode: str) -> bool:
        """开始录制视频或音频。

        录制将在后台持续进行，调用 stop_recording() 结束并获取文件路径。
        IMAGE 模式不适用此方法，请使用 capture_screenshot()。
        """
        from module.Config import Config

        if mode == Config.CaptureMode.IMAGE:
            LogManager.get().warning("Use capture_screenshot() for IMAGE mode")
            return False

        if not self.is_available():
            LogManager.get().warning("ffmpeg not found, recording disabled")
            return False

        if self.recording_process is not None:
            LogManager.get().warning("Recording already in progress")
            return False

        if not window_title:
            LogManager.get().warning("No window title specified for recording")
            return False

        try:
            from module.Config import Config

            suffix = ".wav" if mode == Config.CaptureMode.AUDIO else ".mp4"
            tmp_file = tempfile.NamedTemporaryFile(
                suffix=suffix, prefix="lgc_capture_", delete=False
            )
            self.recording_path = tmp_file.name
            tmp_file.close()

            cmd = self.build_recording_cmd(window_title, mode, self.recording_path)

            self.recording_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            return True

        except Exception as e:
            LogManager.get().warning("Failed to start recording", e)
            self.recording_process = None
            self.recording_path = ""
            return False

    def stop_recording(self) -> str:
        """停止录制并返回文件路径。"""
        if self.recording_process is None:
            return ""

        try:
            # 向 ffmpeg 发送 'q' 来优雅退出
            if self.recording_process.stdin is not None:
                self.recording_process.stdin.write(b"q")
                self.recording_process.stdin.flush()

            self.recording_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.recording_process.kill()
            self.recording_process.wait()
        except Exception as e:
            LogManager.get().warning("Error stopping recording", e)
            self.recording_process.kill()

        path = self.recording_path
        self.recording_process = None
        self.recording_path = ""
        return path

    def send_hotkey(self, window_title: str, key: str) -> bool:
        """向指定窗口发送热键以推进游戏。

        Windows 通过 PowerShell，Linux 通过 xdotool，macOS 通过 osascript。
        """
        if not window_title or not key:
            return False

        try:
            if sys.platform == "win32":
                return self.send_hotkey_windows(window_title, key)
            elif sys.platform == "linux":
                return self.send_hotkey_linux(window_title, key)
            elif sys.platform == "darwin":
                return self.send_hotkey_macos(window_title, key)
            else:
                LogManager.get().warning(
                    f"Hotkey sending not supported on {sys.platform}"
                )
                return False
        except Exception as e:
            LogManager.get().warning("Failed to send hotkey", e)
            return False

    # ==================== 平台相关实现 ====================

    @classmethod
    def build_screenshot_cmd(cls, window_title: str) -> list[str]:
        """构建截图命令（跨平台）。

        Windows: 通过 gdigrab 按窗口标题捕获。
        macOS:   avfoundation 仅支持按屏幕设备索引捕获整个屏幕。
        Linux:   x11grab 捕获整个屏幕（:0.0），暂不支持按窗口标题裁剪。
        """
        ffmpeg = cls.resolve_ffmpeg_path()
        max_w = str(GameCapture.MAX_IMAGE_WIDTH)
        if sys.platform == "win32":
            return [
                ffmpeg,
                "-f",
                "gdigrab",
                "-framerate",
                "1",
                "-i",
                f"title={window_title}",
                "-frames:v",
                "1",
                "-vf",
                f"scale={max_w}:-1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ]
        elif sys.platform == "darwin":
            # macOS 使用 avfoundation；窗口捕获需要 screen device index
            return [
                ffmpeg,
                "-f",
                "avfoundation",
                "-framerate",
                "1",
                "-i",
                "1:none",
                "-frames:v",
                "1",
                "-vf",
                f"scale={max_w}:-1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ]
        else:
            # Linux 使用 x11grab
            return [
                ffmpeg,
                "-f",
                "x11grab",
                "-framerate",
                "1",
                "-i",
                ":0.0",
                "-frames:v",
                "1",
                "-vf",
                f"scale={max_w}:-1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ]

    @classmethod
    def build_recording_cmd(
        cls, window_title: str, mode: str, output_path: str
    ) -> list[str]:
        """构建录制命令（跨平台）。"""
        from module.Config import Config

        ffmpeg = cls.resolve_ffmpeg_path()

        if mode == Config.CaptureMode.AUDIO:
            # 仅录制音频
            if sys.platform == "win32":
                return [
                    ffmpeg,
                    "-f",
                    "dshow",
                    "-i",
                    "audio=virtual-audio-capturer",
                    "-y",
                    output_path,
                ]
            elif sys.platform == "darwin":
                return [
                    ffmpeg,
                    "-f",
                    "avfoundation",
                    "-i",
                    ":0",
                    "-y",
                    output_path,
                ]
            else:
                return [
                    ffmpeg,
                    "-f",
                    "pulse",
                    "-i",
                    "default",
                    "-y",
                    output_path,
                ]
        else:
            # 录制视频
            include_audio = mode == Config.CaptureMode.VIDEO_AUDIO
            if sys.platform == "win32":
                cmd = [
                    ffmpeg,
                    "-f",
                    "gdigrab",
                    "-framerate",
                    "15",
                    "-i",
                    f"title={window_title}",
                ]
                if include_audio:
                    cmd += ["-f", "dshow", "-i", "audio=virtual-audio-capturer"]
                cmd += [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-y",
                    output_path,
                ]
                return cmd
            elif sys.platform == "darwin":
                # avfoundation "1:0" = screen device 1 + audio device 0
                av_input = "1:0" if include_audio else "1:none"
                cmd = [
                    ffmpeg,
                    "-f",
                    "avfoundation",
                    "-framerate",
                    "15",
                    "-i",
                    av_input,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-y",
                    output_path,
                ]
                return cmd
            else:
                cmd = [
                    ffmpeg,
                    "-f",
                    "x11grab",
                    "-framerate",
                    "15",
                    "-i",
                    ":0.0",
                ]
                if include_audio:
                    cmd += ["-f", "pulse", "-i", "default"]
                cmd += [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-y",
                    output_path,
                ]
                return cmd

    @staticmethod
    def send_hotkey_windows(window_title: str, key: str) -> bool:
        """Windows: 通过 PowerShell 激活窗口并发送按键。"""
        # 将常见键名映射到 SendKeys 格式
        key_map: dict[str, str] = {
            "Enter": "{ENTER}",
            "Space": " ",
            "Return": "{ENTER}",
            "Tab": "{TAB}",
            "Escape": "{ESC}",
        }
        send_key = key_map.get(key, key)

        ps_script = (
            "$w = (Get-Process | Where-Object"
            " {$_.MainWindowTitle -like "
            f"'*{window_title}*'"
            "}).MainWindowHandle;"
            "Add-Type -AssemblyName Microsoft.VisualBasic;"
            "[Microsoft.VisualBasic.Interaction]::AppActivate($w);"
            "Start-Sleep -Milliseconds 100;"
            f'[System.Windows.Forms.SendKeys]::SendWait("{send_key}")'
        )

        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0

    @staticmethod
    def send_hotkey_linux(window_title: str, key: str) -> bool:
        """Linux: 通过 xdotool 发送按键。"""
        if shutil.which("xdotool") is None:
            LogManager.get().warning("xdotool not found, hotkey sending disabled")
            return False

        # 先搜索窗口
        search = subprocess.run(
            ["xdotool", "search", "--name", window_title],
            capture_output=True,
            timeout=5,
        )
        if search.returncode != 0:
            return False

        window_ids = search.stdout.decode().strip().split("\n")
        if not window_ids or not window_ids[0]:
            return False

        window_id = window_ids[0]

        # 激活并发送按键
        subprocess.run(
            ["xdotool", "windowactivate", window_id],
            timeout=5,
        )
        time.sleep(0.1)
        result = subprocess.run(
            ["xdotool", "key", "--window", window_id, key],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0

    @staticmethod
    def send_hotkey_macos(window_title: str, key: str) -> bool:
        """macOS: 通过 osascript 发送按键。

        注意：window_title 被用作 AppleScript 的应用名称，
        当窗口标题与应用名称不同时可能无法正常工作。
        """
        script = (
            f'tell application "{window_title}" to activate\n'
            f"delay 0.1\n"
            f'tell application "System Events" to keystroke "{key}"'
        )

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
