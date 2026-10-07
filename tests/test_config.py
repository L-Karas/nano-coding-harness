"""core/config.py 校验逻辑测试。"""
import pytest

import core.config as config_mod
from core.config import AgentConfig, AgentConfigManager


def test_ratio_fields_keep_user_intent():
    """比例语义原样保留（不在加载时解析成绝对值），运行时再按当前模型解析。"""
    cfg = AgentConfig(compact_threshold=0.4, reserve_threshold=0.3)
    assert cfg.compact_threshold == 0.4
    assert cfg.reserve_threshold == 0.3


def test_invalid_thinking_level_rejected():
    with pytest.raises(Exception):
        AgentConfig(default_thinking_level="ultra")


def test_resolve_compact_threshold_scales_with_live_context():
    """阈值比例跟随当前模型上下文；有固定最大输出时上限为 上下文 - 最大输出。"""
    cfg = AgentConfig(compact_threshold=0.5)
    assert cfg.resolve_compact_threshold(1_000_000, 200_000) == 500_000
    assert cfg.resolve_compact_threshold(262_144, 52_428) == 131_072  # 0.5 * 262144


def test_resolve_compact_threshold_caps_absolute_to_output_headroom():
    """绝对值取 min(值, 上下文 - 最大输出)，给单次最大输出留空间。"""
    cfg = AgentConfig(compact_threshold=500_000)
    assert cfg.resolve_compact_threshold(262_144, 52_428) == 209_716  # 262144 - 52428
    assert cfg.resolve_compact_threshold(100_000, 150_000) == 1  # 退化下限


def test_resolve_compact_threshold_without_fixed_max_output_reserves_escalated_tokens():
    """max_output 为空：无固定输出上限，但压缩上限仍给最大单次请求（escalated）留空间。"""
    cfg = AgentConfig(compact_threshold=500_000, escalated_max_tokens=24_000)
    assert cfg.resolve_compact_threshold(262_144, None) == 238_144  # 262144 - 24000
    assert cfg.resolve_compact_threshold(1_000_000, None) == 500_000  # 绝对值本身更小


def test_resolve_reserve_threshold_is_a_fraction_of_context():
    """reserve <=1 是当前上下文长度的比例（不是压缩阈值的比例）；上限为有效压缩阈值。"""
    cfg = AgentConfig(reserve_threshold=0.2)
    assert cfg.resolve_reserve_threshold(1_000_000, 500_000) == 200_000  # 0.2 * 1M
    assert cfg.resolve_reserve_threshold(262_144, 131_072) == 52_428  # 0.2 * 262144
    assert cfg.resolve_reserve_threshold(1_000_000, 150_000) == 150_000  # 封顶压缩阈值


def test_resolve_reserve_threshold_absolute_caps_at_compact():
    cfg = AgentConfig(reserve_threshold=900_000)
    assert cfg.resolve_reserve_threshold(1_000_000, 500_000) == 500_000


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
