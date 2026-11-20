from clipkit.skill import bundled_skill, install_skill


def test_bundled_skill_is_available_in_the_checkout():
    path = bundled_skill()
    assert path is not None
    assert path.name == "SKILL.md"
    assert "editorial rubric" in path.read_text(encoding="utf-8").lower()


def test_install_skill_copies_the_file(tmp_path):
    target = install_skill(tmp_path / "clipkit")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == bundled_skill().read_text(encoding="utf-8")
