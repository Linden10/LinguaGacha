from __future__ import annotations

import json
from pathlib import Path

import pytest

from model.Item import Item
from module.Config import Config
from module.File.ORIMESSAGEJSON import ORIMESSAGEJSON
from tests.module.file.conftest import DummyDataManager


def test_read_from_stream_detects_ori_message_schema_and_preserves_rows(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.ORIMESSAGEJSON.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    payload = [
        {"line": 1, "ori": "A", "message": "A", "meta": "x"},
        {"line": 2, "ori": "A", "message": ""},
        {"line": 3, "ori": "B", "message": "B translated"},
        {"line": 4, "ori": "C"},
    ]

    items = ORIMESSAGEJSON(config).read_from_stream(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "m.json",
    )

    assert len(items) == 4
    assert [item.get_src() for item in items] == ["A", "A", "B", "C"]
    assert [item.get_dst() for item in items] == ["", "", "B translated", ""]
    assert [item.get_row() for item in items] == [0, 1, 2, 3]
    assert items[0].get_file_type() == Item.FileType.ORIMESSAGEJSON
    assert items[0].get_status().value == "NONE"
    assert items[2].get_status().value == "PROCESSED_IN_PAST"


def test_read_from_stream_returns_empty_when_schema_not_matched(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.ORIMESSAGEJSON.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    payload = [{"message": "no ori"}]

    assert (
        ORIMESSAGEJSON(config).read_from_stream(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "m.json",
        )
        == []
    )


def test_write_to_path_updates_only_message_and_preserves_metadata(
    config: Config,
    dummy_data_manager: DummyDataManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.ORIMESSAGEJSON.DataManager.get", lambda: dummy_data_manager
    )
    monkeypatch.setattr(
        "module.File.ORIMESSAGEJSON.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    original_rows = [
        {"line": 1, "ori": "A", "message": "A", "meta": "x"},
        {"line": 2, "ori": "A", "message": "A"},
        {"line": 3, "ori": "B", "message": ""},
        {"line": 4, "ori": "C"},
    ]
    rel_path = "json/meta.json"
    dummy_data_manager.assets[rel_path] = json.dumps(
        original_rows, ensure_ascii=False
    ).encode("utf-8")

    items = [
        Item.from_dict(
            {
                "src": "A",
                "dst": "A-1",
                "row": 0,
                "file_type": Item.FileType.ORIMESSAGEJSON,
                "file_path": rel_path,
            }
        ),
        Item.from_dict(
            {
                "src": "A",
                "dst": "A-2",
                "row": 1,
                "file_type": Item.FileType.ORIMESSAGEJSON,
                "file_path": rel_path,
            }
        ),
        Item.from_dict(
            {
                "src": "B",
                "dst": "",
                "row": 2,
                "file_type": Item.FileType.ORIMESSAGEJSON,
                "file_path": rel_path,
            }
        ),
        Item.from_dict(
            {
                "src": "C",
                "dst": "",
                "row": 3,
                "file_type": Item.FileType.ORIMESSAGEJSON,
                "file_path": rel_path,
            }
        ),
    ]

    ORIMESSAGEJSON(config).write_to_path(items)

    output_file = Path(dummy_data_manager.get_translated_path()) / rel_path
    result = json.loads(output_file.read_text(encoding="utf-8"))
    assert result == [
        {"line": 1, "ori": "A", "message": "A-1", "meta": "x"},
        {"line": 2, "ori": "A", "message": "A-2"},
        {"line": 3, "ori": "B", "message": "B"},
        {"line": 4, "ori": "C", "message": "C"},
    ]


def test_read_from_path_reads_files(
    fs,
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.ORIMESSAGEJSON.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    fs.create_file(
        "/fake/input/m.json",
        contents=json.dumps([{"ori": "A", "message": "A"}], ensure_ascii=False),
        create_missing_dirs=True,
    )

    items = ORIMESSAGEJSON(config).read_from_path(["/fake/input/m.json"], "/fake/input")

    assert len(items) == 1
    assert items[0].get_file_path() == "m.json"
