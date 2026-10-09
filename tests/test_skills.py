"""core/skill/skills.py 三来源扫描、同名优先级与读取接口测试。"""
import pytest

from core.skill import skills as skills_mod


def _write_skill(root, dir_name: str, name: str, description: str = "") -> None:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{name} body",
        encoding="utf-8",
    )


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """把扫描来源指到临时目录；测试后还原真实注册表（conftest 已预扫）。"""
    dirs = {kind: tmp_path / kind for kind in ("global", "user", "project")}
    monkeypatch.setattr(skills_mod, "SKILL_SOURCES",
                        (("global", dirs["global"]), ("user", dirs["user"]),
                         ("project", dirs["project"])))
    saved = dict(skills_mod.SKILL_REGISTRY)
    yield dirs
    skills_mod.SKILL_REGISTRY.clear()
    skills_mod.SKILL_REGISTRY.update(saved)


def test_scan_skills_loads_three_sources_with_types(sources):
    _write_skill(sources["global"], "g-dir", "g-skill", "global desc")
    _write_skill(sources["user"], "u-dir", "u-skill", "user desc")
    _write_skill(sources["project"], "p-dir", "p-skill", "project desc")

    skills_mod.scan_skills()

    assert set(skills_mod.SKILL_REGISTRY) == {"g-skill", "u-skill", "p-skill"}
    assert skills_mod.SKILL_REGISTRY["g-skill"].skill_type == "global"
    assert skills_mod.SKILL_REGISTRY["u-skill"].skill_type == "user"
    assert skills_mod.SKILL_REGISTRY["p-skill"].skill_type == "project"
    assert isinstance(skills_mod.SKILL_REGISTRY["p-skill"], skills_mod.Skill)
    assert skills_mod.SKILL_REGISTRY["p-skill"].description == "project desc"


def test_scan_same_name_prefers_project_then_user_then_global(sources):
    for kind in ("global", "user", "project"):
        _write_skill(sources[kind], f"dup-{kind}", "dup", f"{kind} desc")
    _write_skill(sources["global"], "gu-global", "dup-gu", "global desc")
    _write_skill(sources["user"], "gu-user", "dup-gu", "user desc")

    skills_mod.scan_skills()

    assert skills_mod.SKILL_REGISTRY["dup"].description == "project desc"
    assert skills_mod.SKILL_REGISTRY["dup"].skill_type == "project"
    assert skills_mod.SKILL_REGISTRY["dup-gu"].description == "user desc"
    assert skills_mod.SKILL_REGISTRY["dup-gu"].skill_type == "user"


def test_scan_skills_skips_missing_sources(sources):
    _write_skill(sources["project"], "p-dir", "only-project")

    skills_mod.scan_skills()

    assert set(skills_mod.SKILL_REGISTRY) == {"only-project"}


def test_load_skill_returns_manifest_content(sources):
    _write_skill(sources["user"], "u-dir", "u-skill", "user desc")

    skills_mod.scan_skills()

    assert "u-skill body" in skills_mod.load_skill("u-skill")
    missing = skills_mod.load_skill("nope")
    assert missing.startswith("Skill not found: nope")
    assert "u-skill" in missing


def test_load_skills_returns_registry_of_skill_objects(sources):
    skills_mod.scan_skills()
    assert skills_mod.load_skills() is None

    _write_skill(sources["project"], "p-dir", "p-skill", "project desc")
    skills_mod.scan_skills()

    loaded = skills_mod.load_skills()
    assert loaded is skills_mod.SKILL_REGISTRY
    assert isinstance(loaded["p-skill"], skills_mod.Skill)
    assert loaded["p-skill"].skill_type == "project"
