"""LLM 子包：通用客户端、配置档案与管线 Gateway。

其他工具可直接复用：

    from viberadar.llm import LLMClient, load_store
    profile = load_store(settings).load().active_profile()
    result = LLMClient(profile).chat_json(system="...", user="...", model=profile.model_fast)
"""

from .client import CompletionResult, LLMClient, extract_json_object
from .config import (
    PRESETS,
    STAGES,
    TIERS,
    LLMConfig,
    LLMConfigStore,
    LLMPrice,
    LLMProfile,
    load_store,
    preset_profile,
)
from .costs import compute_cost, estimate_tokens, prompt_digest
from .gateway import (
    COMMON_SYSTEM,
    BudgetExceeded,
    LLMGateway,
    LLMResult,
    make_gateway,
)

__all__ = [
    "COMMON_SYSTEM",
    "PRESETS",
    "STAGES",
    "TIERS",
    "BudgetExceeded",
    "CompletionResult",
    "LLMClient",
    "LLMConfig",
    "LLMConfigStore",
    "LLMGateway",
    "LLMPrice",
    "LLMProfile",
    "LLMResult",
    "compute_cost",
    "estimate_tokens",
    "extract_json_object",
    "load_store",
    "make_gateway",
    "preset_profile",
    "prompt_digest",
]
