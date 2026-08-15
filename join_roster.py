from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def normalize_class_label(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    aliases = {
        "计算机科学与技术": "计科",
        "信息安全": "信安",
        "人工智能": "智能",
        "网络空间安全": "网安",
    }
    for source, target in aliases.items():
        text = text.replace(source, target)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).lower()
    text = re.sub(r"^2026(?:年)?(?:级)?", "26", text)
    text = re.sub(r"^26级", "26", text)
    text = re.sub(r"(?:班级群|班群|班)$", "", text)
    return text


def normalize_person_text(value: str) -> str:
    return re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff·]",
        "",
        unicodedata.normalize("NFKC", value or ""),
    ).lower()


def load_roster(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"students": [], "classes": {}, "source": "", "imported_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"students": [], "classes": {}, "source": "", "imported_at": ""}
    return data if isinstance(data, dict) else {"students": [], "classes": {}}


def save_roster(path: Path, students: list[dict[str, str]], source: str) -> dict[str, Any]:
    counts = Counter(student["class_name"] for student in students)
    payload = {
        "source": Path(source).name,
        "imported_at": datetime.now().astimezone().isoformat(),
        "classes": dict(sorted(counts.items())),
        "students": students,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def match_roster_class(roster: dict[str, Any], group_name: str) -> str | None:
    normalized_group = normalize_class_label(group_name)
    matches = [
        class_name
        for class_name in roster.get("classes", {})
        if normalize_class_label(class_name) == normalized_group
    ]
    return matches[0] if len(matches) == 1 else None


def evaluate_roster_application(
    roster: dict[str, Any], class_name: str, comment: str
) -> tuple[bool, str, dict[str, str] | None]:
    ids = set(re.findall(r"(?<!\d)\d{8,20}(?!\d)", comment or ""))
    if not ids:
        return False, "申请信息中没有识别到学号", None
    candidates = [
        student
        for student in roster.get("students", [])
        if student.get("class_name") == class_name and student.get("student_id") in ids
    ]
    if len(candidates) != 1:
        return False, "学号不在该班名单中", None
    student = candidates[0]
    normalized_comment = normalize_person_text(comment)
    normalized_name = normalize_person_text(student.get("name", ""))
    if not normalized_name or normalized_name not in normalized_comment:
        return False, "姓名与该学号不一致", student
    return True, "姓名和学号均与该班名单一致", student


def parse_roster_xlsx(content: bytes) -> list[dict[str, str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("文件不是有效的 XLSX 工作簿") from exc
    with archive:
        shared_strings = _read_shared_strings(archive)
        worksheet_path = _first_worksheet_path(archive)
        root = ElementTree.fromstring(archive.read(worksheet_path))
        rows = [_read_row(row, shared_strings) for row in root.iter(f"{XML_NS}row")]
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if {"班级", "学号", "姓名"}.issubset({str(value).strip() for value in row})
        ),
        None,
    )
    if header_index is None:
        raise ValueError("未找到“班级、学号、姓名”表头")
    header = [str(value).strip() for value in rows[header_index]]
    positions = {name: header.index(name) for name in ("班级", "学号", "姓名")}
    students: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for row in rows[header_index + 1 :]:
        values = {
            name: str(row[position]).strip() if position < len(row) else ""
            for name, position in positions.items()
        }
        student_id = re.sub(r"\.0$", "", values["学号"])
        if not values["班级"] and not student_id and not values["姓名"]:
            continue
        if not values["班级"] or not values["姓名"] or not re.fullmatch(r"\d{8,20}", student_id):
            raise ValueError("名单中存在班级、姓名或学号不完整的行")
        if student_id in seen_ids:
            raise ValueError(f"名单中存在重复学号：{student_id[-4:]}")
        seen_ids.add(student_id)
        students.append(
            {"class_name": values["班级"], "student_id": student_id, "name": values["姓名"]}
        )
    if not students:
        raise ValueError("工作簿中没有读取到学生记录")
    return students


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{XML_NS}t")) for item in root]


def _first_worksheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    first_sheet = workbook.find(f".//{XML_NS}sheet")
    if first_sheet is None:
        raise ValueError("工作簿中没有工作表")
    relation_id = first_sheet.attrib[f"{REL_NS}id"]
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(
        (
            item.attrib["Target"]
            for item in relationships.findall(f"{PACKAGE_REL_NS}Relationship")
            if item.attrib.get("Id") == relation_id
        ),
        None,
    )
    if not target:
        raise ValueError("无法定位工作表数据")
    return "xl/" + target.lstrip("/") if not target.startswith("xl/") else target


def _read_row(row: ElementTree.Element, shared_strings: list[str]) -> list[str]:
    values: dict[int, str] = {}
    for cell in row.findall(f"{XML_NS}c"):
        reference = cell.attrib.get("r", "A1")
        letters = re.match(r"[A-Z]+", reference)
        if not letters:
            continue
        column = 0
        for letter in letters.group(0):
            column = column * 26 + ord(letter) - 64
        cell_type = cell.attrib.get("t", "")
        value_node = cell.find(f"{XML_NS}v")
        if cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.iter(f"{XML_NS}t"))
        elif value_node is None:
            value = ""
        elif cell_type == "s":
            value = shared_strings[int(value_node.text or 0)]
        else:
            value = value_node.text or ""
        values[column - 1] = value
    width = max(values, default=-1) + 1
    return [values.get(index, "") for index in range(width)]
