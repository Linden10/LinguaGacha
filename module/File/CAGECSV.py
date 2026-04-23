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

    def __init__(self, config: Config) -> None:
        super().__init__()

        # 初始化
        self.config = config

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
        encoding = TextHelper.get_encoding(content=content, add_sig_to_utf8=True)
        text = content.decode(encoding)
        reader = csv.DictReader(io.StringIO(text, newline=""))

        fieldnames = reader.fieldnames
        if not self.is_cage_header(fieldnames):
            return items

        for row_index, row in enumerate(reader):
            text_raw = row.get("%text", "")
            text_value = text_raw if isinstance(text_raw, str) else ""
            name_raw = row.get("%name", "")
            name_value = name_raw if isinstance(name_raw, str) else ""

            status = Base.ProjectStatus.NONE
            if text_value == "":
                status = Base.ProjectStatus.EXCLUDED

            dst = ""

            # 仅 %text 非空行作为可翻译文本，控制行仍保留为 EXCLUDED 以便可视追踪。
            items.append(
                Item.from_dict(
                    {
                        "src": text_value,
                        "dst": dst,
                        "name_src": name_value if name_value != "" else None,
                        "name_dst": name_value if name_value != "" else None,
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
        target = [item for item in items if item.get_file_type() == Item.FileType.CAGECSV]

        group: dict[str, list[Item]] = {}
        for item in target:
            group.setdefault(item.get_file_path(), []).append(item)

        for rel_path, group_items in group.items():
            original_data = DataManager.get().get_asset_decompressed(rel_path)
            if original_data is None:
                continue

            encoding = TextHelper.get_encoding(content=original_data, add_sig_to_utf8=True)
            source_text = original_data.decode(encoding)

            reader = csv.DictReader(io.StringIO(source_text, newline=""))
            fieldnames = reader.fieldnames
            if not self.is_cage_header(fieldnames):
                continue

            snapshots: dict[int, dict[str, str | None]] = {}
            for item in group_items:
                name_dst_raw = item.get_name_dst()
                name_dst = name_dst_raw if isinstance(name_dst_raw, str) else None
                snapshots[item.get_row()] = {
                    "dst": item.get_dst(),
                    "effective_dst": item.get_effective_dst(),
                    "name_dst": name_dst,
                }

            output_rows: list[dict[str, str]] = []
            for row_index, row in enumerate(reader):
                entry = dict(row)
                snapshot = snapshots.get(row_index)
                if snapshot is not None:
                    # 仅更新可翻译字段，其他元数据列保持原值。
                    dst = snapshot.get("dst")
                    effective_dst = snapshot.get("effective_dst")
                    if isinstance(dst, str) and dst != "" and isinstance(effective_dst, str):
                        entry["%text"] = effective_dst

                    name_dst = snapshot.get("name_dst")
                    if isinstance(name_dst, str) and name_dst != "":
                        entry["%name"] = name_dst

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
