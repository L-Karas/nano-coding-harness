"""core/config.py 校验逻辑测试。"""
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

import core.config as config_mod
from core.config import AgentConfig, AgentConfigManager


def test_threshold_fields_are_absolute_int_tokens():
    """压缩阈值只接受绝对 token 数，比例语义已移除。"""
    cfg = AgentConfig(compact_trigger_tokens=200_000, compact_reserve_tokens=30_000)
    assert cfg.compact_trigger_tokens == 200_000
    assert cfg.compact_reserve_tokens == 30_000
    with pytest.raises(ValidationError):
        AgentConfig(compact_trigger_tokens=0.5)


def test_threshold_defaults_are_absolute_token_budgets():
    cfg = AgentConfig()
    assert cfg.compact_trigger_tokens == 500_000
    assert cfg.compact_reserve_tokens == 50_000


def test_invalid_thinking_level_rejected():
    with pytest.raises(Exception):
        AgentConfig(default_thinking_level="ultra")


def test_approval_mode_defaults_to_manual_and_rejects_unknown():
    assert AgentConfig().approval_mode == "Manual"
    assert AgentConfig(approval_mode="Yolo").approval_mode == "Yolo"
    with pytest.raises(ValidationError):
        AgentConfig(approval_mode="always-ask")


def test_resolve_compact_trigger_tokens_prefers_configured_value_below_fallback():
    """窗口 80% 以内优先使用配置值，即使 context - escalated 更小。"""
    cfg = AgentConfig(compact_trigger_tokens=500_000, escalated_max_tokens=900_000)
    assert cfg.resolve_compact_trigger_tokens(1_000_000) == 500_000
    exact = AgentConfig(compact_trigger_tokens=800_000, escalated_max_tokens=900_000)
    assert exact.resolve_compact_trigger_tokens(1_000_000) == 800_000  # 等于兜底


def test_resolve_compact_trigger_tokens_caps_configured_value_at_80_percent():
    """配置值超过窗口 80% 且 context - escalated 也更高时，使用 80% 兜底。"""
    cfg = AgentConfig(compact_trigger_tokens=900_000, escalated_max_tokens=32_000)
    assert cfg.resolve_compact_trigger_tokens(1_000_000) == 800_000  # context - escalated = 968000


def test_resolve_compact_trigger_tokens_uses_context_reserve_when_below_fallback():
    """配置值超过 80% 时，若 context - escalated 更低则使用它。"""
    cfg = AgentConfig(compact_trigger_tokens=900_000, escalated_max_tokens=300_000)
    assert cfg.resolve_compact_trigger_tokens(1_000_000) == 700_000  # 80% = 800000
    low = AgentConfig(compact_trigger_tokens=900_000, escalated_max_tokens=60_000)
    assert low.resolve_compact_trigger_tokens(200_000) == 140_000  # 80% = 160000


def test_resolve_compact_trigger_tokens_degenerate_reserve_keeps_minimum():
    """输出余量不小于上下文时 context - escalated 保底为 1。"""
    cfg = AgentConfig(compact_trigger_tokens=500_000, escalated_max_tokens=150_000)
    assert cfg.resolve_compact_trigger_tokens(100_000) == 1
    equal = AgentConfig(compact_trigger_tokens=500_000, escalated_max_tokens=32_000)
    assert equal.resolve_compact_trigger_tokens(32_768) == 768  # 32768 - 32000


def test_resolve_compact_trigger_tokens_returns_zero_without_context_window():
    """未选择模型（上下文为 0）：阈值 0 代表无窗口，压缩管线据此原样返回。"""
    cfg = AgentConfig(compact_trigger_tokens=500_000)
    assert cfg.resolve_compact_trigger_tokens(0) == 0


def test_resolve_compact_reserve_tokens_caps_at_trigger():
    """保留预算是绝对 token 数，封顶有效压缩阈值。"""
    cfg = AgentConfig(compact_reserve_tokens=50_000)
    assert cfg.resolve_compact_reserve_tokens(150_000) == 50_000
    assert cfg.resolve_compact_reserve_tokens(20_000) == 20_000  # 封顶压缩阈值


def test_clamp_max_tokens_to_effective_output_limit():
    """有固定最大输出时钳制到该值；为空（无固定上限）时不钳制。"""
    cfg = AgentConfig()
    assert cfg.clamp_max_tokens(24_000, 200_000) == 24_000
    assert cfg.clamp_max_tokens(300_000, 128_000) == 128_000
    assert cfg.clamp_max_tokens(200_000, None) == 200_000
    assert cfg.clamp_max_tokens(1, 0) == 1  # 退化下限


def test_legacy_tool_budget_fields_are_removed_and_ignored():
    """persist_tool_tokens / keep_recent_tool_results 已废弃：不再是模型字段，旧配置文件仍可加载。"""
    assert "persist_tool_tokens" not in AgentConfig.model_fields
    assert "keep_recent_tool_results" not in AgentConfig.model_fields
    cfg = AgentConfig.model_validate({"persist_tool_tokens": 3000, "keep_recent_tool_results": 30})
    assert not hasattr(cfg, "persist_tool_tokens")
    assert not hasattr(cfg, "keep_recent_tool_results")


def test_load_config_reads_threshold_keys(tmp_path, monkeypatch):
    setting_file = tmp_path / ".settings.json"
    setting_file.write_text(json.dumps({"compact_trigger_tokens": 300_000,
                                        "compact_reserve_tokens": 30_000}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)
    cfg = AgentConfigManager.load_config().config
    assert (cfg.compact_trigger_tokens, cfg.compact_reserve_tokens) == (300_000, 30_000)


def test_load_config_ignores_legacy_threshold_keys(tmp_path, monkeypatch):
    """旧 key 已废弃：加载不报错，缺省回落到默认预算。"""
    setting_file = tmp_path / ".settings.json"
    setting_file.write_text(json.dumps({"compact_threshold": 0.5}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)
    assert AgentConfigManager.load_config().config.compact_trigger_tokens == 500_000


def test_manager_save_load_roundtrip(tmp_path, monkeypatch):
    setting_file = tmp_path / ".settings.json"
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)
    # save_config 会同步全局单例：换掉它，避免 p:m 配置泄漏到后续测试
    monkeypatch.setattr(config_mod, "CONFIGMANAGER", AgentConfigManager(AgentConfig()))

    mgr = AgentConfigManager(AgentConfig(default_model="p:m"))
    mgr.save_config()
    assert setting_file.exists()

    loaded = AgentConfigManager.load_config()
    assert loaded.config.default_model == "p:m"
    assert loaded.config == mgr.config


def test_save_config_refreshes_runtime_singleton(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", tmp_path / ".settings.json")
    monkeypatch.setattr(config_mod, "CONFIGMANAGER",
                        AgentConfigManager(AgentConfig(default_max_tokens=1_111)))
    AgentConfigManager(AgentConfig(default_max_tokens=2_222)).save_config()
    assert config_mod.CONFIGMANAGER.config.default_max_tokens == 2_222


def test_load_missing_file_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", tmp_path / "nope.json")
    assert AgentConfigManager.load_config().config == AgentConfig()


@pytest.mark.skipif(os.name != "nt", reason="Windows 盘符路径示例")
def test_format_workdir_name_matches_spec_example():
    """Spec 的字面示例被独立断言，不通过重复实现来构造期望值。"""
    assert config_mod.format_workdir_name(
        Path(r"E:\AI-Programs\nano-harness")) == "E__AI-Programs_nano-harness"


def test_format_workdir_name_replaces_separators_and_spaces():
    """分隔符 / 盘符冒号转 `_`，空格转 `-`。"""
    if os.name == "nt":
        workdir, expected = Path(r"C:\Users\me\a b"), "C__Users_me_a-b"
    else:
        workdir, expected = Path("/home/me/a b"), "_home_me_a-b"
    assert config_mod.format_workdir_name(workdir) == expected


def test_format_workdir_name_requires_absolute_path():
    with pytest.raises(ValueError):
        config_mod.format_workdir_name(Path("relative/path"))


def test_session_dir_is_scoped_by_workdir():
    """会话保存到 .session/<格式化工作目录>/，索引文件也跟随该目录。"""
    name = config_mod.format_workdir_name(config_mod.WORKDIR)
    assert "\\" not in name
    assert "/" not in name
    assert ":" not in name
    assert config_mod.SESSION_ROOT_DIR == config_mod.HARNESS_CONFIG_DIR / ".session"
    assert config_mod.SESSION_DIR.parent == config_mod.SESSION_ROOT_DIR
    assert config_mod.SESSION_DIR.name == name
    assert config_mod.SESSION_INDEX_FILE == config_mod.SESSION_DIR / "session_index.jsonl"
