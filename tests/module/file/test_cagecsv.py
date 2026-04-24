from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from base.Base import Base
from model.Item import Item
from module.Config import Config
from module.File.CAGECSV import CAGECSV
from tests.module.file.conftest import DummyDataManager


def build_cage_csv_text() -> str:
    fieldnames = [
        "%line",
        "%seq",
        "%cheffect",
        "%effect1",
        "%music",
        "%bg",
        "%cg",
        "%ov",
        "%truename",
        "%name",
        "%voice",
        "%st_center_name",
        "%st_center",
        "%text",
    ]
    rows = [
        {
            "%line": "10000 ",
            "%seq": "&01ctrl",
            "%cheffect": "",
            "%effect1": "",
            "%music": "",
            "%bg": "",
            "%cg": "",
            "%ov": "",
            "%truename": "",
            "%name": "",
            "%voice": "",
            "%st_center_name": "",
            "%st_center": "",
            "%text": "",
        },
        {
            "%line": "11100 ",
            "%seq": "",
            "%cheffect": "",
            "%effect1": "",
            "%music": "",
            "%bg": "",
            "%cg": "",
            "%ov": "",
            "%truename": "かなみ",
            "%name": "かなみ",
            "%voice": "01kan001_001.wav",
            "%st_center_name": "かなみ",
            "%st_center": "10114",
            "%text": "「あれ、お＜兄＝義兄＞ちゃん……？」",
        },
        {
            "%line": "11200 ",
            "%seq": "",
            "%cheffect": "",
            "%effect1": "",
            "%music": "",
            "%bg": "",
            "%cg": "",
            "%ov": "",
            "%truename": "",
            "%name": "",
            "%voice": "",
            "%st_center_name": "",
            "%st_center": "",
            "%text": "line,with,comma\nand newline",
        },
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def test_read_from_stream_parses_cage_csv_and_marks_control_rows_excluded(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "cp932",
    )
    payload = build_cage_csv_text().encode("cp932")

    items = CAGECSV(config).read_from_stream(payload, "scr_csv/a.csv")

    assert len(items) == 3
    assert items[0].get_status() == Base.ProjectStatus.EXCLUDED
    assert items[1].get_status() == Base.ProjectStatus.NONE
    assert items[1].get_name_src() == "かなみ"
    assert items[1].get_name_dst() == "かなみ"
    assert items[2].get_src() == "line,with,comma\nand newline"


def test_read_from_stream_includes_name_for_every_actor_row(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每行台词都携带当前角色的姓名，连续同角色台词也不例外。"""
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    # 行顺序：かなみ台词1 → かなみ台词2 → 旁白 → 主人公台词 → かなみ台词3
    rows_text = (
        "%line,%seq,%name,%text\r\n"
        "1,,かなみ,かなみ台词1\r\n"
        "2,,かなみ,かなみ台词2\r\n"
        "3,,,旁白\r\n"
        "4,,主人公,主人公台词\r\n"
        "5,,かなみ,かなみ台词3\r\n"
    )
    payload = rows_text.encode("utf-8")

    items = CAGECSV(config).read_from_stream(payload, "a.csv")

    assert len(items) == 5
    # かなみ 初登场 → 姓名あり
    assert items[0].get_name_src() == "かなみ"
    # 連続同キャラでも姓名あり
    assert items[1].get_name_src() == "かなみ"
    # 旁白 → 姓名なし
    assert items[2].get_name_src() is None
    # 主人公 → 姓名あり
    assert items[3].get_name_src() == "主人公"
    # かなみ 再登场 → 姓名あり
    assert items[4].get_name_src() == "かなみ"


def test_read_from_stream_returns_empty_when_header_not_matched(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    payload = "a,b,c\n1,2,3\n".encode("utf-8")

    assert CAGECSV(config).read_from_stream(payload, "a.csv") == []


def test_read_from_stream_falls_back_to_cp932_when_chinese_encoding_detected(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """charset_normalizer が Shift-JIS を中文エンコードと誤検知した場合、cp932 にフォールバックする。"""
    # simulate the misdetection: charset_normalizer returns "gb18030"
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "gb18030",
    )
    payload = build_cage_csv_text().encode("cp932")

    items = CAGECSV(config).read_from_stream(payload, "scr_csv/a.csv")

    # correct decoding via cp932 fallback → Japanese text must be intact
    assert len(items) == 3
    assert items[1].get_src() == "「あれ、お＜兄＝義兄＞ちゃん……？」"
    assert items[1].get_name_src() == "かなみ"


def test_write_to_path_updates_only_name_and_text_and_preserves_metadata_columns(
    config: Config,
    dummy_data_manager: DummyDataManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.CAGECSV.DataManager.get", lambda: dummy_data_manager
    )
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "cp932",
    )
    rel_path = "scr_csv/a.csv"
    source_text = build_cage_csv_text()
    dummy_data_manager.assets[rel_path] = source_text.encode("cp932")

    items = [
        Item.from_dict(
            {
                "src": "",
                "dst": "",
                "row": 0,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
                "status": Base.ProjectStatus.EXCLUDED,
            }
        ),
        Item.from_dict(
            {
                "src": "「あれ、お＜兄＝義兄＞ちゃん……？」",
                "dst": '"Huh, big bro...?"',
                "name_src": "かなみ",
                "name_dst": "Kanami",
                "row": 1,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
        Item.from_dict(
            {
                "src": "line,with,comma\nand newline",
                "dst": "translated,with,comma\nand newline",
                "row": 2,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
    ]

    CAGECSV(config).write_to_path(items)

    output_file = Path(dummy_data_manager.get_translated_path()) / rel_path
    output_raw = output_file.read_bytes()
    assert output_raw.decode("cp932").count("\r\n") >= 4

    reader = csv.DictReader(io.StringIO(output_raw.decode("cp932"), newline=""))
    rows = list(reader)
    assert rows[0]["%line"] == "10000 "
    assert rows[0]["%seq"] == "&01ctrl"
    assert rows[0]["%text"] == ""
    assert rows[1]["%name"] == "Kanami"
    assert rows[1]["%text"] == '"Huh, big bro...?"'
    assert rows[1]["%voice"] == "01kan001_001.wav"
    assert rows[1]["%st_center_name"] == "かなみ"
    assert rows[2]["%name"] == ""
    assert rows[2]["%text"] == "translated,with,comma\nand newline"


def test_write_to_path_preserves_duplicate_text_rows_by_row_order(
    config: Config,
    dummy_data_manager: DummyDataManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.CAGECSV.DataManager.get", lambda: dummy_data_manager
    )
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    rel_path = "scr_csv/dup.csv"
    source_text = "%line,%seq,%name,%text\r\n1,,,same\r\n2,,,same\r\n"
    dummy_data_manager.assets[rel_path] = source_text.encode("utf-8")

    items = [
        Item.from_dict(
            {
                "src": "same",
                "dst": "first",
                "row": 0,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
        Item.from_dict(
            {
                "src": "same",
                "dst": "second",
                "row": 1,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
    ]

    CAGECSV(config).write_to_path(items)

    output_file = Path(dummy_data_manager.get_translated_path()) / rel_path
    reader = csv.DictReader(
        io.StringIO(output_file.read_text(encoding="utf-8"), newline="")
    )
    rows = list(reader)
    assert [row["%text"] for row in rows] == ["first", "second"]


def test_read_from_path_reads_files(
    fs,
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    fs.create_file(
        "/fake/input/a.csv",
        contents="%line,%seq,%name,%text\n1,,,hello\n",
        create_missing_dirs=True,
    )

    items = CAGECSV(config).read_from_path(["/fake/input/a.csv"], "/fake/input")

    assert len(items) == 1
    assert items[0].get_file_path() == "a.csv"
