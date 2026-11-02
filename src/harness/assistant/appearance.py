"""Bot 外观：服务端是调色板的唯一权威，新建 Bot 从未用过的外观里随机取。

外观只影响呈现，不参与运行判定。选中的结果在建号时一次性写进
``config["appearance"]``，之后不再被重算——重试、并发与刷新都读到同一份。
"""

from collections.abc import Iterable, Mapping, Sequence
from secrets import SystemRandom

# 与客户端「新建 Bot」弹窗同一份取色表；服务端先落库，前端只渲染服务端给的清单。
APPEARANCE_COLORS: tuple[str, ...] = (
    "#ffffff",
    "#a9734f",
    "#ff3b30",
    "#ff6a00",
    "#ff9f0a",
    "#25c06a",
    "#0d9381",
    "#126fd4",
    "#7442d1",
    "#ff2d95",
    "#8e8e93",
    "#c82779",
)

APPEARANCE_SHAPES: tuple[str, ...] = (
    "blob",
    "drop",
    "block",
    "star",
    "cloud",
    "pebble",
    "hexagon",
    "petal",
    "rounded-triangle",
    "capsule",
    "leaf",
    "diamond",
    "comet",
    "soft-square",
)

DEFAULT_APPEARANCE: dict[str, str] = {"color": "#126fd4", "avatar": "blob"}

_RANDOM = SystemRandom()


def known_color(color: str) -> bool:
    return color.lower() in {item.lower() for item in APPEARANCE_COLORS}


def known_shape(avatar: str) -> bool:
    return avatar in APPEARANCE_SHAPES


def _least_repeated(values: Sequence[str], counts: Mapping[str, int]) -> list[str]:
    """出现次数最少的取值；并列时全部返回，由调用方随机取一个。"""
    if not values:
        return []
    lowest = min(counts.get(value, 0) for value in values)
    return [value for value in values if counts.get(value, 0) == lowest]


def choose_appearance(
    used: Iterable[Mapping[str, str]],
    *,
    colors: Sequence[str] = APPEARANCE_COLORS,
    shapes: Sequence[str] = APPEARANCE_SHAPES,
) -> dict[str, str]:
    """给新 Bot 挑一套外观。

    规则：优先整项没用过的颜色与图标（先满足「未用单项」，再满足「未用组合」）；
    两边都用过了才退到出现次数最少的那些，避免继续扎堆。
    """
    used_pairs: set[tuple[str, str]] = set()
    color_counts: dict[str, int] = {}
    shape_counts: dict[str, int] = {}
    for item in used:
        color = str(item.get("color") or "").lower()
        avatar = str(item.get("avatar") or "")
        if not color and not avatar:
            continue
        if color:
            used_pairs.add((color, avatar))
            color_counts[color] = color_counts.get(color, 0) + 1
        if avatar:
            shape_counts[avatar] = shape_counts.get(avatar, 0) + 1

    free_colors = [color for color in colors if color.lower() not in color_counts]
    free_shapes = [shape for shape in shapes if shape not in shape_counts]
    color_pool = free_colors or list(colors)
    shape_pool = free_shapes or list(shapes)

    unused_pairs = [
        (color, shape)
        for color in color_pool
        for shape in shape_pool
        if (color.lower(), shape) not in used_pairs
    ]
    if unused_pairs:
        color, shape = _RANDOM.choice(unused_pairs)
        return {"color": color, "avatar": shape}

    color = _RANDOM.choice(_least_repeated(list(colors), color_counts) or list(colors))
    shape = _RANDOM.choice(_least_repeated(list(shapes), shape_counts) or list(shapes))
    return {"color": color, "avatar": shape}
