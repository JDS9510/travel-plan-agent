"""
天气查询工具（本地 Mock 版） —— 内置常见城市四季天气数据。

无需对接外部 API，预留扩展接口（替换 _query_weather 即可接入真实 API）。

使用 langchain @tool 装饰器，100% 兼容 LangGraph 工具调用。
"""

from langchain.tools import tool

# ============================================================
# 内置城市天气数据（按月划分）
# ============================================================
_WEATHER_DATA: dict[str, dict[int, dict]] = {
    "成都": {
        1:  {"summary": "阴冷多雾", "temp": "3-9°C", "rain": "少雨"},
        2:  {"summary": "回暖多雾", "temp": "6-13°C", "rain": "小雨偶至"},
        3:  {"summary": "春暖花开", "temp": "10-18°C", "rain": "小雨增多"},
        4:  {"summary": "温暖宜人", "temp": "15-24°C", "rain": "雨量渐增"},
        5:  {"summary": "初夏微热", "temp": "19-28°C", "rain": "降雨频繁"},
        6:  {"summary": "闷热多雨", "temp": "22-30°C", "rain": "梅雨季"},
        7:  {"summary": "高温湿热", "temp": "24-32°C", "rain": "暴雨集中"},
        8:  {"summary": "晴热少雨", "temp": "23-32°C", "rain": "偶有暴雨"},
        9:  {"summary": "秋高气爽", "temp": "19-27°C", "rain": "降雨渐少"},
        10: {"summary": "凉爽舒适", "temp": "15-22°C", "rain": "少雨宜游"},
        11: {"summary": "初冬阴冷", "temp": "10-16°C", "rain": "少雨"},
        12: {"summary": "湿冷多雾", "temp": "4-11°C", "rain": "少雨"},
    },
    "杭州": {
        1:  {"summary": "寒冷干燥", "temp": "1-8°C", "rain": "少雨偶雪"},
        2:  {"summary": "春寒料峭", "temp": "3-11°C", "rain": "小雨增多"},
        3:  {"summary": "春暖花开", "temp": "8-16°C", "rain": "春雨绵绵"},
        4:  {"summary": "温暖舒适", "temp": "14-22°C", "rain": "雨量适中"},
        5:  {"summary": "初夏宜人", "temp": "19-27°C", "rain": "降雨增多"},
        6:  {"summary": "梅雨闷热", "temp": "23-31°C", "rain": "梅雨季"},
        7:  {"summary": "高温酷暑", "temp": "27-35°C", "rain": "午后雷雨"},
        8:  {"summary": "晴热高温", "temp": "26-34°C", "rain": "偶有台风"},
        9:  {"summary": "秋高气爽", "temp": "21-29°C", "rain": "降雨渐少"},
        10: {"summary": "凉爽宜人", "temp": "15-23°C", "rain": "少雨宜游"},
        11: {"summary": "深秋微凉", "temp": "9-17°C", "rain": "少雨"},
        12: {"summary": "寒冷干燥", "temp": "3-11°C", "rain": "少雨偶雪"},
    },
    "北京": {
        1:  {"summary": "寒冷干燥", "temp": "-7-3°C", "rain": "少雪"},
        2:  {"summary": "严寒渐退", "temp": "-4-7°C", "rain": "少雪"},
        3:  {"summary": "春风多沙", "temp": "2-14°C", "rain": "少雨有沙尘"},
        4:  {"summary": "温暖花开", "temp": "9-21°C", "rain": "少雨"},
        5:  {"summary": "初夏舒适", "temp": "15-27°C", "rain": "降雨增多"},
        6:  {"summary": "盛夏炎热", "temp": "20-32°C", "rain": "雷阵雨多"},
        7:  {"summary": "高温桑拿", "temp": "23-33°C", "rain": "暴雨集中"},
        8:  {"summary": "闷热多雨", "temp": "22-31°C", "rain": "降雨频繁"},
        9:  {"summary": "秋高气爽", "temp": "15-26°C", "rain": "降雨渐少"},
        10: {"summary": "秋日宜人", "temp": "8-19°C", "rain": "少雨"},
        11: {"summary": "初冬寒冷", "temp": "0-10°C", "rain": "少雪"},
        12: {"summary": "严寒干燥", "temp": "-5-4°C", "rain": "少雪"},
    },
    "上海": {
        1:  {"summary": "湿冷阴雨", "temp": "1-8°C", "rain": "小雨偶至"},
        2:  {"summary": "春寒料峭", "temp": "3-10°C", "rain": "小雨增多"},
        3:  {"summary": "春暖花开", "temp": "7-15°C", "rain": "春雨连绵"},
        4:  {"summary": "温暖舒适", "temp": "13-21°C", "rain": "雨量适中"},
        5:  {"summary": "初夏宜人", "temp": "18-26°C", "rain": "降雨增多"},
        6:  {"summary": "梅雨闷热", "temp": "22-29°C", "rain": "梅雨季"},
        7:  {"summary": "高温酷暑", "temp": "27-35°C", "rain": "午后雷雨"},
        8:  {"summary": "晴热", "temp": "27-34°C", "rain": "偶有台风"},
        9:  {"summary": "秋爽", "temp": "22-29°C", "rain": "少雨"},
        10: {"summary": "凉爽宜人", "temp": "16-24°C", "rain": "少雨"},
        11: {"summary": "深秋微凉", "temp": "10-18°C", "rain": "少雨"},
        12: {"summary": "湿冷", "temp": "3-11°C", "rain": "小雨偶至"},
    },
}


def _get_clothing_suggestion(season: int, city: str) -> str:
    """根据月份和城市生成穿搭建议。"""
    if city == "成都":
        if season in (1, 2, 12):
            return "冬装为主：厚外套/羽绒服、毛衣、围巾，成都湿冷体感温度偏低注意保暖"
        elif season in (3, 11):
            return "春秋装+薄外套：卫衣、夹克、长裤，早晚温差大建议洋葱式穿搭"
        elif season in (4, 5, 10):
            return "春装为主：长袖T恤、薄外套、牛仔裤，可带一件轻便风衣防雨"
        else:
            return "夏装：短袖短裤、防晒衣、遮阳帽，随身带伞应对午后阵雨"
    elif city in ("杭州", "上海"):
        if season in (1, 2, 12):
            return "冬装：羽绒服/棉服、毛衣、保暖内衣，江南湿冷需注意防风保暖"
        elif season in (3, 11):
            return "春秋装+外套：针织衫、风衣、长裤，早晚偏凉随身带薄外套"
        elif season in (4, 5, 10):
            return "春装为主：衬衫、薄外套、休闲裤，江南多雨带折叠伞"
        else:
            return "夏装：透气短袖短裤、防晒装备，梅雨季/台风季务必带雨具"
    elif city == "北京":
        if season in (1, 2, 12):
            return "厚冬装：长款羽绒服、棉靴、手套帽子，北京干冷风寒效应明显"
        elif season in (3, 11):
            return "秋冬过渡装：薄羽绒服/厚外套、围巾，早晚寒冷昼夜温差大"
        elif season in (4, 10):
            return "春秋装：夹克、长袖+薄外套，春季偶有沙尘建议带口罩"
        else:
            return "夏装：短袖短裤、防晒帽，北京夏季干燥炎热注意防晒补水"
    else:
        if season in (1, 2, 12):
            return "冬季出行建议携带厚外套、毛衣、围巾等保暖衣物"
        elif season in (3, 4, 11):
            return "春秋过渡季建议携带薄外套、长裤，早晚偏凉注意添衣"
        else:
            return "夏季出行建议携带轻薄透气衣物、防晒用品、雨具"


def _get_monthly_tips(city: str, season: int) -> list[str]:
    """根据城市和月份生成出行提醒。"""
    tips: list[str] = []
    # 通用提醒
    if season in (6, 7, 8):
        tips.append("夏季出行注意防暑降温，随身携带饮用水")
        tips.append("江南梅雨季/北京雨季出行务必携带雨具")
    if season in (12, 1, 2):
        tips.append("冬季出行注意防寒保暖，路面可能结冰注意安全")
    if season in (4, 5, 10):
        tips.append(f"{'4-5月' if season <= 5 else '10月'}为{'' if city == '成都' else '杭州'}最佳旅游季节，酒店机票建议提前预订")

    # 城市特色提醒
    if city == "成都":
        tips.append("成都饮食偏辣，不习惯辛辣的游客备好肠胃药")
    elif city == "杭州":
        tips.append("西湖景区周末人流较大，建议工作日游览体验更佳")
    elif city == "北京":
        tips.append("热门景点（故宫、国博）需提前在线预约，现场可能无票")
    elif city == "上海":
        tips.append("外滩、南京路等热门区域人流密集，注意随身财物安全")

    return tips


@tool
def weather_query(destination: str, month: int) -> dict:
    """查询目的地对应月份的天气概况和出行穿搭建议。

    参数:
        destination: 目的地城市名称，如 "成都"、"杭州"、"北京"、"上海"
        month: 出行月份，1-12

    返回:
        dict: {
            "weather_summary": str,       # 天气概况
            "temperature_range": str,     # 温度范围
            "rainfall": str,              # 降水情况
            "clothing_suggestion": str,   # 穿搭建议
            "travel_tips": list[str],     # 出行提醒
        }
    """
    # 月份归一化
    month = max(1, min(12, month))

    # 匹配城市数据
    city_data = _WEATHER_DATA.get(destination)
    if city_data is None:
        # 城市未内置时返回通用建议
        season = 4 if month in (3, 4, 5) else (
            7 if month in (6, 7, 8) else (
                10 if month in (9, 10, 11) else 1
            )
        )
        return {
            "weather_summary": f"{destination} {month}月天气数据暂未收录",
            "temperature_range": "暂无精确数据",
            "rainfall": "暂无精确数据",
            "clothing_suggestion": _get_clothing_suggestion(season, destination),
            "travel_tips": [
                "建议出行前查看实时天气预报",
                f"{month}月出行请提前确认目的地气候",
            ],
        }

    month_data = city_data.get(month, city_data.get(4, {}))
    season = 4 if month in (3, 4, 5) else (
        7 if month in (6, 7, 8) else (
            10 if month in (9, 10, 11) else 1
        )
    )

    return {
        "weather_summary": month_data.get("summary", f"{month}月天气"),
        "temperature_range": month_data.get("temp", "暂无"),
        "rainfall": month_data.get("rain", "暂无"),
        "clothing_suggestion": _get_clothing_suggestion(season, destination),
        "travel_tips": _get_monthly_tips(destination, season),
    }
