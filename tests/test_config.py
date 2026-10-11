"""core/config.py 校验逻辑测试。"""
import json

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


def test_resolve_compact_trigger_tokens_caps_at_output_reserve():
    """绝对阈值取 min(值, 上下文 - 最大输出)，给单次最大输出留空间。"""
    cfg = AgentConfig(compact_trigger_tokens=500_000)
    assert cfg.resolve_compact_trigger_tokens(1_000_000, 200_000) == 500_000  # 绝对值本身更小
    assert cfg.resolve_compact_trigger_tokens(262_144, 52_428) == 209_716  # 262144 - 52428
    assert cfg.resolve_compact_trigger_tokens(100_000, 150_000) == 1  # 退化下限


def test_resolve_compact_trigger_tokens_without_fixed_max_output_reserves_escalated_tokens():
    """max_output 为空：无固定输出上限，但压缩上限仍给最大单次请求（escalated）留空间。"""
    cfg = AgentConfig(compact_trigger_tokens=500_000, escalated_max_tokens=24_000)
    assert cfg.resolve_compact_trigger_tokens(262_144, None) == 238_144  # 262144 - 24000
    assert cfg.resolve_compact_trigger_tokens(1_000_000, None) == 500_000  # 绝对值本身更小


def test_resolve_compact_trigger_tokens_returns_zero_without_context_window():
    """未选择模型（上下文为 0）：阈值 0 代表无窗口，压缩管线据此原样返回。"""
    cfg = AgentConfig(compact_trigger_tokens=500_000)
    assert cfg.resolve_compact_trigger_tokens(0, 200_000) == 0
    assert cfg.resolve_compact_trigger_tokens(0, None) == 0


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


def test_load_config_migrates_legacy_threshold_keys_and_ratio_values(tmp_path, monkeypatch):
    """旧 key 改名；旧比例值按 1M 上下文折算成绝对 token 数，并回写盘面。"""
    setting_file = tmp_path / ".settings.json"
    setting_file.write_text(json.dumps({
        "compact_threshold": 0.5,
        "reserve_threshold": 100000.0,
        "default_model": "p:m",
    }), encoding="utf-8")
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)

    cfg = AgentConfigManager.load_config().config
    assert cfg.compact_trigger_tokens == 500_000
    assert cfg.compact_reserve_tokens == 100_000
    assert cfg.default_model == "p:m"
    # 迁移一次性回写：新 key、整数值，旧 key 不残留
    raw = json.loads(setting_file.read_text(encoding="utf-8"))
    assert "compact_threshold" not in raw and "reserve_threshold" not in raw
    assert raw["compact_trigger_tokens"] == 500_000
    assert raw["compact_reserve_tokens"] == 100_000


def test_load_config_migrates_legacy_ratio_of_one(tmp_path, monkeypatch):
    """旧语义 <=1 一律是比例：值 1 表示 100% 上下文，迁移为 1M 而非 1 token。"""
    setting_file = tmp_path / ".settings.json"
    setting_file.write_text(json.dumps({"compact_threshold": 1}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)
    assert AgentConfigManager.load_config().config.compact_trigger_tokens == 1_000_000


def test_load_config_prefers_new_key_when_old_and_new_coexist(tmp_path, monkeypatch):
    """新旧 key 并存：以新 key 为准，旧 key 仅被移除（不参与折算）。"""
    setting_file = tmp_path / ".settings.json"
    setting_file.write_text(json.dumps({"compact_threshold": 0.5,
                                        "compact_trigger_tokens": 300_000}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)
    assert AgentConfigManager.load_config().config.compact_trigger_tokens == 300_000
    assert "compact_threshold" not in json.loads(setting_file.read_text(encoding="utf-8"))


def test_load_config_keeps_new_threshold_keys_untouched(tmp_path, monkeypatch):
    setting_file = tmp_path / ".settings.json"
    setting_file.write_text(json.dumps({"compact_trigger_tokens": 300_000,
                                        "compact_reserve_tokens": 30_000}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "HARNESS_SETTING_FILE", setting_file)
    cfg = AgentConfigManager.load_config().config
    assert (cfg.compact_trigger_tokens, cfg.compact_reserve_tokens) == (300_000, 30_000)


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
