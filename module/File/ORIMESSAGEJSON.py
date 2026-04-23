import os
from typing import Any

from base.Base import Base
from model.Item import Item
from module.Config import Config
from module.Data.DataManager import DataManager
from module.Text.TextHelper import TextHelper
from module.Utils.JSONTool import JSONTool


class ORIMESSAGEJSON(Base):
    # [
    #     {
    #         "line": 1,
    #         "ori": "「わかり……ました」",
    #         "message": "「わかり……ました」"
    #     }
    # ]

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

        # 获取文件编码
        encoding = TextHelper.get_encoding(content=content, add_sig_to_utf8=True)

        # 数据处理
        if encoding.lower() in ("utf-8", "utf-8-sig"):
            json_data: Any = JSONTool.loads(content)
        else:
            json_data = JSONTool.loads(content.decode(encoding))

        # 仅识别指定结构，避免误判普通 JSON。
        if not self.is_ori_message_json(json_data):
            return items

        for row_index, entry_raw in enumerate(json_data):
            if not isinstance(entry_raw, dict):
                continue

            src_raw = entry_raw.get("ori")
            src = src_raw if isinstance(src_raw, str) else ""

            message_raw = entry_raw.get("message")
            message = message_raw if isinstance(message_raw, str) else ""
            dst = "" if message == "" or message == src else message

            status = Base.ProjectStatus.NONE
            if src == "":
                status = Base.ProjectStatus.EXCLUDED
            elif dst != "":
                status = Base.ProjectStatus.PROCESSED_IN_PAST

            # 记录原始行对象用于无资产回写兜底，避免丢失未知字段。
            items.append(
                Item.from_dict(
                    {
                        "src": src,
                        "dst": dst,
                        "row": row_index,
                        "file_type": Item.FileType.ORIMESSAGEJSON,
                        "file_path": rel_path,
                        "text_type": Item.TextType.KAG,
                        "status": status,
                        "extra_field": {
                            "row_index": row_index,
                            "original_entry": dict(entry_raw),
                        },
                    }
                )
            )

        return items

    # 写入
    def write_to_path(self, items: list[Item]) -> None:
        # 获取输出目录
        output_path = DataManager.get().get_translated_path()

        target = [
            item for item in items if item.get_file_type() == Item.FileType.ORIMESSAGEJSON
        ]

        group: dict[str, list[Item]] = {}
        for item in target:
            group.setdefault(item.get_file_path(), []).append(item)

        for rel_path, group_items in group.items():
            abs_path = os.path.join(output_path, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

            result_rows = self.load_original_rows(rel_path, group_items)
            if result_rows is None:
                continue

            # 仅覆盖 message 字段，其他字段保持原样。
            item_by_row = {item.get_row(): item for item in group_items}
            for row_index, entry_raw in enumerate(result_rows):
                entry = entry_raw if isinstance(entry_raw, dict) else {}
                item = item_by_row.get(row_index)
                if item is None:
                    continue

                dst = item.get_dst()
                if dst != "":
                    entry["message"] = dst
                    continue

                message_raw = entry.get("message")
                if isinstance(message_raw, str) and message_raw != "":
                    continue

                ori_raw = entry.get("ori")
                entry["message"] = ori_raw if isinstance(ori_raw, str) else ""

            JSONTool.save_file(abs_path, result_rows, indent=4)

    # 检查是否为 ori/message 列表结构
    def is_ori_message_json(self, json_data: Any) -> bool:
        if not isinstance(json_data, list) or len(json_data) == 0:
            return False

        for entry in json_data:
            if not isinstance(entry, dict):
                return False

            ori_raw = entry.get("ori")
            if not isinstance(ori_raw, str):
                return False

            if "message" in entry and not isinstance(entry.get("message"), str):
                return False

        return True

    # 尝试加载原始行表，优先使用工程资产保证完整回写
    def load_original_rows(self, rel_path: str, group_items: list[Item]) -> list[dict] | None:
        decompressed = DataManager.get().get_asset_decompressed(rel_path)
        if decompressed is not None:
            encoding = TextHelper.get_encoding(
                content=decompressed, add_sig_to_utf8=True
            )
            if encoding.lower() in ("utf-8", "utf-8-sig"):
                json_data: Any = JSONTool.loads(decompressed)
            else:
                json_data = JSONTool.loads(decompressed.decode(encoding))

            if self.is_ori_message_json(json_data):
                return [
                    dict(entry_raw) if isinstance(entry_raw, dict) else {}
                    for entry_raw in json_data
                ]

        # 资产不可用时回退到 Item 快照，确保不丢元数据。
        result_rows: dict[int, dict] = {}
        for item in group_items:
            extra_field_raw = item.get_extra_field()
            extra_field = extra_field_raw if isinstance(extra_field_raw, dict) else {}
            row_index_raw = extra_field.get("row_index")
            original_entry_raw = extra_field.get("original_entry")
            if (
                not isinstance(row_index_raw, int)
                or not isinstance(original_entry_raw, dict)
                or row_index_raw < 0
            ):
                continue
            result_rows[row_index_raw] = dict(original_entry_raw)

        if not result_rows:
            return None

        max_row = max(result_rows.keys())
        return [dict(result_rows.get(row, {})) for row in range(max_row + 1)]
