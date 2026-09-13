"""账号等级 / 每日签到（个人中心）。

规则（刻意保持简单、可解释）：
- 签到：**每天一次**，当日重复签到直接拒绝（幂等）。
- 奖励：基础 ``POINTS_PER_CHECKIN``（10）成长值；连续签到每满 ``STREAK_BONUS_EVERY``（7）天
  额外 + ``STREAK_BONUS``（20）。断签（隔天没签）连续天数清零重新计。
- 等级：``level = min(MAX_LEVEL, 1 + points // POINTS_PER_LEVEL)`` —— 每级需 100 成长值（约 10 天）。

预留窗口（未来按等级做内容限制）：
- ``FEATURE_LEVELS``：功能 → 所需等级 的映射表，加一行即声明一个限制点。
- ``can_use(level, feature)``：路由里一行 ``levels.can_use(...)`` 即可挂门槛。
  当前**没有任何调用点**，仅作为预留接口与文档说明。
"""
from __future__ import annotations

import datetime

POINTS_PER_CHECKIN = 10
STREAK_BONUS = 20
STREAK_BONUS_EVERY = 7
POINTS_PER_LEVEL = 100
MAX_LEVEL = 30

# 功能 → 所需等级（预留；当前无调用点。等级从 1 开始）
FEATURE_LEVELS: dict[str, int] = {
    # "comment": 1,          # 例：评论 / 弹幕
    # "playlist": 3,         # 例：自建播放列表
    # "batch_download": 5,   # 例：批量下载
}


def today() -> str:
    return datetime.date.today().isoformat()


def level_of(points: int) -> int:
    return max(1, min(MAX_LEVEL, 1 + int(points or 0) // POINTS_PER_LEVEL))


def next_level_need(points: int) -> int:
    """距离下一级还差多少成长值（已满级返回 0）。"""
    lv = level_of(points)
    if lv >= MAX_LEVEL:
        return 0
    return POINTS_PER_LEVEL - (int(points or 0) % POINTS_PER_LEVEL)


def reward(streak: int) -> int:
    """本次签到应得成长值：基础 + 连续每满 7 天的额外奖励。"""
    pts = POINTS_PER_CHECKIN
    if streak > 0 and streak % STREAK_BONUS_EVERY == 0:
        pts += STREAK_BONUS
    return pts


def next_streak(last_checkin: str | None, streak: int, today_str: str | None = None) -> int:
    """根据最近签到日期计算本次签到后的连续天数。"""
    today_str = today_str or today()
    if last_checkin == today_str:
        return int(streak or 0)                 # 今日已签，不变
    if last_checkin == _yesterday_str(today_str):
        return int(streak or 0) + 1             # 昨天签过 → 连续
    return 1                                    # 断签 → 重新计


def _yesterday_str(today_str: str) -> str:
    d = datetime.date.fromisoformat(today_str)
    return (d - datetime.timedelta(days=1)).isoformat()


def can_use(level: int, feature: str) -> tuple[bool, int]:
    """预留的等级门槛判断：返回 (是否允许, 该功能所需等级)。未声明的功能默认放行。"""
    need = FEATURE_LEVELS.get(feature)
    if need is None:
        return True, 0
    return int(level) >= need, int(need)
