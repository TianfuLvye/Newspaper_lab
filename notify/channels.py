"""Lab 9.1 通道选择。

手册建议「邮件发 PDF(主) + Telegram/飞书摘要卡片(提醒)」。
本切片只落地邮件:附件原生、可归档可搜索、配置成本最低。
Telegram / 飞书 / 静态站 / 移动端客户端都留接口名,调用时明确跳过。
"""
from __future__ import annotations

from collections.abc import Sequence

# 已实现
IMPLEMENTED = ("email",)
# 手册提过、代码里认名字,但本切片不发送
DEFERRED = ("telegram", "feishu", "wecom", "rss", "client")


class UnknownChannelError(ValueError):
    pass


def select_channels(requested: Sequence[str] | None) -> tuple[list[str], list[str]]:
    """把配置里的通道拆成(马上发, 本切片跳过)。

    空列表视为默认 ``email``。未知名字直接报错,避免把 typo 当成「没开推送」。
    """
    names = [str(x).strip().lower() for x in (requested or ()) if str(x).strip()]
    if not names:
        names = ["email"]
    enabled: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        if name in IMPLEMENTED:
            enabled.append(name)
        elif name in DEFERRED:
            skipped.append(name)
        else:
            raise UnknownChannelError(
                f"未知推送通道 {name!r}(支持 {', '.join(IMPLEMENTED + DEFERRED)})"
            )
    return enabled, skipped
