"""支付服務擷取器註冊表。新增一個服務＝新增一個模組並列入 ``MODULES``。"""

from __future__ import annotations

import importlib

from .base import Adapter

MODULES = (
    "pxpayplus",
    "taiwanpay",
    "ipassmoney",
    "easywallet",
)


def load(provider_id: str) -> type[Adapter]:
    module = importlib.import_module(f"{__name__}.{provider_id}")
    adapter: type[Adapter] = module.ADAPTER
    if adapter.id != provider_id:
        raise ValueError(f"{provider_id} 模組的 ADAPTER.id 是 {adapter.id}")
    return adapter


def available() -> list[str]:
    return list(MODULES)
