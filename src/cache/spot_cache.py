"""
热门城市景点缓存模块 —— Top 20 旅游城市的结构化景点数据。

字段与 LLM 检索输出（_normalize_spot）完全一致：
    name, level, address, area, duration, ticket_price,
    core_feature, tags, recommendation

查询接口：
    get_cached_spots(destination, tags) → list[dict] | None
    命中返回标准化景点列表，未命中返回 None（调用方走原有检索链）。

扩展方式：向 SPOT_CACHE 字典添加城市 key 即可，无需修改核心逻辑。
"""

from __future__ import annotations

import os

# ---- 城市别名映射 ----
CITY_ALIASES: dict[str, str] = {
    "蓉城": "成都", "锦城": "成都",
    "魔都": "上海", "申城": "上海",
    "帝都": "北京", "京城": "北京", "燕京": "北京",
    "羊城": "广州", "花城": "广州",
    "鹏城": "深圳",
    "江城": "武汉",
    "山城": "重庆", "渝州": "重庆",
    "金陵": "南京", "石头城": "南京",
    "长安": "西安",
    "星城": "长沙",
    "姑苏": "苏州",
    "绿城": "郑州",
    "春城": "昆明",
    "鹭岛": "厦门",
    "滨城": "大连",
    "冰城": "哈尔滨",
    "津门": "天津",
}

# ---- 热门城市景点缓存 ----
# 每个城市 10-15 个代表景点，字段与 _normalize_spot 输出完全一致。
# 数据来源：各城市文旅局官网、主流旅游平台公开信息。
SPOT_CACHE: dict[str, list[dict]] = {
    "北京": [
        {"name": "故宫博物院", "level": "5A", "address": "北京市东城区景山前街4号", "area": "东城区", "duration": 4.0, "ticket_price": 60.0, "core_feature": "世界五大宫之首，明清两代皇家宫殿", "tags": ["历史文化", "打卡", "博物馆", "人文"], "recommendation": "世界文化遗产，北京必游地标，建议提前预约"},
        {"name": "八达岭长城", "level": "5A", "address": "北京市延庆区G6京藏高速58号出口", "area": "延庆区", "duration": 4.0, "ticket_price": 40.0, "core_feature": "明长城最精华段，世界文化遗产", "tags": ["历史文化", "打卡", "探险", "自然风光"], "recommendation": "不到长城非好汉，建议早去避开人流高峰"},
        {"name": "颐和园", "level": "5A", "address": "北京市海淀区新建宫门路19号", "area": "海淀区", "duration": 3.0, "ticket_price": 30.0, "core_feature": "中国现存最大皇家园林，湖光山色交相辉映", "tags": ["自然风光", "历史文化", "休闲", "摄影"], "recommendation": "泛舟昆明湖，漫步长廊，感受皇家园林之美"},
        {"name": "天坛公园", "level": "5A", "address": "北京市东城区天坛路甲1号", "area": "东城区", "duration": 2.5, "ticket_price": 15.0, "core_feature": "明清皇帝祭天场所，祈年殿为北京地标", "tags": ["历史文化", "打卡", "休闲"], "recommendation": "祈年殿建筑精美，回音壁体验奇妙声学效果"},
        {"name": "天安门广场", "level": "5A", "address": "北京市东城区长安街", "area": "东城区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "世界最大城市广场，中国国家象征", "tags": ["打卡", "历史文化"], "recommendation": "看升旗仪式需凌晨排队，广场周边有人民大会堂和国家博物馆"},
        {"name": "鸟巢（国家体育场）", "level": "5A", "address": "北京市朝阳区国家体育场南路1号", "area": "朝阳区", "duration": 1.5, "ticket_price": 50.0, "core_feature": "2008年奥运会主体育场，现代建筑奇观", "tags": ["打卡", "摄影", "亲子"], "recommendation": "奥运会遗产，夜晚灯光效果极佳，适合拍照打卡"},
        {"name": "南锣鼓巷", "level": "3A", "address": "北京市东城区南锣鼓巷", "area": "东城区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "北京最古老胡同街区，文艺小店与老北京风情交融", "tags": ["文艺", "美食", "休闲", "打卡"], "recommendation": "逛胡同品小吃，感受老北京生活气息，免费游览"},
        {"name": "雍和宫", "level": "5A", "address": "北京市东城区雍和宫大街12号", "area": "东城区", "duration": 2.0, "ticket_price": 25.0, "core_feature": "北京最大的藏传佛教寺院，香火鼎盛", "tags": ["历史文化", "人文"], "recommendation": "清代皇家寺院，万福阁内18米高弥勒木雕堪称国宝"},
        {"name": "798艺术区", "level": "无", "address": "北京市朝阳区酒仙桥路4号", "area": "朝阳区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "老厂房改造的当代艺术聚集区，先锋艺术与工业遗存碰撞", "tags": ["文艺", "摄影", "打卡"], "recommendation": "前卫艺术展览与工业遗址完美融合，文艺青年必打卡"},
        {"name": "什刹海", "level": "4A", "address": "北京市西城区什刹海", "area": "西城区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "北京城内最完整的水域，酒吧街与胡同游并存", "tags": ["休闲", "夜生活", "美食", "打卡"], "recommendation": "夏可泛舟冬可溜冰，周边胡同酒吧氛围极佳"},
    ],
    "上海": [
        {"name": "外滩", "level": "无", "address": "上海市黄浦区中山东一路", "area": "黄浦区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "万国建筑博览群与浦东天际线隔江相望", "tags": ["打卡", "摄影", "夜生活", "休闲"], "recommendation": "上海城市名片，夜景璀璨夺目，免费游览"},
        {"name": "东方明珠塔", "level": "5A", "address": "上海市浦东新区世纪大道1号", "area": "浦东新区", "duration": 2.0, "ticket_price": 199.0, "core_feature": "上海标志性电视塔，城市高空观景地标", "tags": ["打卡", "摄影"], "recommendation": "259米全透明悬空观光廊体验云端漫步，俯瞰浦江两岸"},
        {"name": "上海迪士尼乐园", "level": "无", "address": "上海市浦东新区川沙镇黄赵路310号", "area": "浦东新区", "duration": 8.0, "ticket_price": 475.0, "core_feature": "中国大陆首座迪士尼主题乐园，梦幻童话世界", "tags": ["亲子", "打卡", "休闲"], "recommendation": "适合全年龄段，建议玩一整天，晚上烟花秀不可错过"},
        {"name": "豫园", "level": "4A", "address": "上海市黄浦区福佑路168号", "area": "黄浦区", "duration": 2.0, "ticket_price": 40.0, "core_feature": "明代江南私家园林，闹市中的古典园林精品", "tags": ["历史文化", "休闲", "摄影"], "recommendation": "精致江南园林，毗邻城隍庙美食街，品小笼包赏园林"},
        {"name": "上海科技馆", "level": "5A", "address": "上海市浦东新区世纪大道2000号", "area": "浦东新区", "duration": 3.0, "ticket_price": 45.0, "core_feature": "互动式科普教育基地，中国最大科技馆之一", "tags": ["亲子", "博物馆", "打卡"], "recommendation": "动手体验科学奥秘，特别适合带小朋友的家庭"},
        {"name": "南京路步行街", "level": "无", "address": "上海市黄浦区南京东路", "area": "黄浦区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "中华商业第一街，百年商脉汇聚", "tags": ["购物", "打卡", "美食"], "recommendation": "从外滩一路逛到人民广场，老字号与时尚品牌汇集"},
        {"name": "田子坊", "level": "3A", "address": "上海市黄浦区泰康路210弄", "area": "黄浦区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "石库门里弄改造的创意街区，弄堂艺术与市井生活的融合", "tags": ["文艺", "购物", "摄影", "美食"], "recommendation": "老上海弄堂里的文艺小店，找一家咖啡馆感受慢时光"},
        {"name": "上海博物馆", "level": "4A", "address": "上海市黄浦区人民大道201号", "area": "黄浦区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "中国古代艺术顶级殿堂，青铜器、陶瓷、书画收藏丰富", "tags": ["博物馆", "历史文化", "人文"], "recommendation": "免费参观，馆藏青铜器与陶瓷为国内顶级水平"},
        {"name": "新天地", "level": "无", "address": "上海市黄浦区太仓路181弄", "area": "黄浦区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "石库门建筑群改造的高端休闲街区，中西文化交融", "tags": ["美食", "夜生活", "购物", "休闲"], "recommendation": "中共一大会址所在地，高端餐饮与夜生活聚集地"},
        {"name": "朱家角古镇", "level": "4A", "address": "上海市青浦区朱家角镇", "area": "青浦区", "duration": 4.0, "ticket_price": 0.0, "core_feature": "上海保存最完好的江南水乡古镇，小桥流水人家", "tags": ["历史文化", "休闲", "摄影", "打卡"], "recommendation": "距市区约1小时车程，放生桥与漕港河两岸景色最美"},
    ],
    "广州": [
        {"name": "广州塔（小蛮腰）", "level": "4A", "address": "广州市海珠区阅江西路222号", "area": "海珠区", "duration": 2.0, "ticket_price": 150.0, "core_feature": "中国第一高塔，600米高空俯瞰羊城", "tags": ["打卡", "摄影", "夜生活"], "recommendation": "广州地标，450米观景台可360°俯瞰城市，夜景最佳"},
        {"name": "长隆野生动物世界", "level": "5A", "address": "广州市番禺区大石镇105国道", "area": "番禺区", "duration": 6.0, "ticket_price": 300.0, "core_feature": "亚洲最大野生动物主题公园，500余种珍稀动物", "tags": ["亲子", "打卡", "自然风光"], "recommendation": "可自驾车穿越猛兽区，大熊猫三胞胎是全球唯一存活"},
        {"name": "白云山", "level": "5A", "address": "广州市白云区白云大道南", "area": "白云区", "duration": 4.0, "ticket_price": 5.0, "core_feature": "南粤名山，羊城第一秀，城市绿肺", "tags": ["自然风光", "休闲", "探险", "摄影"], "recommendation": "登摩星岭俯瞰广州全景，山间空气清新适合徒步"},
        {"name": "陈家祠", "level": "4A", "address": "广州市荔湾区中山七路恩龙里34号", "area": "荔湾区", "duration": 2.0, "ticket_price": 10.0, "core_feature": "岭南建筑艺术明珠，集广东民间工艺之大成", "tags": ["历史文化", "人文", "摄影", "博物馆"], "recommendation": "砖雕、木雕、石雕三绝，岭南建筑艺术的巅峰之作"},
        {"name": "沙面岛", "level": "无", "address": "广州市荔湾区沙面岛", "area": "荔湾区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "欧陆风情建筑群，19世纪租界历史的活化石", "tags": ["摄影", "打卡", "休闲", "文艺"], "recommendation": "百座欧式建筑散布小岛，随手拍都是大片，免费游览"},
        {"name": "越秀公园", "level": "4A", "address": "广州市越秀区解放北路960号", "area": "越秀区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "广州最大的综合性公园，五羊石像所在", "tags": ["休闲", "历史文化", "打卡"], "recommendation": "五羊石像是广州标志，镇海楼内有广州博物馆"},
        {"name": "上下九步行街", "level": "无", "address": "广州市荔湾区上下九路", "area": "荔湾区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "百年商业老街，岭南骑楼建筑与地道美食汇聚", "tags": ["购物", "美食", "打卡", "休闲"], "recommendation": "尝地道广式点心（陶陶居/莲香楼），逛岭南特色骑楼"},
        {"name": "中山纪念堂", "level": "4A", "address": "广州市越秀区东风中路299号", "area": "越秀区", "duration": 1.5, "ticket_price": 10.0, "core_feature": "纪念孙中山先生的八角形宫殿式建筑，无一根立柱", "tags": ["历史文化", "人文", "打卡"], "recommendation": "建筑奇迹——内部无柱，穹顶跨度达71米，音响效果极佳"},
        {"name": "广东省博物馆", "level": "4A", "address": "广州市天河区珠江东路2号", "area": "天河区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "岭南历史文化宝库，外观如月光宝盒", "tags": ["博物馆", "历史文化", "亲子"], "recommendation": "免费参观，端砚艺术与潮州木雕展厅最具岭南特色"},
        {"name": "珠江夜游", "level": "无", "address": "广州市越秀区沿江路天字码头", "area": "越秀区", "duration": 1.5, "ticket_price": 68.0, "core_feature": "乘船游览珠江两岸夜景，广州塔至海心沙灯光璀璨", "tags": ["夜生活", "摄影", "打卡", "休闲"], "recommendation": "夜晚广州最美打开方式，珠江两岸灯光秀令人沉醉"},
    ],
    "深圳": [
        {"name": "世界之窗", "level": "5A", "address": "深圳市南山区深南大道9037号", "area": "南山区", "duration": 5.0, "ticket_price": 220.0, "core_feature": "世界名胜微缩主题公园，一天环游世界", "tags": ["打卡", "亲子", "摄影"], "recommendation": "汇集全球130多处名胜微缩景观，晚上有大型歌舞表演"},
        {"name": "深圳欢乐谷", "level": "5A", "address": "深圳市南山区侨城西街18号", "area": "南山区", "duration": 5.0, "ticket_price": 230.0, "core_feature": "大型现代主题乐园，九大主题区适合全年龄段", "tags": ["亲子", "打卡", "探险"], "recommendation": "玛雅水上乐园夏季必玩，雪山飞龙过山车惊险刺激"},
        {"name": "大梅沙海滨公园", "level": "无", "address": "深圳市盐田区盐梅路", "area": "盐田区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "深圳最长海滩，免费开放的滨海休闲胜地", "tags": ["自然风光", "休闲", "亲子"], "recommendation": "免费沙滩，夏天游泳戏水首选，周边有东部华侨城"},
        {"name": "莲花山公园", "level": "4A", "address": "深圳市福田区红荔路6030号", "area": "福田区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "市中心城市公园，山顶邓小平铜像俯瞰福田CBD", "tags": ["休闲", "打卡", "摄影"], "recommendation": "山顶广场是拍摄深圳中轴线的最佳机位，免费游览"},
        {"name": "深圳湾公园", "level": "无", "address": "深圳市南山区滨海大道", "area": "南山区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "15公里滨海长廊，隔海眺望香港元朗", "tags": ["休闲", "摄影", "自然风光", "亲子"], "recommendation": "沿海骑行/散步，傍晚看日落和跨海大桥很美"},
        {"name": "东部华侨城", "level": "5A", "address": "深圳市盐田区大梅沙东部华侨城", "area": "盐田区", "duration": 6.0, "ticket_price": 200.0, "core_feature": "大型生态旅游度假区，茶溪谷与大侠谷各具特色", "tags": ["自然风光", "亲子", "打卡", "休闲"], "recommendation": "茵特拉根小镇欧式风情浓郁，云海谷高尔夫球场环境顶级"},
        {"name": "锦绣中华民俗村", "level": "5A", "address": "深圳市南山区深南大道9003号", "area": "南山区", "duration": 4.0, "ticket_price": 220.0, "core_feature": "中国名胜微缩景观与56个民族文化展示", "tags": ["历史文化", "亲子", "打卡"], "recommendation": "一日看尽中华美景，民族歌舞表演极具观赏性"},
        {"name": "华侨城创意文化园", "level": "无", "address": "深圳市南山区锦绣北街2号", "area": "南山区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "旧工业区改造的LOFT创意园区，深圳文艺地标", "tags": ["文艺", "摄影", "美食", "休闲"], "recommendation": "每月举办创意市集，独立咖啡馆和设计师店铺林立"},
        {"name": "中英街", "level": "4A", "address": "深圳市盐田区沙头角镇中英街", "area": "盐田区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "一国两制边界线，深港交界的特殊历史街区", "tags": ["购物", "历史文化", "打卡"], "recommendation": "需办理通行证进入，免税购物与历史探秘并存"},
        {"name": "深圳博物馆", "level": "4A", "address": "深圳市福田区福中路市民中心A区", "area": "福田区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "展示深圳改革开放历程的综合性博物馆", "tags": ["博物馆", "历史文化", "亲子"], "recommendation": "了解深圳从小渔村到国际都市的奇迹蜕变，免费参观"},
    ],
    "成都": [
        {"name": "大熊猫繁育研究基地", "level": "4A", "address": "成都市成华区熊猫大道1375号", "area": "成华区", "duration": 3.0, "ticket_price": 55.0, "core_feature": "全球最大的大熊猫人工繁育机构，近距离观看国宝", "tags": ["亲子", "打卡", "自然"], "recommendation": "全球最大的大熊猫人工繁育机构，可近距离观看国宝大熊猫，亲子游首选"},
        {"name": "宽窄巷子", "level": "4A", "address": "成都市青羊区长顺街", "area": "青羊区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "成都最具代表性的历史文化街区，川西民居与潮流的融合", "tags": ["打卡", "美食", "人文", "休闲"], "recommendation": "成都最具代表性的历史文化街区，汇集地道小吃与川西民居建筑，免费游览"},
        {"name": "锦里古街", "level": "无", "address": "成都市武侯区武侯祠大街231号", "area": "武侯区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "紧邻武侯祠的仿古商业街，三国文化与四川民俗交融", "tags": ["美食", "人文", "打卡", "休闲"], "recommendation": "紧邻武侯祠的仿古商业街，夜游氛围绝佳，集中品尝三大炮、糖油果子等地道小吃"},
        {"name": "武侯祠", "level": "4A", "address": "成都市武侯区武侯祠大街231号", "area": "武侯区", "duration": 2.0, "ticket_price": 50.0, "core_feature": "中国唯一君臣合祀祠庙，三国文化圣地", "tags": ["人文", "打卡", "历史文化"], "recommendation": "中国唯一君臣合祀祠庙，三国文化圣地，红墙竹林极具出片感"},
        {"name": "都江堰景区", "level": "5A", "address": "成都市都江堰市公园路", "area": "都江堰市", "duration": 4.0, "ticket_price": 80.0, "core_feature": "世界文化遗产，两千余年至今仍在运转的水利工程奇迹", "tags": ["打卡", "人文", "自然"], "recommendation": "世界文化遗产，两千余年至今仍在运转的水利工程奇迹，山水壮阔"},
        {"name": "杜甫草堂", "level": "4A", "address": "成都市青羊区青华路37号", "area": "青羊区", "duration": 2.0, "ticket_price": 50.0, "core_feature": "诗圣杜甫流寓成都时的故居，清幽雅致的文人园林", "tags": ["人文", "休闲", "历史文化"], "recommendation": "诗圣杜甫流寓成都时的故居，园林清幽雅致，适合静心漫步感受诗歌文化"},
        {"name": "春熙路-太古里商圈", "level": "无", "address": "成都市锦江区中纱帽街8号", "area": "锦江区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "成都最繁华的商业中心，IFS大熊猫爬楼网红地标", "tags": ["打卡", "美食", "休闲", "购物"], "recommendation": "成都最繁华的商业中心，大熊猫爬楼网红打卡点，IFS+太古里潮流地标"},
        {"name": "青城山风景区", "level": "5A", "address": "成都市都江堰市青城山镇", "area": "都江堰市", "duration": 5.0, "ticket_price": 80.0, "core_feature": "道教发源地之一，前山问道后山观景，绿荫蔽日天然氧吧", "tags": ["自然", "休闲", "打卡", "探险"], "recommendation": "道教发源地之一，前山问道后山观景，绿荫蔽日天然氧吧，适合户外徒步"},
        {"name": "人民公园", "level": "4A", "address": "成都市青羊区少城路12号", "area": "青羊区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "体验老成都市井生活的最佳去处，鹤鸣茶社喝盖碗茶", "tags": ["休闲", "人文"], "recommendation": "体验老成都市井生活的最佳去处，鹤鸣茶社喝盖碗茶、掏耳朵，悠然半日闲"},
        {"name": "金沙遗址博物馆", "level": "4A", "address": "成都市青羊区金沙遗址路2号", "area": "青羊区", "duration": 2.5, "ticket_price": 70.0, "core_feature": "古蜀文明遗址，太阳神鸟金饰出土地", "tags": ["人文", "亲子", "博物馆", "历史文化"], "recommendation": "太阳神鸟金饰是中国文化遗产标志原型，了解神秘古蜀文明的最佳窗口"},
    ],
    "杭州": [
        {"name": "西湖风景区", "level": "5A", "address": "杭州市西湖区西湖", "area": "西湖区", "duration": 5.0, "ticket_price": 0.0, "core_feature": "世界文化遗产，中国最美城市湖泊，十景名扬天下", "tags": ["自然风光", "打卡", "摄影", "休闲"], "recommendation": "免费游览，苏堤春晓、断桥残雪、三潭印月等十景各具韵味"},
        {"name": "灵隐寺", "level": "5A", "address": "杭州市西湖区灵隐路法云弄1号", "area": "西湖区", "duration": 2.5, "ticket_price": 75.0, "core_feature": "中国十大名刹之一，千年古刹隐于飞来峰下", "tags": ["历史文化", "人文", "打卡"], "recommendation": "千年古刹香火鼎盛，飞来峰摩崖石刻为江南石窟艺术瑰宝"},
        {"name": "西溪国家湿地公园", "level": "5A", "address": "杭州市西湖区天目山路518号", "area": "西湖区", "duration": 4.0, "ticket_price": 80.0, "core_feature": "中国首个国家湿地公园，城市中的自然绿洲", "tags": ["自然风光", "休闲", "摄影", "亲子"], "recommendation": "乘摇橹船穿行芦苇荡，感受江南水乡的宁静之美"},
        {"name": "雷峰塔", "level": "4A", "address": "杭州市西湖区南山路15号", "area": "西湖区", "duration": 1.5, "ticket_price": 40.0, "core_feature": "白蛇传传说发生地，西湖十景之雷峰夕照", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "登塔俯瞰西湖全景，白蛇传传说为这里增添了浪漫色彩"},
        {"name": "河坊街", "level": "无", "address": "杭州市上城区河坊街", "area": "上城区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "南宋御街遗址所在，杭州最热闹的历史文化街区", "tags": ["美食", "购物", "打卡", "历史文化"], "recommendation": "尝定胜糕、葱包烩等地道杭州小吃，购买丝绸和龙井茶"},
        {"name": "宋城景区", "level": "4A", "address": "杭州市西湖区之江路148号", "area": "西湖区", "duration": 4.0, "ticket_price": 320.0, "core_feature": "大型宋代文化主题公园，宋城千古情演出震撼", "tags": ["打卡", "亲子", "历史文化"], "recommendation": "给我一天还你千年，宋城千古情演出被誉为世界三大名秀之一"},
        {"name": "龙井村", "level": "无", "address": "杭州市西湖区龙井路龙井村", "area": "西湖区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "西湖龙井茶核心产区，梯田茶园风光无限", "tags": ["休闲", "摄影", "美食", "自然风光"], "recommendation": "春季采茶季最佳，在茶农家品一杯正宗狮峰龙井"},
        {"name": "京杭大运河（杭州段）", "level": "无", "address": "杭州市拱墅区运河文化广场", "area": "拱墅区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "世界文化遗产，千年运河的江南起点", "tags": ["历史文化", "休闲", "打卡"], "recommendation": "乘坐漕舫船游览运河，两岸历史文化街区值得漫步"},
        {"name": "钱塘江大桥", "level": "无", "address": "杭州市滨江区钱塘江大桥", "area": "滨江区", "duration": 1.0, "ticket_price": 0.0, "core_feature": "中国自行设计建造的第一座双层铁路公路两用桥", "tags": ["打卡", "摄影"], "recommendation": "茅以升设计的桥梁杰作，六和塔旁是最佳拍摄机位"},
        {"name": "千岛湖风景区", "level": "5A", "address": "杭州市淳安县千岛湖镇", "area": "淳安县", "duration": 6.0, "ticket_price": 195.0, "core_feature": "1078座岛屿星罗棋布，天下第一秀水", "tags": ["自然风光", "休闲", "摄影", "探险"], "recommendation": "距市区约2小时车程，乘船登岛游览，湖光山色绝美"},
    ],
    "重庆": [
        {"name": "洪崖洞", "level": "4A", "address": "重庆市渝中区嘉陵江滨江路88号", "area": "渝中区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "吊脚楼建筑群依山而建，现实版千与千寻", "tags": ["打卡", "摄影", "美食", "夜生活"], "recommendation": "重庆第一网红地标，夜晚亮灯后如同动漫场景"},
        {"name": "解放碑", "level": "无", "address": "重庆市渝中区民族路177号", "area": "渝中区", "duration": 1.0, "ticket_price": 0.0, "core_feature": "重庆城市精神地标，抗战胜利纪功碑", "tags": ["打卡", "购物", "美食"], "recommendation": "重庆CBD核心，周边八一好吃街汇集重庆各色小吃"},
        {"name": "磁器口古镇", "level": "4A", "address": "重庆市沙坪坝区磁南街1号", "area": "沙坪坝区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "千年古镇，巴渝文化缩影，陈麻花闻名全国", "tags": ["历史文化", "美食", "打卡", "休闲"], "recommendation": "尝陈麻花、毛血旺，逛明清古建筑，感受老重庆码头文化"},
        {"name": "大足石刻", "level": "5A", "address": "重庆市大足区宝顶镇", "area": "大足区", "duration": 3.0, "ticket_price": 135.0, "core_feature": "世界文化遗产，中国晚期石窟艺术的巅峰之作", "tags": ["历史文化", "人文", "打卡"], "recommendation": "世界文化遗产，唐宋石刻造像精美绝伦，佛教艺术瑰宝"},
        {"name": "武隆天生三桥", "level": "5A", "address": "重庆市武隆区仙女山镇", "area": "武隆区", "duration": 4.0, "ticket_price": 135.0, "core_feature": "世界自然遗产，亚洲最大天生桥群", "tags": ["自然风光", "探险", "摄影", "打卡"], "recommendation": "变形金刚4取景地，天龙桥、青龙桥、黑龙桥气势恢宏"},
        {"name": "长江索道", "level": "4A", "address": "重庆市渝中区新华路151号", "area": "渝中区", "duration": 0.5, "ticket_price": 20.0, "core_feature": "万里长江第一条空中走廊，重庆独特交通体验", "tags": ["打卡", "摄影"], "recommendation": "从空中横跨长江，山城立体交通的魔幻体验"},
        {"name": "南山一棵树观景台", "level": "无", "address": "重庆市南岸区龙黄公路", "area": "南岸区", "duration": 1.5, "ticket_price": 30.0, "core_feature": "俯瞰重庆夜景的最佳机位，渝中半岛灯火辉煌", "tags": ["摄影", "夜生活", "打卡"], "recommendation": "重庆夜景名片，渝中半岛的万家灯火尽收眼底"},
        {"name": "李子坝轻轨站", "level": "无", "address": "重庆市渝中区李子坝正街", "area": "渝中区", "duration": 0.5, "ticket_price": 0.0, "core_feature": "轻轨穿楼而过的魔幻奇观，重庆8D城市代表", "tags": ["打卡", "摄影"], "recommendation": "轻轨穿楼的独特景观，重庆魔幻交通最佳代表"},
        {"name": "鹅岭贰厂文创园", "level": "无", "address": "重庆市渝中区鹅岭正街1号", "area": "渝中区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "民国印钞厂改造的文创园区，从你的全世界路过取景地", "tags": ["文艺", "摄影", "打卡", "休闲"], "recommendation": "电影取景地，天台可俯瞰两江四岸风光，文艺小店密集"},
        {"name": "仙女山国家森林公园", "level": "5A", "address": "重庆市武隆区仙女山镇", "area": "武隆区", "duration": 4.0, "ticket_price": 60.0, "core_feature": "南国罕见的林海雪原，高山草原风光", "tags": ["自然风光", "休闲", "亲子", "摄影"], "recommendation": "夏季避暑冬季滑雪，高山草原被誉为东方瑞士"},
    ],
    "武汉": [
        {"name": "黄鹤楼", "level": "5A", "address": "武汉市武昌区蛇山西山坡特1号", "area": "武昌区", "duration": 2.0, "ticket_price": 70.0, "core_feature": "天下江山第一楼，崔颢李白留下千古绝唱", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "登楼眺望长江大桥与汉阳风光，武汉第一文化地标"},
        {"name": "东湖风景区", "level": "5A", "address": "武汉市武昌区东湖路特1号", "area": "武昌区", "duration": 4.0, "ticket_price": 0.0, "core_feature": "中国最大城中湖，听涛磨山落雁三大景区各具魅力", "tags": ["自然风光", "休闲", "摄影", "亲子"], "recommendation": "比西湖大六倍，骑行东湖绿道是最佳游览方式"},
        {"name": "户部巷", "level": "无", "address": "武汉市武昌区自由路", "area": "武昌区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "汉味小吃第一巷，武汉过早文化集中地", "tags": ["美食", "打卡", "休闲"], "recommendation": "热干面、豆皮、面窝、糊汤粉一站式吃遍武汉早点"},
        {"name": "武汉大学", "level": "无", "address": "武汉市武昌区珞珈山路16号", "area": "武昌区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "中国最美大学之一，樱花季全国闻名", "tags": ["摄影", "打卡", "文艺", "自然风光"], "recommendation": "三月樱花盛开时最美，老斋舍和樱花大道是经典机位"},
        {"name": "湖北省博物馆", "level": "4A", "address": "武汉市武昌区东湖路160号", "area": "武昌区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "曾侯乙编钟与越王勾践剑出土地，荆楚文化殿堂", "tags": ["博物馆", "历史文化", "亲子", "人文"], "recommendation": "曾侯乙编钟和越王勾践剑是镇馆之宝，免费参观"},
        {"name": "汉口江滩", "level": "无", "address": "武汉市江岸区沿江大道", "area": "江岸区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "亚洲最大滨江公园，长江两岸风光尽收眼底", "tags": ["休闲", "摄影", "自然风光"], "recommendation": "傍晚散步看长江日落，对岸武昌天际线很美"},
        {"name": "归元禅寺", "level": "4A", "address": "武汉市汉阳区归元寺路20号", "area": "汉阳区", "duration": 1.5, "ticket_price": 10.0, "core_feature": "武汉香火最旺的佛教寺院，五百罗汉堂独特", "tags": ["历史文化", "人文"], "recommendation": "数罗汉是武汉人的传统习俗，新年祈福圣地"},
        {"name": "武汉长江大桥", "level": "无", "address": "武汉市武昌区临江大道", "area": "武昌区", "duration": 1.0, "ticket_price": 0.0, "core_feature": "万里长江第一桥，中苏友谊的象征", "tags": ["打卡", "摄影", "历史文化"], "recommendation": "步行过桥是经典的武汉体验，桥上可以看到黄鹤楼全景"},
        {"name": "楚河汉街", "level": "无", "address": "武汉市武昌区楚河汉街", "area": "武昌区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "现代商业步行街与楚文化元素的融合", "tags": ["购物", "美食", "夜生活", "打卡"], "recommendation": "武汉潮流地标，汉秀剧场是世界顶级水秀表演"},
        {"name": "木兰天池", "level": "5A", "address": "武汉市黄陂区木兰山", "area": "黄陂区", "duration": 5.0, "ticket_price": 80.0, "core_feature": "木兰故里，峡谷瀑布与高山草原并存的山水画卷", "tags": ["自然风光", "探险", "休闲", "亲子"], "recommendation": "距市区约1.5小时车程，春季杜鹃花开满山红遍"},
    ],
    "西安": [
        {"name": "兵马俑博物馆", "level": "5A", "address": "西安市临潼区秦陵北路", "area": "临潼区", "duration": 3.0, "ticket_price": 120.0, "core_feature": "世界第八大奇迹，秦始皇陵陪葬陶俑军阵", "tags": ["历史文化", "博物馆", "打卡", "人文"], "recommendation": "世界文化遗产，千人千面的陶俑令人震撼，必看一号坑"},
        {"name": "大雁塔", "level": "5A", "address": "西安市雁塔区大雁塔南广场", "area": "雁塔区", "duration": 2.0, "ticket_price": 40.0, "core_feature": "唐代玄奘法师藏经塔，西安城市标志", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "北广场音乐喷泉亚洲最大，夜晚灯光效果震撼"},
        {"name": "西安城墙", "level": "5A", "address": "西安市碑林区南大街", "area": "碑林区", "duration": 3.0, "ticket_price": 54.0, "core_feature": "中国保存最完整的古代城垣，可骑行环绕一周", "tags": ["历史文化", "打卡", "摄影", "探险"], "recommendation": "租自行车环城墙一周约2小时，俯瞰古城内外风景"},
        {"name": "回民街", "level": "无", "address": "西安市莲湖区回民街", "area": "莲湖区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "西安最著名的小吃街，清真美食的天堂", "tags": ["美食", "打卡", "休闲", "夜生活"], "recommendation": "羊肉泡馍、肉夹馍、biangbiang面、凉皮一站式吃遍西安"},
        {"name": "华清宫", "level": "5A", "address": "西安市临潼区华清路38号", "area": "临潼区", "duration": 2.5, "ticket_price": 120.0, "core_feature": "唐代皇家温泉行宫，长恨歌故事发生地", "tags": ["历史文化", "打卡", "人文"], "recommendation": "晚上《长恨歌》实景演出震撼，建议与兵马俑同一天游览"},
        {"name": "陕西历史博物馆", "level": "4A", "address": "西安市雁塔区小寨东路91号", "area": "雁塔区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "给我一天还你万年，周秦汉唐文物精华所在", "tags": ["博物馆", "历史文化", "人文"], "recommendation": "免费但需提前预约，馆藏唐代金银器和壁画举世闻名"},
        {"name": "大唐不夜城", "level": "无", "address": "西安市雁塔区雁塔南路", "area": "雁塔区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "盛唐文化主题步行街，不倒翁小姐姐走红网络", "tags": ["夜生活", "打卡", "美食", "摄影"], "recommendation": "夜晚灯光璀璨如梦回大唐，各类唐文化表演轮番上演"},
        {"name": "钟鼓楼", "level": "无", "address": "西安市碑林区东西南北四条大街交汇处", "area": "碑林区", "duration": 1.5, "ticket_price": 30.0, "core_feature": "西安城市中心地标，钟楼与鼓楼遥相呼应", "tags": ["打卡", "历史文化", "摄影"], "recommendation": "登楼俯瞰西安中轴线，晨钟暮鼓的传统在此延续"},
        {"name": "碑林博物馆", "level": "5A", "address": "西安市碑林区三学街15号", "area": "碑林区", "duration": 2.0, "ticket_price": 65.0, "core_feature": "中国最大的书法艺术宝库，历代名家碑刻荟萃", "tags": ["博物馆", "历史文化", "人文"], "recommendation": "书法爱好者的圣地，颜真卿柳公权等名家碑刻真迹"},
        {"name": "华山", "level": "5A", "address": "陕西省华阴市华山镇", "area": "华阴市", "duration": 8.0, "ticket_price": 160.0, "core_feature": "五岳之西岳，奇险天下第一山", "tags": ["自然风光", "探险", "摄影", "打卡"], "recommendation": "距西安约1.5小时高铁，长空栈道惊险刺激，建议安排一整天"},
    ],
    "南京": [
        {"name": "中山陵", "level": "5A", "address": "南京市玄武区石象路7号", "area": "玄武区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "孙中山先生陵寝，中国近代建筑史上第一陵", "tags": ["历史文化", "打卡", "人文"], "recommendation": "392级台阶象征当时3亿9千2百万同胞，免费参观需预约"},
        {"name": "夫子庙-秦淮河风光带", "level": "5A", "address": "南京市秦淮区贡院西街53号", "area": "秦淮区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "十里秦淮，六朝金粉，南京最具代表性的历史文化街区", "tags": ["历史文化", "美食", "夜生活", "打卡"], "recommendation": "夜游秦淮河最有意境，江南贡院和科举博物馆值得一看"},
        {"name": "总统府", "level": "4A", "address": "南京市玄武区长江路292号", "area": "玄武区", "duration": 2.0, "ticket_price": 40.0, "core_feature": "中国近代史重要遗址，从两江总督到民国总统府", "tags": ["历史文化", "人文", "打卡"], "recommendation": "一座总统府半部近代史，从明清到民国的建筑群极富层次感"},
        {"name": "南京博物院", "level": "4A", "address": "南京市玄武区中山东路321号", "area": "玄武区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "中国三大博物馆之一，前身为国立中央博物院", "tags": ["博物馆", "历史文化", "亲子", "人文"], "recommendation": "民国馆沉浸式体验最受欢迎，免费但需提前预约"},
        {"name": "明孝陵", "level": "5A", "address": "南京市玄武区石象路7号", "area": "玄武区", "duration": 2.5, "ticket_price": 70.0, "core_feature": "明太祖朱元璋陵寝，明清皇陵之冠", "tags": ["历史文化", "摄影", "打卡"], "recommendation": "世界文化遗产，神道石像生气势恢宏，秋季银杏最美"},
        {"name": "南京城墙（中华门）", "level": "5A", "address": "南京市秦淮区中华路", "area": "秦淮区", "duration": 1.5, "ticket_price": 50.0, "core_feature": "世界最长古城墙，中华门瓮城结构独一无二", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "中华门瓮城内有藏兵洞27个，是世界上保存最完好的古城墙之一"},
        {"name": "玄武湖公园", "level": "4A", "address": "南京市玄武区玄武巷1号", "area": "玄武区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "中国最大皇家园林湖泊，金陵明珠", "tags": ["自然风光", "休闲", "摄影", "亲子"], "recommendation": "免费游览，泛舟湖上看紫金山天际线，四季花海不断"},
        {"name": "老门东", "level": "无", "address": "南京市秦淮区箍桶巷", "area": "秦淮区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "南京老城南传统民居风貌区，文艺小店与古建筑共存", "tags": ["美食", "文艺", "打卡", "休闲"], "recommendation": "比夫子庙更清静的老街区，德云社南京分社也在这里"},
        {"name": "鸡鸣寺", "level": "无", "address": "南京市玄武区鸡鸣寺路1号", "area": "玄武区", "duration": 1.5, "ticket_price": 10.0, "core_feature": "南朝四百八十寺之首，春季樱花大道美不胜收", "tags": ["历史文化", "摄影", "打卡"], "recommendation": "春季樱花大道是南京最美赏樱地，素面尤其好吃"},
        {"name": "牛首山文化旅游区", "level": "4A", "address": "南京市江宁区宁丹大道18号", "area": "江宁区", "duration": 3.0, "ticket_price": 98.0, "core_feature": "佛教牛头禅宗发源地，佛顶宫供奉释迦牟尼佛顶骨舍利", "tags": ["历史文化", "打卡", "摄影", "人文"], "recommendation": "佛顶宫建筑恢宏，地宫供奉着世间唯一的佛顶骨舍利"},
    ],
    "苏州": [
        {"name": "拙政园", "level": "5A", "address": "苏州市姑苏区东北街178号", "area": "姑苏区", "duration": 2.0, "ticket_price": 80.0, "core_feature": "中国四大名园之首，江南私家园林的代表之作", "tags": ["历史文化", "摄影", "打卡", "休闲"], "recommendation": "世界文化遗产，借景手法精妙绝伦，四季景致各异"},
        {"name": "平江路", "level": "无", "address": "苏州市姑苏区平江路", "area": "姑苏区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "苏州保存最完好的古街，小桥流水人家的真实写照", "tags": ["文艺", "摄影", "美食", "休闲"], "recommendation": "沿河漫步听评弹，找一家茶馆感受最苏州的慢生活"},
        {"name": "虎丘", "level": "5A", "address": "苏州市姑苏区虎丘山门内8号", "area": "姑苏区", "duration": 2.5, "ticket_price": 70.0, "core_feature": "吴中第一名胜，云岩寺塔为中国的比萨斜塔", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "苏东坡说'到苏州不游虎丘乃憾事也'，剑池和虎丘塔最值得看"},
        {"name": "周庄古镇", "level": "5A", "address": "苏州市昆山市周庄镇", "area": "昆山市", "duration": 4.0, "ticket_price": 100.0, "core_feature": "中国第一水乡，沈厅张厅见证江南富商生活", "tags": ["历史文化", "摄影", "打卡", "休闲"], "recommendation": "陈逸飞画笔下的双桥让周庄闻名世界，乘船游古镇最具意境"},
        {"name": "金鸡湖景区", "level": "5A", "address": "苏州市工业园区金鸡湖", "area": "工业园区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "苏州现代城市客厅，东方之门与音乐喷泉交相辉映", "tags": ["休闲", "摄影", "购物", "夜生活"], "recommendation": "苏州中心商场和诚品书店是文化消费地标，夜景非常出片"},
        {"name": "留园", "level": "5A", "address": "苏州市姑苏区留园路338号", "area": "姑苏区", "duration": 1.5, "ticket_price": 55.0, "core_feature": "中国四大名园之一，空间层次感登峰造极", "tags": ["历史文化", "摄影", "休闲"], "recommendation": "移步换景的极致，园内冠云峰是太湖石中的极品"},
        {"name": "寒山寺", "level": "4A", "address": "苏州市姑苏区寒山寺弄24号", "area": "姑苏区", "duration": 1.5, "ticket_price": 20.0, "core_feature": "姑苏城外寒山寺，夜半钟声到客船的诗意所在", "tags": ["历史文化", "人文", "打卡"], "recommendation": "张继《枫桥夜泊》让这里千古流传，新年听钟声是传统"},
        {"name": "同里古镇", "level": "5A", "address": "苏州市吴江区同里镇", "area": "吴江区", "duration": 4.0, "ticket_price": 100.0, "core_feature": "醇正水乡旧时江南，退思园为世界文化遗产", "tags": ["历史文化", "摄影", "休闲", "打卡"], "recommendation": "比周庄更原生态，退思园的水上园林布局独一无二"},
        {"name": "苏州博物馆", "level": "4A", "address": "苏州市姑苏区东北街204号", "area": "姑苏区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "贝聿铭大师封山之作，本身就是一件建筑艺术品", "tags": ["博物馆", "摄影", "打卡", "文艺"], "recommendation": "建筑比藏品更值得看，片石假山水墨画般的意境"},
        {"name": "狮子林", "level": "4A", "address": "苏州市姑苏区园林路23号", "area": "姑苏区", "duration": 1.5, "ticket_price": 40.0, "core_feature": "假山王国，乾隆六次游览的太湖石迷宫", "tags": ["历史文化", "亲子", "摄影"], "recommendation": "迷宫般的假山群是孩子们的乐园，乾隆御笔真趣二字犹在"},
    ],
    "长沙": [
        {"name": "岳麓山", "level": "5A", "address": "长沙市岳麓区登高路58号", "area": "岳麓区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "南岳七十二峰之尾，千年学府岳麓书院坐落山脚", "tags": ["自然风光", "历史文化", "打卡", "休闲"], "recommendation": "爱晚亭秋色最美，岳麓书院千年文脉传承"},
        {"name": "橘子洲", "level": "5A", "address": "长沙市岳麓区橘子洲头", "area": "岳麓区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "中国第一洲，毛泽东青年雕像巍然矗立", "tags": ["打卡", "摄影", "休闲", "自然风光"], "recommendation": "毛泽东青年雕塑是长沙地标，烟花表演时最美"},
        {"name": "太平街", "level": "无", "address": "长沙市天心区太平街", "area": "天心区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "长沙最完整的老街，清末民初建筑与网红美食并存", "tags": ["美食", "打卡", "夜生活"], "recommendation": "茶颜悦色、黑色经典臭豆腐等网红店密集，逛吃首选"},
        {"name": "湖南省博物馆", "level": "4A", "address": "长沙市开福区东风路50号", "area": "开福区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "马王堆汉墓出土文物，辛追夫人千年不腐震惊世界", "tags": ["博物馆", "历史文化", "人文"], "recommendation": "马王堆女尸与素纱禅衣是必看国宝，免费但需预约"},
        {"name": "坡子街", "level": "无", "address": "长沙市天心区坡子街", "area": "天心区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "长沙美食集中地，火宫殿臭豆腐发源地", "tags": ["美食", "打卡", "夜生活"], "recommendation": "火宫殿是长沙小吃集大成者，糖油粑粑、姊妹团子必尝"},
        {"name": "天心阁", "level": "4A", "address": "长沙市天心区天心路17号", "area": "天心区", "duration": 1.5, "ticket_price": 32.0, "core_feature": "长沙仅存的古城标志，古城墙上俯瞰星城", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "长沙古城墙的唯一遗存，登阁可看长沙老城区全景"},
        {"name": "IFS国金中心", "level": "无", "address": "长沙市芙蓉区解放西路188号", "area": "芙蓉区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "长沙最高建筑，KAWS雕塑屋顶打卡圣地", "tags": ["打卡", "购物", "美食", "摄影"], "recommendation": "楼顶KAWS雕塑是长沙潮流地标，长沙IFS汇集国际大牌"},
        {"name": "湖南大学", "level": "无", "address": "长沙市岳麓区麓山南路", "area": "岳麓区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "千年学府百年名校，没有围墙的开放式大学", "tags": ["文艺", "打卡", "摄影"], "recommendation": "从岳麓书院到现代大学的千年传承，校园本身就是景区"},
        {"name": "梅溪湖国际文化艺术中心", "level": "无", "address": "长沙市岳麓区梅溪湖路", "area": "岳麓区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "扎哈·哈迪德设计的未来主义建筑，芙蓉花造型", "tags": ["文艺", "摄影", "打卡"], "recommendation": "建筑本身就是艺术品，定期有高品质演出和展览"},
        {"name": "文和友（海信广场店）", "level": "无", "address": "长沙市天心区湘江中路海信广场", "area": "天心区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "还原80年代老长沙市井风貌的超级网红餐厅", "tags": ["美食", "打卡", "摄影", "怀旧"], "recommendation": "不是景点胜似景点，吃小龙虾的同时感受老长沙怀旧氛围"},
    ],
    "郑州": [
        {"name": "少林寺", "level": "5A", "address": "郑州市登封市嵩山少林寺", "area": "登封市", "duration": 4.0, "ticket_price": 80.0, "core_feature": "禅宗祖庭功夫圣地，天下功夫出少林", "tags": ["历史文化", "打卡", "人文", "探险"], "recommendation": "世界文化遗产，武术表演精彩绝伦，塔林肃穆庄严"},
        {"name": "嵩山风景区", "level": "5A", "address": "郑州市登封市嵩山", "area": "登封市", "duration": 5.0, "ticket_price": 80.0, "core_feature": "五岳之中岳，天地之中历史建筑群世界遗产", "tags": ["自然风光", "历史文化", "探险", "打卡"], "recommendation": "少林寺、嵩阳书院、中岳庙、观星台等天地之中建筑群核心"},
        {"name": "河南博物院", "level": "4A", "address": "郑州市金水区农业路8号", "area": "金水区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "中原文明宝库，贾湖骨笛与莲鹤方壶举世闻名", "tags": ["博物馆", "历史文化", "亲子", "人文"], "recommendation": "贾湖骨笛是中国最早的乐器，莲鹤方壶是青铜时代的巅峰"},
        {"name": "郑州黄河风景名胜区", "level": "4A", "address": "郑州市惠济区江山路", "area": "惠济区", "duration": 3.0, "ticket_price": 60.0, "core_feature": "黄河中下游分界线，炎黄二帝巨型塑像面朝大河", "tags": ["自然风光", "历史文化", "打卡", "摄影"], "recommendation": "炎黄二帝巨型塑像巍峨壮观，黄河气垫船体验独一无二"},
        {"name": "二七纪念塔", "level": "无", "address": "郑州市二七区二七广场", "area": "二七区", "duration": 0.5, "ticket_price": 0.0, "core_feature": "郑州城市地标，纪念1923年二七大罢工", "tags": ["打卡", "历史文化"], "recommendation": "郑州的城市原点，周边二七商圈是最繁华的商业区"},
        {"name": "郑州方特欢乐世界", "level": "4A", "address": "郑州市中牟县郑开大道", "area": "中牟县", "duration": 5.0, "ticket_price": 220.0, "core_feature": "中原地区最大的高科技主题乐园", "tags": ["亲子", "打卡", "探险"], "recommendation": "飞越极限项目体验感极强，适合全家出游"},
        {"name": "康百万庄园", "level": "4A", "address": "郑州市巩义市康店镇", "area": "巩义市", "duration": 2.5, "ticket_price": 75.0, "core_feature": "豫商精神代表，明清巨富豪宅", "tags": ["历史文化", "人文", "打卡"], "recommendation": "豫商文化代表，比乔家大院大19倍，建筑群依山就势"},
        {"name": "郑州动物园", "level": "3A", "address": "郑州市金水区花园路103号", "area": "金水区", "duration": 2.5, "ticket_price": 30.0, "core_feature": "河南省最大的动物园，大熊猫馆备受喜爱", "tags": ["亲子", "休闲", "自然"], "recommendation": "大熊猫馆和海洋馆最受欢迎，适合带小朋友的家庭"},
        {"name": "商城遗址", "level": "无", "address": "郑州市管城回族区商城路", "area": "管城回族区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "3600年前商代都城遗址，郑州历史的源头", "tags": ["历史文化", "人文"], "recommendation": "商代城墙遗址距今3600年，是郑州作为八大古都的实物证据"},
        {"name": "银基国际旅游度假区", "level": "4A", "address": "郑州市新密市刘寨镇", "area": "新密市", "duration": 5.0, "ticket_price": 200.0, "core_feature": "中原最大综合性度假区，集冰雪世界与动物王国于一体", "tags": ["亲子", "打卡", "休闲"], "recommendation": "冰雪世界全年保持零下温度，动物王国可与动物近距离互动"},
    ],
    "天津": [
        {"name": "天津之眼", "level": "4A", "address": "天津市河北区李公祠大街与五马路交口", "area": "河北区", "duration": 1.0, "ticket_price": 70.0, "core_feature": "世界上唯一建在桥上的摩天轮，海河上最闪耀的地标", "tags": ["打卡", "摄影", "夜生活"], "recommendation": "夜晚乘坐摩天轮俯瞰海河夜景，天津最浪漫的体验"},
        {"name": "五大道", "level": "4A", "address": "天津市和平区五大道", "area": "和平区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "万国建筑博览会，2000多栋异国风格小洋楼", "tags": ["历史文化", "摄影", "打卡", "休闲"], "recommendation": "骑自行车或坐马车游览最佳，民国风云人物的故居散布其间"},
        {"name": "古文化街", "level": "5A", "address": "天津市南开区通北路", "area": "南开区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "津门故里，天后宫与泥人张杨柳青年画齐聚", "tags": ["历史文化", "美食", "购物", "打卡"], "recommendation": "买泥人张彩塑、杨柳青年画，听一场相声，吃狗不理包子"},
        {"name": "意式风情区", "level": "4A", "address": "天津市河北区自由道", "area": "河北区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "亚洲唯一意大利式建筑群，马可波罗广场为心", "tags": ["摄影", "打卡", "美食", "休闲"], "recommendation": "原汁原味的意大利建筑，露天咖啡馆和西餐厅氛围极佳"},
        {"name": "瓷房子", "level": "4A", "address": "天津市和平区赤峰道72号", "area": "和平区", "duration": 1.5, "ticket_price": 60.0, "core_feature": "用7亿多片古瓷片镶嵌而成的法式洋楼", "tags": ["打卡", "摄影", "文艺"], "recommendation": "独一无二的疯狂建筑，古瓷片镶嵌的墙面令人叹为观止"},
        {"name": "盘山", "level": "5A", "address": "天津市蓟州区盘山", "area": "蓟州区", "duration": 5.0, "ticket_price": 100.0, "core_feature": "京东第一山，乾隆帝32次巡幸", "tags": ["自然风光", "探险", "打卡", "历史文化"], "recommendation": "早知有盘山何必下江南，三盘暮雨是津门十景之一"},
        {"name": "海河游船", "level": "无", "address": "天津市各游船码头", "area": "和平区", "duration": 1.0, "ticket_price": 100.0, "core_feature": "乘船游览海河两岸风光，穿越天津的历史与现代", "tags": ["夜生活", "摄影", "打卡", "休闲"], "recommendation": "夜晚游船看两岸灯光，天津之眼和津湾广场最美"},
        {"name": "天津博物馆", "level": "4A", "address": "天津市河西区平江道62号", "area": "河西区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "了解天津近代历史的窗口，馆藏丰富", "tags": ["博物馆", "历史文化", "人文"], "recommendation": "免费参观，近代天津展厅展示了九国租界的独特历史"},
        {"name": "西开教堂", "level": "无", "address": "天津市和平区西宁道9号", "area": "和平区", "duration": 0.5, "ticket_price": 0.0, "core_feature": "天津最大的天主教堂，罗曼式建筑风格", "tags": ["摄影", "打卡", "文艺"], "recommendation": "天津最出片的教堂建筑，彩色玻璃窗在阳光下美轮美奂"},
        {"name": "杨柳青古镇", "level": "4A", "address": "天津市西青区杨柳青镇", "area": "西青区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "中国北方年画之乡，石家大院为华北第一宅", "tags": ["历史文化", "人文", "打卡", "亲子"], "recommendation": "石家大院和安家大院展现北方富豪生活，体验年画制作"},
    ],
    "厦门": [
        {"name": "鼓浪屿", "level": "5A", "address": "厦门市思明区鼓浪屿", "area": "思明区", "duration": 6.0, "ticket_price": 0.0, "core_feature": "世界文化遗产，万国建筑博览+钢琴之岛", "tags": ["打卡", "摄影", "文艺", "休闲"], "recommendation": "日光岩俯瞰全岛，菽庄花园藏海补山，需要提前预订船票"},
        {"name": "厦门大学", "level": "无", "address": "厦门市思明区思明南路422号", "area": "思明区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "中国最美大学之一，面朝大海的嘉庚建筑群", "tags": ["打卡", "摄影", "文艺"], "recommendation": "芙蓉隧道涂鸦墙和上弦场是最佳拍照点，需预约入校"},
        {"name": "南普陀寺", "level": "无", "address": "厦门市思明区思明南路515号", "area": "思明区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "闽南佛教圣地，背靠五老峰面朝大海", "tags": ["历史文化", "人文", "休闲"], "recommendation": "闽南佛教圣地，素饼是厦门特产伴手礼，免费游览"},
        {"name": "曾厝垵", "level": "无", "address": "厦门市思明区曾厝垵", "area": "思明区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "中国最文艺渔村，小吃与民宿的天堂", "tags": ["美食", "文艺", "打卡", "休闲"], "recommendation": "沙茶面、海蛎煎、土笋冻等闽南小吃吃不停，文艺小店密度极高"},
        {"name": "环岛路", "level": "无", "address": "厦门市思明区环岛路", "area": "思明区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "中国最美马拉松赛道，椰风海韵一路相伴", "tags": ["自然风光", "摄影", "休闲", "打卡"], "recommendation": "骑行环岛路是最佳体验，椰风寨到会展中心段最美"},
        {"name": "中山路步行街", "level": "无", "address": "厦门市思明区中山路", "area": "思明区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "厦门最老牌的商业街，骑楼建筑连绵千米", "tags": ["购物", "美食", "打卡"], "recommendation": "南洋骑楼风格建筑群，花生汤和沙茶面不可错过"},
        {"name": "胡里山炮台", "level": "4A", "address": "厦门市思明区曾厝垵路2号", "area": "思明区", "duration": 1.5, "ticket_price": 25.0, "core_feature": "世界现存最大的海岸古炮台，克虏伯大炮", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "克虏伯大炮是世界上现存最古老最大的海岸炮"},
        {"name": "沙坡尾", "level": "无", "address": "厦门市思明区沙坡尾", "area": "思明区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "厦门港的起源地，老渔港与新潮艺术碰撞", "tags": ["文艺", "摄影", "美食", "打卡"], "recommendation": "老厦门渔港风情与艺术西区的碰撞，避风坞日落很美"},
        {"name": "集美学村", "level": "无", "address": "厦门市集美区嘉庚路", "area": "集美区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "陈嘉庚先生创办的学校建筑群，中西合璧", "tags": ["历史文化", "摄影", "文艺", "打卡"], "recommendation": "嘉庚建筑中西合璧独树一帜，龙舟池畔景色宜人"},
        {"name": "万石植物园", "level": "4A", "address": "厦门市思明区虎园路25号", "area": "思明区", "duration": 3.0, "ticket_price": 30.0, "core_feature": "城市中的热带雨林，多肉植物区成网红打卡地", "tags": ["自然风光", "摄影", "亲子", "打卡"], "recommendation": "多肉植物区和雨林喷雾区最适合拍照，仿佛走进仙境"},
    ],
    "青岛": [
        {"name": "崂山风景区", "level": "5A", "address": "青岛市崂山区崂山", "area": "崂山区", "duration": 5.0, "ticket_price": 120.0, "core_feature": "海上第一名山，道教全真派发祥地之一", "tags": ["自然风光", "历史文化", "探险", "打卡"], "recommendation": "山海相连的独特景观，太清宫和巨峰景区各具特色"},
        {"name": "栈桥", "level": "4A", "address": "青岛市市南区太平路12号", "area": "市南区", "duration": 1.0, "ticket_price": 0.0, "core_feature": "青岛象征，百年历史的清代海军栈桥伸入海中", "tags": ["打卡", "摄影", "历史文化"], "recommendation": "回澜阁是青岛最经典的地标，冬季海鸥聚集时最壮观"},
        {"name": "八大关", "level": "4A", "address": "青岛市市南区八大关", "area": "市南区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "万国建筑博览会，十条以关隘命名的道路各具风情", "tags": ["摄影", "打卡", "休闲", "文艺"], "recommendation": "花石楼和公主楼是最知名建筑，四季花景不断变化"},
        {"name": "青岛啤酒博物馆", "level": "4A", "address": "青岛市市北区登州路56号", "area": "市北区", "duration": 2.0, "ticket_price": 60.0, "core_feature": "中国首家啤酒博物馆，百年青啤的活历史", "tags": ["美食", "打卡", "博物馆", "休闲"], "recommendation": "门票含两杯原浆啤酒和啤酒豆，了解啤酒酿造全过程"},
        {"name": "五四广场", "level": "无", "address": "青岛市市南区东海西路", "area": "市南区", "duration": 1.0, "ticket_price": 0.0, "core_feature": "青岛新城标，五月的风雕塑象征五四精神", "tags": ["打卡", "摄影", "休闲"], "recommendation": "奥帆中心就在旁边，晚上灯光秀配合海景非常震撼"},
        {"name": "金沙滩", "level": "4A", "address": "青岛市黄岛区金沙滩路", "area": "黄岛区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "亚洲第一滩，沙质细腻色泽金黄", "tags": ["自然风光", "休闲", "亲子"], "recommendation": "沙质细腻呈金黄色，比市区海水浴场更宽阔干净"},
        {"name": "天主教堂", "level": "无", "address": "青岛市市南区浙江路15号", "area": "市南区", "duration": 0.5, "ticket_price": 10.0, "core_feature": "青岛最大哥特式建筑，双塔耸立老城区", "tags": ["摄影", "打卡", "文艺"], "recommendation": "青岛最出片的教堂，周边老城区漫步非常有欧洲小镇感觉"},
        {"name": "信号山公园", "level": "3A", "address": "青岛市市南区龙山路16号", "area": "市南区", "duration": 1.5, "ticket_price": 15.0, "core_feature": "俯瞰青岛老城区红瓦绿树碧海蓝天的最佳观景台", "tags": ["摄影", "打卡", "休闲"], "recommendation": "旋转观景台可360度俯瞰老青岛红瓦绿树全景"},
        {"name": "青岛海底世界", "level": "4A", "address": "青岛市市南区莱阳路1号", "area": "市南区", "duration": 2.5, "ticket_price": 150.0, "core_feature": "中国第一座水族馆，海底隧道与梦幻水母宫", "tags": ["亲子", "打卡", "自然"], "recommendation": "海底隧道和梦幻水母宫适合亲子游，与海洋生物零距离接触"},
        {"name": "小鱼山公园", "level": "4A", "address": "青岛市市南区福山支路24号", "area": "市南区", "duration": 1.0, "ticket_price": 10.0, "core_feature": "览潮阁上俯瞰汇泉湾和第一海水浴场", "tags": ["摄影", "打卡", "休闲"], "recommendation": "青岛最佳日落观赏点之一，老城全景尽收眼底"},
    ],
    "大连": [
        {"name": "星海广场", "level": "无", "address": "大连市沙河口区星海广场", "area": "沙河口区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "亚洲最大城市广场，百年城雕与华表气势恢宏", "tags": ["打卡", "摄影", "休闲"], "recommendation": "大连城市名片，星海湾跨海大桥和城堡酒店是最佳拍照背景"},
        {"name": "老虎滩海洋公园", "level": "5A", "address": "大连市中山区滨海中路9号", "area": "中山区", "duration": 4.0, "ticket_price": 220.0, "core_feature": "中国最大极地海洋动物馆，白鲸和海豚表演精彩", "tags": ["亲子", "打卡", "自然"], "recommendation": "极地馆的白鲸表演最受欢迎，珊瑚馆美轮美奂"},
        {"name": "金石滩国家旅游度假区", "level": "5A", "address": "大连市金州区金石滩", "area": "金州区", "duration": 5.0, "ticket_price": 60.0, "core_feature": "天然地质博物馆，震旦纪岩石海岸地貌奇观", "tags": ["自然风光", "休闲", "摄影", "亲子"], "recommendation": "发现王国主题乐园适合亲子，黄金海岸沙质极佳"},
        {"name": "大连森林动物园", "level": "4A", "address": "大连市西岗区迎春路60号", "area": "西岗区", "duration": 4.0, "ticket_price": 120.0, "core_feature": "依山傍海的动物园，大熊猫和东北虎都有", "tags": ["亲子", "自然", "打卡"], "recommendation": "圈养区和散养区分开，散养区可坐游览车近距离看动物"},
        {"name": "棒棰岛", "level": "4A", "address": "大连市中山区迎宾路1号", "area": "中山区", "duration": 2.0, "ticket_price": 20.0, "core_feature": "国宾馆所在地，大连最清净的海水浴场", "tags": ["自然风光", "休闲", "摄影"], "recommendation": "海水清澈见底，是领导人疗养胜地，游客稀少清净"},
        {"name": "滨海路", "level": "无", "address": "大连市中山区滨海路", "area": "中山区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "中国最美滨海公路，山海相依一路风光", "tags": ["自然风光", "摄影", "休闲", "打卡"], "recommendation": "建议自驾或徒步，燕窝岭到北大桥段风景最美"},
        {"name": "俄罗斯风情街", "level": "无", "address": "大连市西岗区胜利街", "area": "西岗区", "duration": 1.0, "ticket_price": 0.0, "core_feature": "百年前俄国人建造的老街，异国情调浓郁", "tags": ["购物", "摄影", "打卡"], "recommendation": "买俄罗斯套娃和巧克力，拍俄式建筑，街头有手风琴表演"},
        {"name": "旅顺口", "level": "4A", "address": "大连市旅顺口区", "area": "旅顺口区", "duration": 4.0, "ticket_price": 0.0, "core_feature": "半部中国近代史，军港与日俄战争遗址", "tags": ["历史文化", "打卡", "人文"], "recommendation": "旅顺军港、白玉山塔和日俄监狱旧址是了解近代史的重要窗口"},
        {"name": "东港威尼斯水城", "level": "无", "address": "大连市中山区港浦路", "area": "中山区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "仿威尼斯建造的水上新城，贡多拉游船穿梭其间", "tags": ["打卡", "摄影", "夜生活"], "recommendation": "夜晚灯光最美，乘坐贡多拉小船仿佛穿越到欧洲"},
        {"name": "冰峪沟", "level": "4A", "address": "大连市庄河市仙人洞镇", "area": "庄河市", "duration": 5.0, "ticket_price": 120.0, "core_feature": "辽南小桂林，峡谷溪流与英纳河水库交相辉映", "tags": ["自然风光", "探险", "摄影"], "recommendation": "距市区约2.5小时车程，秋天红叶满山最美"},
    ],
    "昆明": [
        {"name": "石林风景区", "level": "5A", "address": "昆明市石林彝族自治县石林", "area": "石林县", "duration": 4.0, "ticket_price": 130.0, "core_feature": "世界自然遗产，喀斯特地貌奇观，阿诗玛的故乡", "tags": ["自然风光", "摄影", "打卡", "探险"], "recommendation": "世界自然遗产，大石林和小石林各具特色，建议请导游讲解"},
        {"name": "滇池", "level": "4A", "address": "昆明市西山区滇池路", "area": "西山区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "云南最大淡水湖，高原明珠，冬季红嘴鸥聚集", "tags": ["自然风光", "休闲", "摄影", "亲子"], "recommendation": "冬季海埂大坝喂红嘴鸥是昆明最经典的体验"},
        {"name": "云南民族村", "level": "4A", "address": "昆明市西山区滇池路1310号", "area": "西山区", "duration": 4.0, "ticket_price": 90.0, "core_feature": "26个少数民族村寨缩影，一日体验多彩云南", "tags": ["打卡", "亲子", "人文", "历史文化"], "recommendation": "各民族的歌舞表演和手工艺展示非常精彩，建议安排半天"},
        {"name": "西山森林公园", "level": "4A", "address": "昆明市西山区西山", "area": "西山区", "duration": 3.0, "ticket_price": 40.0, "core_feature": "睡美人山，龙门石窟俯瞰滇池全景", "tags": ["自然风光", "历史文化", "摄影", "打卡"], "recommendation": "登龙门俯瞰滇池全景，龙门石窟雕刻精美"},
        {"name": "翠湖公园", "level": "无", "address": "昆明市五华区翠湖南路67号", "area": "五华区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "昆明城中碧玉，冬季红嘴鸥与夏日荷花", "tags": ["休闲", "摄影", "自然风光"], "recommendation": "市中心免费公园，冬季红嘴鸥和夏季荷花各有风情"},
        {"name": "金马碧鸡坊", "level": "无", "address": "昆明市五华区金碧路", "area": "五华区", "duration": 0.5, "ticket_price": 0.0, "core_feature": "昆明市徽，金马碧鸡二坊相映成趣", "tags": ["打卡", "历史文化"], "recommendation": "昆明城市象征，周边是繁华商业区，适合逛街时顺路打卡"},
        {"name": "云南省博物馆", "level": "4A", "address": "昆明市官渡区广福路6393号", "area": "官渡区", "duration": 2.5, "ticket_price": 0.0, "core_feature": "古滇国青铜文明与南诏大理国历史的宝库", "tags": ["博物馆", "历史文化", "人文", "亲子"], "recommendation": "古滇国青铜器独一无二，了解云南从远古到现代的历史脉络"},
        {"name": "官渡古镇", "level": "4A", "address": "昆明市官渡区官渡古镇", "area": "官渡区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "千年古镇，少林寺与妙湛寺双塔并存", "tags": ["历史文化", "美食", "打卡"], "recommendation": "官渡粑粑是昆明名小吃，古镇免费游览"},
        {"name": "世博园", "level": "5A", "address": "昆明市盘龙区世博路10号", "area": "盘龙区", "duration": 3.0, "ticket_price": 100.0, "core_feature": "99昆明世博会会址，世界各国园林微缩", "tags": ["打卡", "亲子", "休闲", "自然风光"], "recommendation": "中国馆和温室最有看头，适合亲子游和拍照"},
        {"name": "斗南花市", "level": "无", "address": "昆明市呈贡区斗南街道", "area": "呈贡区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "亚洲最大鲜花交易市场，鲜花按斤卖", "tags": ["购物", "摄影", "文艺", "打卡"], "recommendation": "花10元能买一大把鲜花，晚上8点后批发交易最热闹"},
    ],
    "三亚": [
        {"name": "亚龙湾", "level": "4A", "address": "三亚市亚龙湾国家旅游度假区", "area": "吉阳区", "duration": 4.0, "ticket_price": 0.0, "core_feature": "天下第一湾，沙质洁白海水湛蓝", "tags": ["自然风光", "休闲", "亲子", "打卡"], "recommendation": "三亚最好的海滩，沙质细腻海水清澈，适合游泳和日光浴"},
        {"name": "天涯海角", "level": "4A", "address": "三亚市天涯区天涯海角", "area": "天涯区", "duration": 2.5, "ticket_price": 81.0, "core_feature": "海南旅游的标志，天涯石与海角石见证爱情", "tags": ["打卡", "摄影", "自然风光"], "recommendation": "三亚必打卡地标，天涯石和海角石是经典拍照点"},
        {"name": "南山文化旅游区", "level": "5A", "address": "三亚市崖州区南山", "area": "崖州区", "duration": 4.0, "ticket_price": 129.0, "core_feature": "108米南海观音像巍然矗立海上", "tags": ["历史文化", "打卡", "摄影", "人文"], "recommendation": "108米海上观音宏伟壮观，抱佛脚祈福是特色体验"},
        {"name": "蜈支洲岛", "level": "5A", "address": "三亚市海棠区蜈支洲岛", "area": "海棠区", "duration": 6.0, "ticket_price": 144.0, "core_feature": "中国最佳潜水基地，情人桥和观日岩绝美", "tags": ["自然风光", "探险", "打卡", "摄影"], "recommendation": "潜水天堂，海水能见度极高，水上项目丰富"},
        {"name": "鹿回头风景区", "level": "4A", "address": "三亚市吉阳区鹿回头", "area": "吉阳区", "duration": 2.0, "ticket_price": 42.0, "core_feature": "三亚爱情传说发生地，俯瞰三亚湾全景", "tags": ["摄影", "打卡", "休闲", "夜生活"], "recommendation": "三亚最佳日落和夜景观赏点，鹿回头雕塑是三亚标志"},
        {"name": "大东海", "level": "4A", "address": "三亚市吉阳区大东海", "area": "吉阳区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "三亚市中心最近的海滨浴场，免费开放", "tags": ["自然风光", "休闲", "亲子", "夜生活"], "recommendation": "免费海滩，周边餐饮酒吧密集，夜生活丰富"},
        {"name": "三亚千古情景区", "level": "4A", "address": "三亚市吉阳区迎宾路333号", "area": "吉阳区", "duration": 3.5, "ticket_price": 300.0, "core_feature": "大型歌舞秀展现三亚万年历史", "tags": ["打卡", "亲子", "历史文化"], "recommendation": "一生必看的演出，视觉盛宴令人震撼"},
        {"name": "呀诺达雨林", "level": "5A", "address": "三亚市保亭县三道镇", "area": "保亭县", "duration": 4.0, "ticket_price": 168.0, "core_feature": "热带雨林天然氧吧，踏瀑戏水体验刺激", "tags": ["自然风光", "探险", "亲子", "打卡"], "recommendation": "热带雨林探险，玻璃栈道和踏瀑戏水最受欢迎"},
        {"name": "海棠湾免税店", "level": "无", "address": "三亚市海棠区海棠北路118号", "area": "海棠区", "duration": 3.0, "ticket_price": 0.0, "core_feature": "全球最大单体免税店，国际大牌一应俱全", "tags": ["购物", "打卡"], "recommendation": "海南离岛免税政策每人每年10万额度，比国内专柜便宜30%+"},
        {"name": "西岛", "level": "4A", "address": "三亚市天涯区西岛", "area": "天涯区", "duration": 5.0, "ticket_price": 98.0, "core_feature": "原生态渔村与珊瑚礁潜水，比蜈支洲岛更安静", "tags": ["自然风光", "休闲", "探险", "打卡"], "recommendation": "岛上渔村保留原生态生活，牛王岭看日落极美"},
    ],
    "桂林": [
        {"name": "漓江风景区", "level": "5A", "address": "桂林市阳朔县漓江", "area": "阳朔县", "duration": 5.0, "ticket_price": 215.0, "core_feature": "桂林山水甲天下，百里漓江百里画廊", "tags": ["自然风光", "摄影", "打卡", "休闲"], "recommendation": "乘船从桂林到阳朔4小时，九马画山和黄布倒影是最佳拍摄点"},
        {"name": "象山景区", "level": "5A", "address": "桂林市象山区民主路1号", "area": "象山区", "duration": 1.5, "ticket_price": 55.0, "core_feature": "桂林城徽，象鼻山酷似大象饮水漓江", "tags": ["打卡", "摄影", "自然风光"], "recommendation": "桂林的城市象征，象鼻与象身之间的水月洞是最经典画面"},
        {"name": "阳朔西街", "level": "无", "address": "桂林市阳朔县西街", "area": "阳朔县", "duration": 2.0, "ticket_price": 0.0, "core_feature": "中国第一条洋人街，中西文化交融的地球村", "tags": ["美食", "夜生活", "购物", "打卡"], "recommendation": "啤酒鱼是阳朔特色必吃，酒吧街越夜越热闹"},
        {"name": "龙脊梯田", "level": "4A", "address": "桂林市龙胜县龙脊镇", "area": "龙胜县", "duration": 5.0, "ticket_price": 80.0, "core_feature": "世界梯田之冠，壮族瑶族千年的农耕艺术", "tags": ["自然风光", "摄影", "人文", "打卡"], "recommendation": "距桂林约2小时车程，5-6月灌水期和9-10月金秋季最美"},
        {"name": "芦笛岩", "level": "4A", "address": "桂林市秀峰区芦笛路1号", "area": "秀峰区", "duration": 1.5, "ticket_price": 90.0, "core_feature": "国宾洞，接待过300多位外国元首的溶洞奇观", "tags": ["自然风光", "打卡", "摄影"], "recommendation": "溶洞内钟乳石千姿百态，灯光效果梦幻"},
        {"name": "两江四湖", "level": "5A", "address": "桂林市秀峰区两江四湖", "area": "秀峰区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "桂林城市名片，杉湖日月双塔日夜皆美", "tags": ["夜生活", "摄影", "打卡", "休闲"], "recommendation": "夜游两江四湖最佳，日月双塔和玻璃桥灯光璀璨"},
        {"name": "独秀峰王城", "level": "5A", "address": "桂林市秀峰区王城路1号", "area": "秀峰区", "duration": 2.0, "ticket_price": 100.0, "core_feature": "明代靖江王府，桂林山水甲天下的出处", "tags": ["历史文化", "打卡", "摄影"], "recommendation": "独秀峰上刻有桂林山水甲天下的千古名句"},
        {"name": "十里画廊", "level": "无", "address": "桂林市阳朔县321国道", "area": "阳朔县", "duration": 3.0, "ticket_price": 0.0, "core_feature": "骑行阳朔最美路段，月亮山大榕树蝴蝶泉沿途分布", "tags": ["自然风光", "摄影", "探险", "打卡"], "recommendation": "租电动车或自行车骑行最佳，沿途风景如画"},
        {"name": "银子岩", "level": "4A", "address": "桂林市荔浦市马岭镇", "area": "荔浦市", "duration": 1.5, "ticket_price": 65.0, "core_feature": "世界溶洞奇观，游了银子岩一世不缺钱", "tags": ["自然风光", "打卡", "摄影"], "recommendation": "三层溶洞景观，雪山飞瀑和音乐石屏最震撼"},
        {"name": "遇龙河漂流", "level": "无", "address": "桂林市阳朔县遇龙河", "area": "阳朔县", "duration": 2.5, "ticket_price": 180.0, "core_feature": "人工竹筏漂流，比漓江更宁静更诗意的山水画卷", "tags": ["自然风光", "休闲", "摄影", "打卡"], "recommendation": "人工撑筏安静悠闲，富里桥到旧县段风景最美"},
    ],
    "哈尔滨": [
        {"name": "中央大街", "level": "4A", "address": "哈尔滨市道里区中央大街", "area": "道里区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "亚洲最长的步行街，71栋欧式建筑连绵千米", "tags": ["打卡", "摄影", "美食", "购物"], "recommendation": "马迭尔冰棍和哈尔滨红肠必尝，面包石路面百年历史"},
        {"name": "冰雪大世界", "level": "无", "address": "哈尔滨市松北区太阳岛西区", "area": "松北区", "duration": 3.0, "ticket_price": 330.0, "core_feature": "世界最大冰雪主题乐园，冰雕艺术殿堂", "tags": ["打卡", "摄影", "亲子", "夜生活"], "recommendation": "仅冬季开放（12月-2月），夜晚灯光下的冰雕如同童话世界"},
        {"name": "圣索菲亚教堂", "level": "无", "address": "哈尔滨市道里区透笼街88号", "area": "道里区", "duration": 1.0, "ticket_price": 20.0, "core_feature": "远东最大东正教堂，拜占庭式建筑的杰作", "tags": ["打卡", "摄影", "历史文化"], "recommendation": "哈尔滨最出片的建筑，绿色洋葱头穹顶极具异域风情"},
        {"name": "太阳岛风景区", "level": "5A", "address": "哈尔滨市松北区太阳岛", "area": "松北区", "duration": 4.0, "ticket_price": 30.0, "core_feature": "松花江上的城市绿洲，夏季避暑冬季雪博", "tags": ["自然风光", "休闲", "亲子", "打卡"], "recommendation": "冬季雪博会和夏季俄罗斯小镇各有特色，俄罗斯风情小镇有趣"},
        {"name": "松花江", "level": "无", "address": "哈尔滨市道里区斯大林街", "area": "道里区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "冬季冰封的松花江变身天然游乐场", "tags": ["休闲", "摄影", "自然风光"], "recommendation": "冬季江面冰上马车、滑冰、狗拉爬犁，夏季江边散步看日落"},
        {"name": "伏尔加庄园", "level": "4A", "address": "哈尔滨市香坊区成高子镇", "area": "香坊区", "duration": 3.0, "ticket_price": 100.0, "core_feature": "俄罗斯主题文化庄园，30多座俄式建筑复刻", "tags": ["摄影", "打卡", "文艺", "休闲"], "recommendation": "仿佛穿越到俄罗斯，圣尼古拉教堂的复刻版最为壮观"},
        {"name": "东北虎林园", "level": "4A", "address": "哈尔滨市松北区松北街88号", "area": "松北区", "duration": 2.0, "ticket_price": 100.0, "core_feature": "世界最大东北虎繁育基地，近距离看猛虎", "tags": ["亲子", "自然", "打卡"], "recommendation": "坐观光车穿行虎群中，可买活鸡投喂看猛虎扑食"},
        {"name": "哈尔滨极地馆", "level": "4A", "address": "哈尔滨市松北区太阳大道3号", "area": "松北区", "duration": 2.5, "ticket_price": 160.0, "core_feature": "世界首座极地演艺游乐园，白鲸水下表演", "tags": ["亲子", "打卡", "自然"], "recommendation": "白鲸米拉和尼克拉的水下海洋之心表演感动无数观众"},
        {"name": "老道外中华巴洛克", "level": "3A", "address": "哈尔滨市道外区靖宇街", "area": "道外区", "duration": 2.0, "ticket_price": 0.0, "core_feature": "中国保留最完整的中华巴洛克建筑群", "tags": ["历史文化", "美食", "摄影", "文艺"], "recommendation": "哈尔滨美食聚集地，张包铺、老鼎丰等百年老字号云集"},
        {"name": "果戈里大街", "level": "无", "address": "哈尔滨市南岗区果戈里大街", "area": "南岗区", "duration": 1.5, "ticket_price": 0.0, "core_feature": "百年商业老街，秋林公司里道斯红肠闻名", "tags": ["购物", "美食", "打卡"], "recommendation": "秋林公司买红肠和大列巴，奋斗路上的老洋房值得一看"},
    ],
}

# ---- 缓存查询接口 ----

def _calculate_tag_similarity(tags: list[str], spot_tags: list[str]) -> float:
    """Jaccard 系数 —— 偏好标签与景点标签的相似度。"""
    if not tags:
        return 0.0
    set_a = {t.lower() for t in tags}
    set_b = {t.lower() for t in spot_tags}
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def get_cached_spots(
    destination: str,
    tags: list[str] | None = None,
) -> list[dict] | None:
    """从本地缓存获取景点列表。

    命中返回标准化景点列表，未命中返回 None（调用方走原有检索逻辑）。

    Args:
        destination: 目的地城市名称。
        tags: 偏好标签列表，非空时按 Jaccard 相似度排序。

    Returns:
        list[dict] | None: 景点列表或 None。
    """
    if os.getenv("SPOT_CACHE_ENABLED", "1") != "1":
        return None

    if tags is None:
        tags = []

    dest_clean = destination.strip()

    # 精确匹配
    spots: list[dict] | None = SPOT_CACHE.get(dest_clean)

    # 别名匹配
    if spots is None:
        resolved = CITY_ALIASES.get(dest_clean)
        if resolved:
            spots = SPOT_CACHE.get(resolved)

    if spots is None:
        return None

    if not tags:
        # 无偏好 → 返回前 8 个热门景点
        return spots[:8]

    # 按 Jaccard 相似度排序
    scored: list[tuple[float, dict]] = []
    for spot in spots:
        sim = _calculate_tag_similarity(tags, spot.get("tags", []))
        scored.append((sim, spot))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 返回相似度 > 0 的，至少 5 个
    result = [s for _, s in scored if _ > 0][:10]
    if len(result) < 5:
        result = [s for _, s in scored[:10]]

    return result


def is_hot_city(destination: str) -> bool:
    """判断目的地是否为热门城市（缓存覆盖）。

    Args:
        destination: 目的地城市名称。

    Returns:
        bool: 是否在缓存中。
    """
    dest_clean = destination.strip()
    return dest_clean in SPOT_CACHE or dest_clean in CITY_ALIASES
