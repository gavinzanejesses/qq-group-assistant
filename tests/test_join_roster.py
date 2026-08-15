from __future__ import annotations

import io
import zipfile

from join_roster import (
    evaluate_roster_application,
    match_roster_class,
    normalize_class_label,
    parse_roster_xlsx,
)


def test_class_names_match_group_names() -> None:
    roster = {"classes": {"26计科1": 39, "26信安2": 39, "26智能3": 38}}
    assert normalize_class_label("2026级计算机科学与技术1班") == "26计科1"
    assert match_roster_class(roster, "2026级信安2班") == "26信安2"
    assert match_roster_class(roster, "2026级人工智能3班") == "26智能3"


def test_application_requires_class_name_and_student_id() -> None:
    roster = {
        "students": [
            {"class_name": "26计科1", "student_id": "99990000001", "name": "张三"},
            {"class_name": "26计科2", "student_id": "99990000002", "name": "李四"},
        ]
    }
    approved, _, student = evaluate_roster_application(
        roster, "26计科1", "答案：张三 99990000001"
    )
    assert approved is True
    assert student and student["name"] == "张三"
    assert evaluate_roster_application(roster, "26计科1", "李四 99990000002")[0] is False
    assert evaluate_roster_application(roster, "26计科1", "王五 99990000001")[0] is False


def test_parse_minimal_xlsx_roster() -> None:
    workbook = b'''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Export" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="worksheet"/>
</Relationships>'''
    worksheet = '''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
 <row r="1"><c r="A1" t="inlineStr"><is><t>班级</t></is></c><c r="B1" t="inlineStr"><is><t>学号</t></is></c><c r="C1" t="inlineStr"><is><t>姓名</t></is></c></row>
 <row r="2"><c r="A2" t="inlineStr"><is><t>26计科1</t></is></c><c r="B2"><v>99990000001</v></c><c r="C2" t="inlineStr"><is><t>张三</t></is></c></row>
</sheetData></worksheet>'''.encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    assert parse_roster_xlsx(buffer.getvalue()) == [
        {"class_name": "26计科1", "student_id": "99990000001", "name": "张三"}
    ]
