"""支付服務擷取器註冊表。新增一個服務＝新增一個模組並列入 ``MODULES``。"""

from __future__ import annotations

import importlib

from .base import Adapter

MODULES = (
    "pxpayplus",
    "taiwanpay",
    "ipassmoney",
    "easywallet",
    "jkopay",
    "opay",
    "icashpay",
    "pipay",
    "pluspay",
    "gamapay",
    "pxmart",
)
# openpoint（OPEN 錢包）擷取器已寫好但不註冊：2026-11 實測 3 筆全是 icash Pay 活動、
# 條款連到 icash 網站，與 icashpay 重複。列為涵蓋缺口（見 docs/coverage.md）。


def load(provider_id: str) -> type[Adapter]:
    module = importlib.import_module(f"{__name__}.{provider_id}")
    adapter: type[Adapter] = module.ADAPTER
    if adapter.id != provider_id:
        raise ValueError(f"{provider_id} 模組的 ADAPTER.id 是 {adapter.id}")
    return adapter


def available() -> list[str]:
    return list(MODULES)
