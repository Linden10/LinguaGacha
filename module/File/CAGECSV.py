import csv
import io
import os

from base.Base import Base
from model.Item import Item
from module.Config import Config
from module.Data.DataManager import DataManager
from module.Text.TextHelper import TextHelper


class CAGECSV(Base):
    REQUIRED_HEADERS: tuple[str, ...] = ("%line", "%seq", "%name", "%text")

    # charset_normalizer 有时会把 Shift-JIS 字节序列误判为中文编码，尤其是头部含尾随逗号的情况。
    # CAGE 引擎是日文游戏引擎，输出文件只会是 cp932 或 UTF-8，不会是中文编码。
    CHINESE_ENCODINGS: frozenset[str] = frozenset({"gb18030", "gbk", "gb2312", "big5"})

    def __init__(self, config: Config) -> None:
        super().__init__()

        # 初始化
        self.config = config

    # 编码探测：当 charset_normalizer 返回中文编码时，检查首行是否为纯 ASCII；
    # CAGE CSV 的表头列名均为 ASCII，若首行可作 ASCII 解码则判定为 cp932。
    def detect_encoding(self, content: bytes) -> str:
        encoding = TextHelper.get_encoding(content=content, add_sig_to_utf8=True)
        if encoding in self.CHINESE_ENCODINGS:
            try:
                content.split(b"\n")[0].rstrip(b"\r").decode("ascii")
                encoding = "cp932"
            except UnicodeDecodeError:
                # 首行含非 ASCII 字节，说明文件确实是中文编码，保留原始探测结果。
                pass
        return encoding

    # 读取
    def read_from_path(self, abs_paths: list[str], input_path: str) -> list[Item]:
        items: list[Item] = []
        for abs_path in abs_paths:
            # 获取相对路径
            rel_path = os.path.relpath(abs_path, input_path)

            # 数据处理
            with open(abs_path, "rb") as reader:
                items.extend(self.read_from_stream(reader.read(), rel_path))

        return items

    # 从流读取
    def read_from_stream(self, content: bytes, rel_path: str) -> list[Item]:
        items: list[Item] = []

        # CSV 需要先保留编码，再做结构化解析，避免 Shift-JIS 场景误写回。
        encoding = self.detect_encoding(content)
        text = content.decode(encoding)
        reader = csv.DictReader(io.StringIO(text, newline=""))

        fieldnames = reader.fieldnames
        if not self.is_cage_header(fieldnames):
            return items

        # 追踪上一个出场角色，仅在角色切换时注入姓名，避免相邻台词重复标注。
        prev_name: str = ""

        for row_index, row in enumerate(reader):
            text_raw = row.get("%text", "")
            text_value = text_raw if isinstance(text_raw, str) else ""
            name_raw = row.get("%name", "")
            name_value = name_raw if isinstance(name_raw, str) else ""

            status = Base.ProjectStatus.NONE
            if text_value == "":
                status = Base.ProjectStatus.EXCLUDED

            # name_changed は prev_name 更新前に計算する必要がある（順序依存）。
            # 連続同キャラ台词では姓名を注入せず、actor切換時のみ注入する。
            name_changed = name_value != "" and name_value != prev_name
            if name_value != "":
                prev_name = name_value
            elif text_value != "":
                # 旁白等有文本但无姓名的行重置追踪，使下次出场无论是否同一角色都重新注入姓名。
                # 文本为空的控制行（text_value == ""）不重置，避免打断连续对话序列。
                prev_name = ""

            # 仅 %text 非空行作为可翻译文本，控制行仍保留为 EXCLUDED 以便可视追踪。
            items.append(
                Item.from_dict(
                    {
                        "src": text_value,
                        "dst": "",
                        "name_src": name_value if name_changed else None,
                        "name_dst": name_value if name_changed else None,
                        "row": row_index,
                        "file_type": Item.FileType.CAGECSV,
                        "file_path": rel_path,
                        "text_type": Item.TextType.KAG,
                        "status": status,
                    }
                )
            )

        return items

    # 写入
    def write_to_path(self, items: list[Item]) -> None:
        output_path = DataManager.get().get_translated_path()
        target = [
            item for item in items if item.get_file_type() == Item.FileType.CAGECSV
        ]

        group: dict[str, list[Item]] = {}
        for item in target:
            group.setdefault(item.get_file_path(), []).append(item)

        for rel_path, group_items in group.items():
            original_data = DataManager.get().get_asset_decompressed(rel_path)
            if original_data is None:
                continue

            encoding = self.detect_encoding(original_data)
            source_text = original_data.decode(encoding)

            reader = csv.DictReader(io.StringIO(source_text, newline=""))
            fieldnames = reader.fieldnames
            if not self.is_cage_header(fieldnames):
                continue

            item_translations: dict[int, dict[str, str | None]] = {}
            # 构建姓名译名表：从携带 name_src/name_dst 的首次出场行中提取，
            # 用于写回时将译名应用到 prev_name 优化跳过的连续同角色行。
            name_translation: dict[str, str] = {}
            for item in group_items:
                name_src = item.get_name_src()
                name_dst_raw = item.get_name_dst()
                name_dst = name_dst_raw if isinstance(name_dst_raw, str) else None
                if (
                    isinstance(name_src, str)
                    and name_src
                    and isinstance(name_dst, str)
                    and name_dst
                ):
                    name_translation[name_src] = name_dst
                item_translations[item.get_row()] = {
                    "dst": item.get_dst(),
                    "effective_dst": item.get_effective_dst(),
                    "name_dst": name_dst,
                }

            output_rows: list[dict[str, str]] = []
            for row_index, row in enumerate(reader):
                entry = dict(row)
                snapshot = item_translations.get(row_index)
                if snapshot is not None:
                    # 仅更新可翻译字段，其他元数据列保持原值。
                    dst = snapshot.get("dst")
                    effective_dst = snapshot.get("effective_dst")
                    if (
                        isinstance(dst, str)
                        and dst != ""
                        and isinstance(effective_dst, str)
                    ):
                        entry["%text"] = effective_dst

                    name_dst = snapshot.get("name_dst")
                    if isinstance(name_dst, str) and name_dst != "":
                        entry["%name"] = name_dst
                    elif name_translation:
                        # prev_name 优化跳过的连续同角色行，通过译名表补全译名。
                        orig_name = entry.get("%name", "")
                        if orig_name and orig_name in name_translation:
                            entry["%name"] = name_translation[orig_name]

                output_rows.append(entry)

            line_ending = "\r\n" if "\r\n" in source_text else "\n"
            io_buffer = io.StringIO(newline="")
            writer = csv.DictWriter(
                io_buffer,
                fieldnames=list(fieldnames),
                lineterminator=line_ending,
            )
            writer.writeheader()
            writer.writerows(output_rows)

            abs_path = os.path.join(output_path, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as writer_file:
                writer_file.write(io_buffer.getvalue().encode(encoding))

    # 校验 CAGE 头结构，避免误识别普通 CSV
    def is_cage_header(self, fieldnames: list[str] | None) -> bool:
        if not isinstance(fieldnames, list):
            return False
        return all(header in fieldnames for header in self.REQUIRED_HEADERS)
