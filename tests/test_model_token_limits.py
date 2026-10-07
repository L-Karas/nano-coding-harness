"""ModelClient 动态 token 预算：上下文/最大输出直接取自注册表，阈值按当前模型动态解析。"""
import core.client.model as model
import core.config as config_mod


def _client(monkeypatch, registry_ctx: int, registry_out, config: config_mod.AgentConfig):
    """构造仅带 provider/model 的 client，并把注册表与全局配置换成测试值。"""
    monkeypatch.setattr(model, "CONFIGMANAGER", config_mod.AgentConfigManager(config))
    monkeypatch.setitem(model._MODEL_LIST, "TestProvider", {
        "base_url": "",
        "model_list": {"test-model": {"context_length": registry_ctx, "max_output": registry_out}},
    })
    client = model.ModelClient.__new__(model.ModelClient)  # 不建真实 client
    client.current_provider = "TestProvider"
    client.current_model = "test-model"
    return client


def test_budgets_follow_current_model(monkeypatch):
    cfg = config_mod.AgentConfig(compact_threshold=0.5, reserve_threshold=0.2)
    client = _client(monkeypatch, 262_144, 52_000, cfg)
    assert client.load_context_length() == 262_144
    assert client.load_max_output() == 52_000
    assert client.compact_threshold() == 131_072  # 0.5 * 262144
    assert client.reserve_threshold() == 52_428  # 0.2 * 262144（按上下文比例，非阈值比例）


def test_null_max_output_disables_clamping_and_reserves_escalated_headroom(monkeypatch):
    """注册表 max_output 为空 = 无固定输出上限：不钳制；压缩上限用 上下文 - escalated_max_tokens。"""
    cfg = config_mod.AgentConfig(compact_threshold=500_000, escalated_max_tokens=32_000)
    client = _client(monkeypatch, 262_144, None, cfg)
    assert client.load_max_output() is None
    assert client.clamp_max_tokens(200_000) == 200_000
    assert client.compact_threshold() == 230_144  # 262144 - 32000


def test_unselected_model_has_no_budget(monkeypatch):
    monkeypatch.setattr(model, "CONFIGMANAGER", config_mod.AgentConfigManager(config_mod.AgentConfig()))
    client = model.ModelClient.__new__(model.ModelClient)
    client.current_provider = ""
    client.current_model = ""
    assert client.load_context_length() == 0
    assert client.load_max_output() is None
    assert client.compact_threshold() == 0
    assert client.clamp_max_tokens(12_000) == 12_000


def test_clamp_max_tokens_uses_current_model_output_limit(monkeypatch):
    client = _client(monkeypatch, 1_000_000, 128_000, config_mod.AgentConfig())
    assert client.clamp_max_tokens(200_000) == 128_000
    assert client.clamp_max_tokens(12_000) == 12_000
