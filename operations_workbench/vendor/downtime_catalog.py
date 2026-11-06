"""Curated attraction aliases from the 2026-09-21 downtime knowledge pack."""

from __future__ import annotations

ATTRACTIONS = [
    {"location": "DTDWT", "park_number": "802", "name_zh": "华特迪士尼大剧院", "name_en": "Walt Disney Grand Theatre", "aliases": ["大剧院", "华特迪士尼大剧院"]},
    {"location": "SDLAICDCT", "park_number": "703", "name_zh": "古迹探索营 - 绳索挑战道", "name_en": "Camp Discovery - Challenge Trails", "aliases": ["绳索挑战道", "挑战道", "古迹探索营绳索挑战道"]},
    {"location": "SDLAIRR", "park_number": "702", "name_zh": "雷鸣山漂流", "name_en": "Roaring Rapids", "aliases": ["漂流", "雷鸣山", "雷鸣山漂流"]},
    {"location": "SDLAISOA", "park_number": "707", "name_zh": "翱翔·飞越地平线", "name_en": "Soaring Over the Horizon", "aliases": ["飞越", "飞越地平线", "翱翔", "地平线"]},
    {"location": "SDLBOHEPB", "park_number": "604", "name_zh": "娱乐演出大楼", "name_en": "Entertainment - Parade Building", "aliases": ["巡游", "巡游大楼", "娱乐演出大楼"]},
    {"location": "SDLBOHPRD", "park_number": "", "name_zh": "上海迪士尼乐园花车巡游", "name_en": "Shanghai Disneyland Parade", "aliases": ["花车", "花车巡游", "巡游花车", "迪士尼花车巡游"]},
    {"location": "SDLFLESC", "park_number": "401", "name_zh": "奇幻童话城堡", "name_en": "Enchanted Storybook Castle", "aliases": ["城堡", "童话城堡", "奇幻童话城堡"]},
    {"location": "SDLFLHPS", "park_number": "404", "name_zh": "旋转疯蜜罐", "name_en": "Hunny Pot Spin", "aliases": ["蜂蜜罐", "疯蜜罐", "蜜罐", "旋转疯蜜罐"]},
    {"location": "SDLFLMAWP", "park_number": "408", "name_zh": "小熊维尼历险记", "name_en": "The Many Adventures of Winnie the Pooh", "aliases": ["小熊", "小熊维尼", "维尼小熊", "维尼", "小熊维尼历险记"]},
    {"location": "SDLFLPPF", "park_number": "403", "name_zh": "小飞侠天空奇遇", "name_en": "Peter Pan's Flight", "aliases": ["小飞侠", "小飞侠天空奇遇"]},
    {"location": "SDLFLSDMT", "park_number": "407", "name_zh": "七个小矮人矿山飞车", "name_en": "Seven Dwarfs Mine Train", "aliases": ["小矮人", "矿山车", "七个小矮人", "小矮人矿山车"]},
    {"location": "SDLFLVCG", "park_number": "409", "name_zh": "晶彩奇航", "name_en": "Voyage to the Crystal Grotto", "aliases": ["晶彩奇航"]},
    {"location": "SDLFLZTP", "park_number": "451", "name_zh": "疯狂动物城热力追踪", "name_en": "Zootopia Hot Pursuit", "aliases": ["热力追踪", "动物城热力追踪", "疯狂动物城热力追踪", "疯狂动物城"]},
    {"location": "SDLGIDF", "park_number": "208", "name_zh": "小飞象", "name_en": "Dumbo The Flying Elephant", "aliases": ["小飞象"]},
    {"location": "SDLGIFC", "park_number": "207", "name_zh": "幻想曲旋转木马", "name_en": "Fantasia Carousel", "aliases": ["旋转木马", "木马", "幻想曲旋转木马"]},
    {"location": "SDLTCEC", "park_number": "504", "name_zh": "探险家独木舟", "name_en": "Explorer Canoes", "aliases": ["独木舟", "探险家独木舟"]},
    {"location": "SDLTCP8", "park_number": "", "name_zh": "P8 项目", "name_en": "P8 Project", "aliases": ["P8", "P8项目"]},
    {"location": "SDLTCPOTC", "park_number": "503", "name_zh": "加勒比海盗：沉船宝藏之战", "name_en": "Pirates of the Caribbean", "aliases": ["海盗", "加勒比", "加勒比海盗", "沉船宝藏"]},
    {"location": "SDLTLAES", "park_number": "307", "name_zh": "E 空间·百事舞台", "name_en": "Pepsi E Stage", "aliases": ["百事舞台", "E空间", "E空间百事舞台"]},
    {"location": "SDLTLBLPR", "park_number": "3034", "name_zh": "巴斯光年星际营救", "name_en": "Buzz Lightyear Planet Rescue", "aliases": ["巴斯", "巴斯光年", "巴斯光年星际营救"]},
    {"location": "SDLTLJP", "park_number": "301", "name_zh": "喷气背包飞行器", "name_en": "Jet Packs", "aliases": ["喷气背包", "背包", "喷气背包飞行器"]},
    {"location": "SDLTLPR", "park_number": "302", "name_zh": "创极速光轮", "name_en": "Tron Lightcycle Power Run", "aliases": ["创", "TRON", "极速光轮", "创极速光轮", "创极光轮", "光轮摩托"]},
    {"location": "SDLTSLRR", "park_number": "351", "name_zh": "抱抱龙冲天赛车", "name_en": "Rex's Racer", "aliases": ["抱抱龙", "抱抱龙冲天赛车"]},
    {"location": "SDLTSLSL", "park_number": "353", "name_zh": "弹簧狗团团转", "name_en": "Slinky Dog Spin", "aliases": ["弹簧狗", "弹簧狗团团转"]},
    {"location": "SDLTSLWR", "park_number": "352", "name_zh": "胡迪牛仔嘉年华", "name_en": "Woody's Roundup", "aliases": ["胡迪", "胡迪牛仔嘉年华", "牛仔嘉年华"]},
]

ATTRACTION_BY_LOCATION = {item["location"]: item for item in ATTRACTIONS}

# Top-level Maximo classifications; child classificationids extend the 2-digit prefix.
CLASSIFICATIONS = [
    {"code": "01", "name_en": "Ride", "name_zh": "游乐设备"},
    {"code": "02", "name_en": "Show", "name_zh": "演出"},
    {"code": "03", "name_en": "Facility", "name_zh": "设施"},
    {"code": "04", "name_en": "Intrusion System", "name_zh": "入侵检测系统"},
    {"code": "05", "name_en": "Utilities", "name_zh": "公用工程"},
    {"code": "06", "name_en": "Operations", "name_zh": "运营"},
    {"code": "07", "name_en": "Guest", "name_zh": "游客"},
    {"code": "08", "name_en": "Other", "name_zh": "其他"},
    {"code": "09", "name_en": "Scheduled", "name_zh": "计划停机"},
    {"code": "10", "name_en": "Ride Vehicle", "name_zh": "乘载车辆"},
    {"code": "11", "name_en": "Investigation", "name_zh": "调查"},
    {"code": "12", "name_en": "Weather", "name_zh": "天气"},
    {"code": "13", "name_en": "Custodial", "name_zh": "保洁"},
]
CLASSIFICATION_CODES = frozenset(item["code"] for item in CLASSIFICATIONS)


def attraction_for_location(location: str | None) -> dict | None:
    """Resolve a work-order location to its longest matching catalog prefix."""
    if not location:
        return None
    matches = [
        item for code, item in ATTRACTION_BY_LOCATION.items()
        if location == code or location.startswith(code)
    ]
    return max(matches, key=lambda item: len(item["location"]), default=None)
