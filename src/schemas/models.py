"""
标准化行程数据模型 —— 基于 Pydantic v2 BaseModel。

所有模型严格约束格式、内置输入校验，从源头规范大模型输出。
原生支持 model_dump() / model_dump_json()，完全兼容 LangGraph 状态与 API 输出。
"""

from typing import ClassVar
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ============================================================
# 统一模型配置混入
# ============================================================
class _BaseModel(BaseModel):
    """项目级 BaseModel 基类。

    统一配置：
    - from_attributes=True    支持从 ORM/对象实例化
    - extra='forbid'          禁止额外字段，自动过滤非法输入
    - str_strip_whitespace=True  自动去除字符串首尾空白
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


# ============================================================
# Spot —— 景点模型
# ============================================================
class Spot(_BaseModel):
    """单个景点信息。

    用于描述行程中的每个游览目的地，包含名称、地址、游玩时长、
    门票价格、景区等级、所属区域及推荐标签等字段。
    """

    name: str = Field(
        ...,
        min_length=1,
        description="景点名称",
        examples=["故宫博物院"],
    )
    address: str = Field(
        ...,
        min_length=1,
        description="景点地址/位置",
        examples=["北京市东城区景山前街4号"],
    )
    duration: float = Field(
        ...,
        ge=0.5,
        description="预计游玩时长（单位：小时），最小 0.5 小时",
        examples=[2.5],
    )
    ticket_price: float = Field(
        default=0.0,
        ge=0,
        description="门票价格（单位：元），最小 0（免费景点）",
        examples=[60.00],
    )
    level: str = Field(
        default="",
        description="景区等级（5A/4A/3A/2A/A/无），空字符串表示未收录",
        examples=["5A"],
    )
    area: str = Field(
        default="",
        description="所属行政区/区域，如'青羊区'、'西湖区'",
        examples=["东城区"],
    )
    core_feature: str = Field(
        default="",
        description="核心特色，一句话概括景点最突出的亮点",
        examples=["世界五大宫之首，明清皇家宫殿"],
    )
    time_slot: str = Field(
        default="",
        description="游览时段（上午/下午/晚上），空字符串表示未指定",
        examples=["上午"],
    )
    tags: list[str] = Field(
        default_factory=list,
        description="景点标签，自动去重",
        examples=[["历史", "博物馆", "亲子"]],
    )
    recommendation: str = Field(
        default="",
        description="推荐理由",
        examples=["世界五大宫之首，明清皇家宫殿，必打卡地标"],
    )

    @field_validator("ticket_price")
    @classmethod
    def _round_ticket_price(cls, v: float) -> float:
        """门票价格自动保留 2 位小数。"""
        return round(v, 2)

    @field_validator("duration")
    @classmethod
    def _round_duration(cls, v: float) -> float:
        """游玩时长自动保留 1 位小数。"""
        return round(v, 1)

    @field_validator("tags")
    @classmethod
    def _deduplicate_tags(cls, v: list[str]) -> list[str]:
        """标签自动去重，保持原有顺序。"""
        seen: set[str] = set()
        result: list[str] = []
        for tag in v:
            if tag not in seen:
                seen.add(tag)
                result.append(tag)
        return result


# ============================================================
# DailyPlan —— 单日行程模型
# ============================================================
class DailyPlan(_BaseModel):
    """单日行程明细。

    描述某一出行日的游览安排，包含景点列表、餐饮推荐、
    交通说明及当日预算等字段。
    """

    day_index: int = Field(
        ...,
        ge=1,
        description="第几天（从 1 开始计数）",
        examples=[1],
    )
    theme: str = Field(
        ...,
        min_length=1,
        description="当日行程主题",
        examples=["皇城根下深度文化游"],
    )
    spots: list[Spot] = Field(
        default_factory=list,
        description="当日景点列表（按游览顺序排列）",
    )
    food_recommendation: list[str] = Field(
        default_factory=list,
        description="当日餐饮推荐",
        examples=[["全聚德烤鸭（前门店）", "护国寺小吃（护国寺街）"]],
    )
    traffic_note: str = Field(
        default="",
        description="当日交通说明（出行方式、路线建议）",
        examples=["全程地铁+步行，建议购买一日通票"],
    )
    accommodation: str = Field(
        default="",
        description="当日住宿建议（酒店区域/类型/价位）",
        examples=["二七广场附近经济型酒店（如汉庭/如家，约180元/晚/间）"],
    )
    budget_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="预算明细：{住宿/餐饮/市内交通/景点门票/应急备用金: 金额}",
        examples=[{"住宿": 180, "餐饮": 160, "市内交通": 60, "景点门票": 130, "应急备用金": 50}],
    )
    daily_budget: float = Field(
        default=0.0,
        ge=0,
        description="当日预估预算（单位：元），最小 0",
        examples=[580.00],
    )

    @field_validator("daily_budget")
    @classmethod
    def _round_daily_budget(cls, v: float) -> float:
        """当日预算自动保留 2 位小数。"""
        return round(v, 2)


# ============================================================
# TravelPlan —— 完整行程模型
# ============================================================
class TravelPlan(_BaseModel):
    """完整旅行行程计划。

    聚合目的地信息、总天数、总预算、出行人群、偏好标签
    以及逐日行程明细。
    """

    destination: str = Field(
        ...,
        min_length=1,
        description="目的地城市",
        examples=["北京"],
    )
    total_days: int = Field(
        ...,
        ge=1,
        description="出行总天数，最小 1 天",
        examples=[3],
    )
    total_budget: float = Field(
        ...,
        ge=0,
        description="总预算上限（单位：元），最小 0",
        examples=[3000.00],
    )
    people: str = Field(
        ...,
        min_length=1,
        description="出行人群描述",
        examples=["一家三口（父母+8岁孩子）"],
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="出行偏好标签",
        examples=[["历史文化", "美食", "亲子友好"]],
    )
    daily_plans: list[DailyPlan] = Field(
        default_factory=list,
        description="每日行程明细（长度必须等于 total_days）",
    )
    travel_tips: list[str] = Field(
        default_factory=list,
        description="出行注意事项",
        examples=[["北京早晚温差大，建议带薄外套", "热门景点需提前预约"]],
    )

    @field_validator("total_budget")
    @classmethod
    def _round_total_budget(cls, v: float) -> float:
        """总预算自动保留 2 位小数。"""
        return round(v, 2)

    @model_validator(mode="after")
    def _validate_daily_plans_count(self) -> "TravelPlan":
        """模型级校验：daily_plans 长度必须等于 total_days。"""
        if len(self.daily_plans) != self.total_days:
            raise ValueError(
                f"daily_plans 长度 ({len(self.daily_plans)}) "
                f"与 total_days ({self.total_days}) 不一致，"
                f"请确保每日行程数量等于出行总天数。"
            )
        return self


# ============================================================
# UserDemand —— 用户需求模型
# ============================================================
class UserDemand(_BaseModel):
    """用户出行需求输入。

    作为 Agent 流程的入口数据结构，承载用户的基础出行诉求。
    """

    destination: str = Field(
        ...,
        min_length=1,
        description="目的地城市",
        examples=["杭州"],
    )
    days: int = Field(
        ...,
        ge=1,
        description="出行天数，最小 1 天",
        examples=[3],
    )
    total_budget: float = Field(
        ...,
        ge=0,
        description="总预算上限（单位：元），最小 0",
        examples=[2000.00],
    )
    people: str = Field(
        ...,
        min_length=1,
        description="出行人群描述",
        examples=["情侣"],
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="偏好标签，默认空列表",
        examples=[["自然风光", "浪漫", "美食"]],
    )
    remark: str = Field(
        default="",
        description="其他补充要求",
        examples=["希望每天不要太赶，下午留出自由活动时间"],
    )

    @field_validator("total_budget")
    @classmethod
    def _round_total_budget(cls, v: float) -> float:
        """总预算自动保留 2 位小数。"""
        return round(v, 2)


# ============================================================
# LLM 输出校验基类 —— 宽容模式，忽略大模型多余字段
# ============================================================
class _LLMOutputBase(BaseModel):
    """LLM 输出校验模型基类。

    与 _BaseModel 的区别：
    - extra='ignore'  宽容模式：忽略大模型输出的额外字段而非报错
    - 其余配置一致
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


# ============================================================
# DemandAnalyzeOutput —— 需求拆解节点输出模型
# ============================================================
class DemandAnalyzeOutput(_LLMOutputBase):
    """demand_analyze_node 节点 LLM 输出校验模型。

    对应节点输出的结构化用户需求，所有必填字段与 UserDemand 保持一致。
    """

    destination: str = Field(..., min_length=1, description="目的地城市")
    days: int = Field(..., ge=1, description="出行天数")
    total_budget: float = Field(..., ge=0, description="总预算上限")
    people: str = Field(..., min_length=1, description="出行人群描述")
    preferences: list[str] = Field(default_factory=list, description="偏好标签")
    remark: str = Field(default="", description="补充要求")

    @field_validator("total_budget")
    @classmethod
    def _round_budget(cls, v: float) -> float:
        return round(v, 2)


# ============================================================
# DailyFramework —— 单日行程框架模型
# ============================================================
class DailyFramework(_LLMOutputBase):
    """outline_generate_node 输出的单日框架模型。"""

    day_index: int = Field(..., ge=1, description="第几天")
    theme: str = Field(..., min_length=1, description="当日主题")
    budget: float = Field(..., ge=0, description="当日预算")
    spot_types: list[str] = Field(default_factory=list, description="景点类型")
    prefer_tags: list[str] = Field(default_factory=list, description="偏好标签")
    food_style: str = Field(default="", description="餐饮风格")


# ============================================================
# OutlineOutput —— 行程框架输出模型
# ============================================================
class OutlineOutput(_LLMOutputBase):
    """outline_generate_node 节点 LLM 输出校验模型。"""

    total_days: int = Field(..., ge=1, description="总天数")
    budget_split: list[float] = Field(default_factory=list, description="每日预算分配")
    daily_frameworks: list[DailyFramework] = Field(
        default_factory=list,
        description="每日框架列表",
    )

    @model_validator(mode="after")
    def _validate_framework_count(self) -> "OutlineOutput":
        """校验每日框架数量与 total_days 一致。"""
        if self.daily_frameworks and len(self.daily_frameworks) != self.total_days:
            raise ValueError(
                f"daily_frameworks 长度 ({len(self.daily_frameworks)}) "
                f"与 total_days ({self.total_days}) 不一致"
            )
        return self


# ============================================================
# CheckOutput —— 校验输出模型
# ============================================================
class CheckOutput(_LLMOutputBase):
    """plan_check_node 使用的校验结果模型。"""

    is_pass: bool = Field(default=True, description="是否通过校验")
    issues: list[str] = Field(default_factory=list, description="问题列表")
    suggestions: list[str] = Field(default_factory=list, description="改进建议")


# ============================================================
# FactCheckOutput —— 事实校验节点输出模型
# ============================================================
class FactCheckOutput(_LLMOutputBase):
    """fact_check_node 使用的 LLM 事实校验结果模型。

    对已生成行程执行三项强制校验：
    1. 景点归属 —— 所有景点必须归属目的城市，无虚构/跨市错配
    2. 物价合理性 —— 门票/餐饮/住宿贴合目的地真实消费水平
    3. 行程合理性 —— 每日时长安排合理，无时间冲突/单日过载
    """

    is_pass: bool = Field(
        default=True,
        description="是否通过全部三项事实校验（三项均无问题才为 true）",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="校验发现的具体问题列表，每条标注所属校验项",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="针对各问题的具体可操作改进建议",
    )
