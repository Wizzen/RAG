from __future__ import annotations

"""向模型提供追踪表查询的明确嵌套参数结构，避免猜测过滤器格式。"""

from typing import Literal
from typing_extensions import NotRequired, TypedDict


class TrackerFilter(TypedDict):
    field: str
    op: Literal['eq','ne','contains','in','between','gt','gte','lt','lte','is_empty']
    value: str | int | float | bool | list[str | int | float | bool]
    type: NotRequired[Literal['text','number','date']]


class TrackerOrder(TypedDict):
    field: str
    direction: Literal['asc','desc']
    type: NotRequired[Literal['text','number','date']]


TrackerMetric = TypedDict('TrackerMetric', {
    'op': Literal['count','distinct_count','sum','avg','min','max'],
    'field': NotRequired[str],
    'as': NotRequired[str],
})


class TrackerGroup(TypedDict):
    field: str
    period: NotRequired[Literal['value','day','month','year']]
