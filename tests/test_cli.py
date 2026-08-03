from pathlib import Path

import yaml

from qq_assistant_cli import validate_config

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_is_valid() -> None:
    assert validate_config(ROOT / "config.example.yml") == []


def test_invalid_regex_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        "group_id: 1\n"
        "admin_commands:\n  authorized_users: [1]\n"
        "remark:\n  pattern: '[bad'\n"
        "join_review: {}\nmoderation: {}\n",
        encoding="utf-8",
    )
    assert any("正则无效" in error for error in validate_config(path))


def test_remark_template_requires_mentions(tmp_path: Path) -> None:
    config = yaml.safe_load((ROOT / "config.example.yml").read_text(encoding="utf-8"))
    config["remark"]["group_message_template"] = "请修改名片：{example}"
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    assert any("必须包含 {mentions}" in error for error in validate_config(path))
