"""测试进程初始化：与生产 core.bootstrap 同源，但不启动 cron 后台线程。

各测试模块在导入期就会调用 get_logger() 写日志、读模型注册表；这里先建 .harness，
再加载 hook / 技能 / 模型注册表，使测试环境与运行进程的默认状态一致。
"""
from core.bootstrap import init_harness

init_harness()

from core.client.model import load_model_registry  # noqa: E402
from core.hook.hook import install_default_hooks  # noqa: E402
from core.skill.skills import scan_skills  # noqa: E402

install_default_hooks()
scan_skills()
load_model_registry()
