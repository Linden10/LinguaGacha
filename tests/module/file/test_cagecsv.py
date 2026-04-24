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

    # Row 0: control row (EXCLUDED text item)
    # Row 1: actor-change dialogue → name item + text item
    # Row 2: narrator line → text item only
    assert len(items) == 4
    assert items[0].get_status() == Base.ProjectStatus.EXCLUDED
    assert items[0].get_src() == ""
    # items[1] is the name item inserted for the actor change at row 1
    assert items[1].get_extra_field() == CAGECSV.NAME_ROW_EXTRA_FIELD
    assert items[1].get_src() == "かなみ"
    assert items[1].get_status() == Base.ProjectStatus.NONE
    # items[2] is the dialogue text item at row 1
    assert items[2].get_status() == Base.ProjectStatus.NONE
    assert items[2].get_name_src() == "かなみ"
    assert items[2].get_name_dst() == "かなみ"
    assert items[3].get_src() == "line,with,comma\nand newline"


def test_read_from_stream_injects_name_only_on_actor_change(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """角色相邻台词中只有第一行携带姓名，角色切換時才再次注入。
    每次角色切換時都額外插入一條姓名行（extra_field="name_row"）。"""
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    # 行顺序：かなみ台词1 → かなみ台词2 → 旁白 → 主人公台词 → かなみ台词3（旁白でリセット済み）
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

    # 3 actor-changes (かなみ row0, 主人公 row3, かなみ row4) → 3 name items + 5 text items = 8
    assert len(items) == 8

    # Extract text items (not name rows) in order to verify name_src injection
    text_items = [
        item for item in items if item.get_extra_field() != CAGECSV.NAME_ROW_EXTRA_FIELD
    ]
    assert len(text_items) == 5
    # かなみ が初登場 → 姓名あり
    assert text_items[0].get_name_src() == "かなみ"
    # 連続同キャラ → 姓名なし
    assert text_items[1].get_name_src() is None
    # 旁白 → 姓名なし
    assert text_items[2].get_name_src() is None
    # 主人公 → 姓名あり（actor切换）
    assert text_items[3].get_name_src() == "主人公"
    # かなみ 再登場（旁白でリセット済み） → 姓名あり
    assert text_items[4].get_name_src() == "かなみ"

    # Verify name items carry the right src and status
    name_items = [
        item for item in items if item.get_extra_field() == CAGECSV.NAME_ROW_EXTRA_FIELD
    ]
    assert len(name_items) == 3
    assert name_items[0].get_src() == "かなみ"
    assert name_items[1].get_src() == "主人公"
    assert name_items[2].get_src() == "かなみ"
    assert all(n.get_status() == Base.ProjectStatus.NONE for n in name_items)


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
    """CAGE CSV の表头は ASCII のみで構成されるため、中文エンコードとして誤検知された場合は
    cp932 にフォールバックする。これにより Shift-JIS ファイルがゴミ文字化するのを防ぐ。"""
    # simulate the misdetection: charset_normalizer returns "gb18030"
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "gb18030",
    )
    payload = build_cage_csv_text().encode("cp932")

    items = CAGECSV(config).read_from_stream(payload, "scr_csv/a.csv")

    # correct decoding via cp932 fallback → Japanese text must be intact
    # Row 0: control row; Row 1: name item + text item; Row 2: narrator text item
    assert len(items) == 4
    # items[1] is the name item for the actor change at row 1
    assert items[1].get_extra_field() == CAGECSV.NAME_ROW_EXTRA_FIELD
    assert items[1].get_src() == "かなみ"
    # items[2] is the dialogue text item at row 1
    assert items[2].get_src() == "「あれ、お＜兄＝義兄＞ちゃん……？」"
    assert items[2].get_name_src() == "かなみ"


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


def test_write_to_path_propagates_translated_name_to_consecutive_actor_rows(
    config: Config,
    dummy_data_manager: DummyDataManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prev_name 最適化で name_dst=None の連続同角色行にも訳名が適用される。"""
    monkeypatch.setattr(
        "module.File.CAGECSV.DataManager.get", lambda: dummy_data_manager
    )
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    rel_path = "scr_csv/consecutive.csv"
    source_text = (
        "%line,%seq,%name,%text\r\n"
        "1,,かなみ,台词1\r\n"
        "2,,かなみ,台词2\r\n"
        "3,,かなみ,台词3\r\n"
    )
    dummy_data_manager.assets[rel_path] = source_text.encode("utf-8")

    items = [
        # 首行携带姓名（actor 切换）
        Item.from_dict(
            {
                "src": "台词1",
                "dst": "line1",
                "name_src": "かなみ",
                "name_dst": "Kanami",
                "row": 0,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
        # 连续同角色行：prev_name 优化跳过，name_dst=None
        Item.from_dict(
            {
                "src": "台词2",
                "dst": "line2",
                "row": 1,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
        Item.from_dict(
            {
                "src": "台词3",
                "dst": "line3",
                "row": 2,
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
    # 所有行的姓名均应为译名，而非原始日文
    assert [row["%name"] for row in rows] == ["Kanami", "Kanami", "Kanami"]
    assert [row["%text"] for row in rows] == ["line1", "line2", "line3"]


def test_write_to_path_uses_name_item_dst_for_name_translation(
    config: Config,
    dummy_data_manager: DummyDataManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """姓名行（extra_field="name_row"）的译文优先于文本行的 name_dst 用于写回 %name 列。"""
    monkeypatch.setattr(
        "module.File.CAGECSV.DataManager.get", lambda: dummy_data_manager
    )
    monkeypatch.setattr(
        "module.File.CAGECSV.TextHelper.get_encoding",
        lambda **_: "utf-8",
    )
    rel_path = "scr_csv/name_row.csv"
    source_text = "%line,%seq,%name,%text\r\n1,,かなみ,台词1\r\n2,,かなみ,台词2\r\n"
    dummy_data_manager.assets[rel_path] = source_text.encode("utf-8")

    items = [
        # 姓名行：AI 已翻译，dst="Kanami"
        Item.from_dict(
            {
                "src": "かなみ",
                "dst": "Kanami",
                "extra_field": CAGECSV.NAME_ROW_EXTRA_FIELD,
                "row": 0,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
        # 首行文本（actor 切换），name_dst 同样为 "Kanami"
        Item.from_dict(
            {
                "src": "台词1",
                "dst": "line1",
                "name_src": "かなみ",
                "name_dst": "Kanami",
                "row": 0,
                "file_type": Item.FileType.CAGECSV,
                "file_path": rel_path,
            }
        ),
        # 連続同キャラ行：name_dst=None，依赖 name_translation 补全
        Item.from_dict(
            {
                "src": "台词2",
                "dst": "line2",
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
    assert [row["%name"] for row in rows] == ["Kanami", "Kanami"]
    assert [row["%text"] for row in rows] == ["line1", "line2"]


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
