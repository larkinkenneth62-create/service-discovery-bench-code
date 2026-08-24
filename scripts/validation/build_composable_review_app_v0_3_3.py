#!/usr/bin/env python3
"""Build the offline composable v0.3.3 task-necessity review application."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_INPUT = Path(
    "outputs/composable_paired_task_preparation_v0_3_3/"
    "composable_paired_task_review_items_v0_3_3.csv"
)
DEFAULT_TRANSLATIONS = Path(
    "outputs/composable_paired_task_preparation_v0_3_3/"
    "composable_query_translations_zh_v0_3_3.json"
)
DEFAULT_OUTPUT = Path(
    "outputs/composable_paired_task_preparation_v0_3_3/"
    "composable_paired_task_review_app_v0_3_3.html"
)
DEFAULT_MANIFEST = Path(
    "outputs/composable_paired_task_preparation_v0_3_3/"
    "composable_paired_task_review_app_v0_3_3_manifest.json"
)

HUMAN_FIELDS = [
    "dependency_required_for_query",
    "upstream_already_satisfies_subgoal",
    "full_query_subgoals_covered_by_gold_chain",
    "disconnected_parallel_subgoals_present",
    "cross_service_dependency_valid",
    "dependency_edge_valid",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "query_gold_chain_alignment",
    "service_gold_complete",
    "service_candidate_space_valid",
    "service_leakage_final",
    "service_level_eligible",
    "api_gold_complete",
    "api_candidate_space_valid",
    "api_parent_mapping_valid",
    "api_leakage_final",
    "api_level_eligible",
    "composable_release_action",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]

CATEGORY_ZH = {
    "Location": "位置与地点",
    "Mapping": "地图与地理编码",
    "Weather": "天气",
    "Travel": "旅行",
    "Sports": "体育",
    "Finance": "金融",
    "Business": "商业",
    "Data": "数据",
    "Tools": "通用工具",
    "Text Analysis": "文本分析",
    "Translation": "翻译",
    "Music": "音乐",
    "Movies": "影视",
    "Social": "社交媒体",
    "Food": "餐饮与食谱",
    "Health and Fitness": "健康与健身",
    "Transportation": "交通运输",
    "Logistics": "物流",
    "Email": "电子邮件",
    "News, Media": "新闻与媒体",
}

SERVICE_NAME_EXACT = {
    "Reverse Geocoding and Geolocation Service": "逆地理编码与地理位置服务",
    "IP Geolocation_v3": "IP 地理位置查询服务 v3",
    "Precise IP Location and other data": "精确 IP 位置与附加数据服务",
    "MapToolkit": "地图工具包",
    "MapTiles": "地图瓦片服务",
    "WeatherAPI.com": "综合天气数据服务",
    "Air Quality": "空气质量服务",
    "Astronomy": "天文数据服务",
    "Email Utilities": "电子邮件验证与规范化工具",
    "Email Validator": "电子邮件验证服务",
    "Text to Speech": "文本转语音服务",
    "Text Sentiment": "文本情感分析服务",
    "Profanity Filter": "不当语言检测服务",
    "Distance Calculator_v3": "坐标距离计算服务 v3",
    "Body Mass Index (BMI) Calculator": "身体质量指数计算服务",
    "BMI Calculator_v2": "身体质量指数计算服务 v2",
    "Chess Puzzles_v2": "国际象棋题库 v2",
    "Domain Checker": "域名可用性检查服务",
    "DNS Records Lookup": "DNS 记录查询服务",
    "Dream Diffusion": "定制图像生成服务",
    "Microsoft Edge Text to Speech": "微软 Edge 文本转语音服务",
    "Shakespeare Translator": "莎士比亚风格英语翻译服务",
    "MyMemory Translation Memory": "MyMemory 翻译记忆服务",
    "Deezer": "Deezer 音乐服务",
    "Google Translate": "Google 翻译服务",
    "OpenCage Geocoder": "OpenCage 地理编码服务",
    "Spott": "Spott 地点、城市与国家查询服务",
    "WGD Places": "WGD 国家与城市地点数据服务",
}


CAPABILITY_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("air quality", "pollution"), "空气质量查询", "查询当前、预报或历史空气质量及污染指标"),
    (("weather alert", "severe weather", "active alert"), "天气预警查询", "查询指定地点生效中的恶劣天气预警"),
    (("weather station",), "气象站查询", "查询附近气象站及其位置或观测数据"),
    (("weather", "forecast", "temperature", "precipitation"), "天气数据查询", "查询当前天气、逐小时或多日天气预报"),
    (("sunrise", "sunset", "astronomy", "moonrise", "planet position", "celestial"), "天文与日出日落查询", "查询日出、日落、月相或其他天文数据"),
    (("tide", "noaa station"), "潮汐与海洋站点查询", "查询潮汐、海洋观测站及相关时间数据"),
    (("time zone", "timezone", "local time", "current time"), "时区与当地时间查询", "根据地点或坐标查询时区和当地时间"),
    (("ip geolocation", "ip address", "geoip", "ip location"), "IP 地理位置查询", "根据 IP 地址查询城市、国家、坐标和时区等位置数据"),
    (("reverse geocod",), "逆地理编码", "把经纬度坐标转换为可读地址或附近地点"),
    (("geocod", "latitude", "longitude", "coordinates", "geolocation"), "地理编码与坐标查询", "在地址、地点与经纬度坐标之间进行查询或转换"),
    (("postal code", "postcode", "zip code", "zipcode"), "邮政编码查询", "查询邮政编码、地址及对应行政区信息"),
    (("distance", "directions", "routing", "route"), "距离与路线查询", "计算地点间距离、路线或导航方向"),
    (("nearby", "nearest cities", "places", "attraction", "landmark", "venue"), "地点与附近设施搜索", "搜索地点、附近设施、景点或活动场地"),
    (("hotel", "accommodation", "hostel"), "酒店与住宿搜索", "搜索酒店、旅舍或其他住宿并返回详情"),
    (("restaurant", "cafe", "food venue"), "餐厅与餐饮地点搜索", "搜索餐厅、咖啡馆或其他餐饮地点"),
    (("brewery", "breweries"), "啤酒厂信息查询", "搜索啤酒厂并查询其营业、设施或地点详情"),
    (("rental", "airbnb", "vrbo", "property"), "房产与短租数据查询", "搜索房产或短租房源并查询设施、评分等信息"),
    (("traffic camera", "traffic", "webcam"), "实时交通与摄像头查询", "查询交通状况、道路摄像头或现场画面"),
    (("flight", "aircraft", "airport", "adsb"), "航空与航班数据查询", "查询机场、航班或飞机实时位置与规格"),
    (("helicopter",), "直升机规格查询", "按条件查询直升机尺寸、速度和载荷等规格"),
    (("vehicle", "car ", "cars", "vin", "automobile", "trim"), "车辆数据查询", "查询汽车品牌、车型、配置、VIN 或车辆规格"),
    (("train", "railway", "station", "public transport", "trip search"), "铁路与公共交通查询", "搜索车站、公共交通线路、班次和行程"),
    (("email valid", "validate email", "verify email", "mailbox"), "电子邮件验证", "验证电子邮件地址格式、可投递性或邮箱状态"),
    (("temporary email", "new email", "email address", "email alias"), "电子邮件地址管理", "创建或查询临时邮箱、发件地址或邮箱别名"),
    (("dns",), "DNS 记录查询", "查询域名的 DNS 记录及相关网络配置"),
    (("domain", "whois"), "域名与 WHOIS 查询", "检查域名可用性并查询 WHOIS 或注册信息"),
    (("proxy",), "代理服务器查询", "获取代理服务器并检查可用性或风险"),
    (("translate", "translation", "language detection", "supported languages"), "语言翻译与检测", "翻译文本、检测语言或列出支持的语言"),
    (("text to speech", "speech synthesis", "voice", "audio"), "文本转语音", "把文本合成为可播放的语音或音频"),
    (("sentiment",), "文本情感分析", "识别文本的情感倾向和整体语气"),
    (("profanity", "offensive", "nsfw"), "不当内容检测", "检测脏话、冒犯性文本或不宜内容"),
    (("named entit", "entity extraction"), "命名实体识别", "从文本中提取人物、地点、组织等命名实体"),
    (("rewrite", "paraphrase", "bionized"), "文本改写与处理", "对文本进行改写、格式化或辅助阅读处理"),
    (("qr code", "qrcode"), "二维码生成", "根据文本或网址生成二维码"),
    (("uuid", "guid"), "唯一标识符生成", "生成或查询 UUID、GUID 等唯一标识符"),
    (("image", "diffusion", "thumbnail", "animation"), "图像生成与处理", "生成、转换或分析图片和动画素材"),
    (("video", "mp4"), "视频素材处理", "查询、生成或处理视频文件及其元数据"),
    (("nba", "basketball", "football", "soccer", "sports", "tournament"), "体育赛事与队伍查询", "查询体育项目、球队、球员、赛程或比赛结果"),
    (("horse racing", "racecard", "race odds"), "赛马数据查询", "查询赛马赛程、赛卡、赛事详情或赔率"),
    (("golf",), "高尔夫数据查询", "查询高尔夫球场、赛事或赔率"),
    (("chess",), "国际象棋数据查询", "获取棋题、对局或相关游戏数据"),
    (("movie", "imdb", "streaming", "title details"), "影视内容查询", "搜索电影、评分、演员、类型和流媒体信息"),
    (("music", "artist", "track", "playlist", "album", "deezer"), "音乐内容查询", "查询艺人、歌曲、播放列表、专辑或发行信息"),
    (("news", "article", "newspaper", "telegraph"), "新闻与文章查询", "搜索新闻文章、媒体内容或文章详情"),
    (("instagram", "tiktok", "social media", "reddit", "hashtag"), "社交媒体数据查询", "查询社交账号、帖子、话题标签或互动数据"),
    (("recipe", "ingredient", "cooking"), "食材与食谱查询", "查询食材、菜谱或烹饪相关信息"),
    (("currency", "exchange rate", "forex"), "汇率与货币查询", "查询货币信息、实时或历史汇率"),
    (("stock", "bond", "financial", "revenue", "transaction"), "金融与交易数据查询", "查询证券、债券、收入、交易或金融时间序列"),
    (("bmi", "body mass"), "BMI 计算与分类", "根据身高体重计算身体质量指数并判断类别"),
    (("quote", "joke", "random word", "dictionary", "antonym", "synonym"), "词语、名言与文本素材查询", "获取词语释义、名言、笑话或语言素材"),
    (("job", "visa sponsorship"), "招聘职位查询", "搜索职位及签证担保等招聘条件"),
    (("coupon",), "优惠码查询", "查询指定网站或商品可用的优惠码"),
    (("product", "inventory", "store"), "商品与库存查询", "查询商品、库存、类别、价格或详情"),
    (("map tile", "staticmap", "contour", "terrain tile"), "地图瓦片与地图图像", "获取指定样式或语言标注的地图瓦片、静态地图或地形图层"),
    (("google search", "search result", "serp", "web search"), "网页搜索", "执行网页搜索并返回结果、摘要或相关链接"),
    (("jail", "arrest", "inmate", "mugshot"), "逮捕与在押人员信息查询", "查询逮捕记录、在押人员或监狱公开信息"),
    (("seaport", "port names", "port information"), "港口信息查询", "查询海港名称、所在城市、代码和联系信息"),
    (("address correction", "address validation", "verify address", "global address"), "地址校验与规范化", "校验、纠正和规范化邮政地址，并可返回地理编码"),
    (("ipl", "nhl", "nfl", "motogp", "league", "match details", "live score", "player", "team details", "standings", "driver details"), "体育赛事与人员数据", "查询联赛、球队、运动员、比分、排名或赛事详情"),
    (("linkedin", "profile data", "follower", "user profile", "user metadata"), "用户与个人资料查询", "查询公开用户资料、账号关系或个人元数据"),
    (("book", "author", "publisher"), "图书与作者信息查询", "查询图书、作者、出版社及出版资料"),
    (("phone number", "mobile number", "contact", "email phone", "scraper"), "联系方式提取与验证", "提取或验证电子邮件、电话号码和其他公开联系信息"),
    (("etsy", "tag", "keyword", "seo", "backlink", "sitemap"), "关键词与网站分析", "查询标签、关键词、反向链接、站点地图或搜索优化数据"),
    (("star", "planet", "moon phase", "astrolog", "horoscope", "birth chart"), "星体与占星数据", "查询恒星、行星、月相、星盘或每日星座信息"),
    (("carbon dioxide", "co2", "carbon emission"), "碳排放与二氧化碳数据", "查询大气二氧化碳浓度或估算碳排放"),
    (("country", "countries", "city", "cities", "state", "administrative", "boundary", "boundaries"), "国家、城市与行政区数据", "查询国家、城市、州或行政区边界及基础资料"),
    (("motorcycle", "motorbike"), "摩托车规格查询", "按品牌或型号查询摩托车技术规格"),
    (("airline", "fleet"), "航空公司信息查询", "查询航空公司及其机队、代码和运营资料"),
    (("facebook ad", "advert", "marketing copy", "caption"), "营销文案生成", "生成广告、社交媒体标题或营销内容"),
    (("course review", "learning path", "teacher", "faculty"), "课程与教学评价查询", "查询课程、教师、学习路径及评价信息"),
    (("mongodb", "database", "vault", "wallet database"), "数据库与数据记录操作", "查询或管理数据库中的记录、存储项或钱包数据"),
    (("gitlab", "devops", "deployment tool", "source code"), "开发与部署工具查询", "查询代码托管、DevOps、部署或项目管理相关数据"),
    (("artificial intelligence", "ai bot", "ask-ai", "chatopt", "ai-powered"), "人工智能问答与生成", "根据请求生成回答、内容或带来源的搜索结果"),
    (("ascii", "binary", "unit converter", "convert from one unit", "measurement"), "格式与计量单位转换", "在文本编码、数值格式或计量单位之间进行转换"),
    (("tax identification", "tin check", "tin lookup"), "税号验证", "查询或验证个人和企业税务识别号"),
    (("authentication", "authorization", "login", "oauth", "identity"), "身份验证与授权", "执行账号登录、身份认证、授权或访问检查"),
    (("body weight", "calorie", "meal planner", "metabolic rate"), "营养与身体指标计算", "计算体重、热量、基础代谢或生成饮食计划"),
    (("password",), "密码生成", "按长度和规则生成随机或定制密码"),
    (("barcode",), "条形码生成", "生成不同制式和样式的条形码"),
    (("ranking", "world ranking"), "排名数据查询", "查询运动员、项目或对象的排行榜数据"),
    (("airplane", "airplanes"), "飞机规格查询", "查询商用飞机型号及其基础技术规格"),
    (("wallet", "multisig", "nft"), "数字钱包与 NFT 查询", "查询数字钱包、资产、NFT 或多签记录"),
    (("philosopher", "philosophy"), "哲学人物与思想查询", "查询哲学家生平、思想和历史资料"),
    (("watch model", "watches"), "腕表型号查询", "查询腕表品牌、型号、发布日期和功能参数"),
    (("molecular", "chemical", "quantum"), "分子与化学数据查询", "查询分子结构、量子化学或分子动力学数据"),
    (("trivia", "facts about", "interesting facts", "numbers api"), "知识事实与趣闻查询", "获取历史、数字或常识类事实和趣闻"),
    (("torrent",), "种子资源搜索", "跨多个站点搜索种子资源并返回结果"),
    (("boardgame", "game database", "game details", "champion"), "游戏与角色数据查询", "查询桌游、电子游戏、角色或对局详情"),
    (("random password", "user agent"), "随机技术数据生成", "生成随机密码、用户代理或其他测试数据"),
    (("xml", "json response", "sample response", "request headers", "request body"), "接口示例与请求检查", "查看接口示例响应、请求头或请求体，用于演示和调试"),
    (("todo", "to do"), "待办事项管理", "创建、查询或管理待办事项"),
    (("string", "query string", "url argument", "text converter"), "字符串与 URL 处理", "处理字符串、URL 参数、查询串或文本格式"),
    (("screenshot",), "截图生成与下载", "生成、获取或下载截图文件"),
    (("download", "stream mp3", "mp3"), "媒体下载与流式传输", "生成媒体下载地址或提供音频流"),
    (("pet",), "宠物记录查询", "根据标识符查询宠物记录"),
    (("facility", "hospital", "bank", "college"), "公共设施查询", "按城市和类型查询医院、银行、学校等设施"),
    (("solar", "heliospheric", "helioviewer"), "太阳与日球层数据可视化", "查询或可视化太阳及日球层观测数据"),
    (("onboarding project", "demo project", "teste", "sample response"), "测试或演示接口", "原始 catalog 显示为入门、测试或演示接口；应重点检查是否属于无效候选"),
    (("fipe", "veiculo", "marca", "modelo"), "车辆品牌与型号查询", "按车辆类型、品牌、型号和年份查询车辆资料"),
    (("apimail", "mailslurp", "email sandbox", "email_validation", "readmail", "current mail", "normalize form of an email"), "邮箱创建、测试与验证", "创建、读取、规范化或验证测试邮箱和电子邮件地址"),
    (("java code", "compiler", "java versions"), "Java 编译与版本查询", "查询 Java 版本或编译、运行 Java 代码"),
    (("love calculator", "relationship strength"), "关系匹配计算", "根据输入信息计算关系匹配或强度指标"),
    (("partenaire", "partner locations", "mobilis"), "合作网点查询", "查询并定位指定机构的合作伙伴或服务网点"),
    (("speakeasy", "synthesize speech", "google's text-to-speech"), "文本转语音", "把输入文本合成为语音音频"),
    (("wayback", "archived web", "internet archive"), "网页存档查询", "在互联网档案中搜索和获取历史网页快照"),
    (("fancy text",), "花体文字转换", "把普通文本转换为装饰性花体文本"),
    (("textapi", "natural language processing", "nlp"), "自然语言处理", "执行文本提取、处理和自然语言分析"),
    (("shakespeare", "old english", "anglo-saxon"), "古英语或莎士比亚风格翻译", "把现代英语转换为古英语或莎士比亚风格表达"),
    (("mlem", "blep", "animal picture"), "动物图片获取", "获取动物图片或相关趣味图像"),
    (("films",), "影片数据查询", "查询影片、类型或相关影视资料"),
    (("makani", "geographic addressing"), "迪拜 Makani 地址查询", "根据迪拜坐标或 Makani 编号查询官方地址信息"),
    (("word scramble", "scramble"), "单词字母打乱", "将输入单词的字母顺序随机打乱"),
    (("waifu", "chatbot"), "聊天机器人", "与角色型聊天机器人进行文本对话"),
    (("truecaller", "bulk number", "number details"), "电话号码信息查询", "查询单个或批量电话号码的归属及公开详情"),
    (("transitaire", "customs broker", "dédouanement"), "清关代理查询", "查询清关代理或货运代理机构信息"),
    (("get place", "single place", "place identified"), "地点详情查询", "根据 IP、ID 或其他标识查询地点详情"),
    (("bodies positions", "body positions"), "天体位置查询", "查询太阳、月球、行星等天体的位置数据"),
    (("supported makes", "supported types", "supported years"), "车辆可选项查询", "列出支持的汽车品牌、类型或年份"),
    (("repost",), "文章转载", "把指定文章转载到目标发布平台"),
    (("year fact",), "年份趣闻查询", "获取指定年份对应的历史事实或趣闻"),
    (("puzzle",), "棋题或谜题查询", "按条件随机获取棋题、谜题或练习题"),
    (("auto-complete", "autocomplete", "autosuggest", "suggestions by"), "自动补全与建议", "根据关键词返回搜索建议或自动补全结果"),
    (("data sets available", "availability"), "数据可用性查询", "检查指定地点或条件下可用的数据集"),
    (("historical rates",), "历史汇率查询", "查询指定日期的历史货币汇率"),
    (("citation",), "引文与名句查询", "查询词语、作者或主题相关的引文和名句"),
    (("username", "dribbble", "dev.to"), "用户名可用性检查", "检查指定用户名在社交或开发者平台上是否可用"),
    (("cast & crew", "title similars", "similar title"), "电影演职员与相似影片查询", "查询影片演职员信息或相似影片"),
    (("temporary email", "get email"), "临时邮箱获取", "创建或获取用于临时收发邮件的邮箱地址"),
    (("fixtures and results",), "赛程与赛果查询", "查询指定队伍或赛事的赛程和比赛结果"),
    (("aka", "alternative title"), "影片别名查询", "查询影片在不同地区或语言下的别名"),
    (("api health", "health check", "health"), "服务健康检查", "检查 API 或在线服务的运行状态"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a single-file Chinese bilingual composable review app."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--translations", type=Path, default=DEFAULT_TRANSLATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV has no header: {path}")
        rows = list(reader)
        columns = list(reader.fieldnames)
    if not rows:
        raise ValueError(f"Input CSV has no rows: {path}")
    missing = [name for name in HUMAN_FIELDS if name not in columns]
    if missing:
        raise ValueError(f"Input CSV is missing human fields: {missing}")
    return rows, columns


def parse_json_list(raw: str, field_name: str, item_id: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{item_id}: invalid JSON in {field_name}: {exc}") from exc
    if not isinstance(value, list):
        raise ValueError(f"{item_id}: {field_name} must be a JSON list")
    return [item for item in value if isinstance(item, dict)]


def load_query_translations(path: Path, rows: list[dict[str, str]]) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Query translation JSON not found: {path}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Query translation file must contain an object: {path}")
    missing = [row["review_item_id"] for row in rows if not str(value.get(row["review_item_id"], "")).strip()]
    if missing:
        raise ValueError(f"Missing query translations for {len(missing)} rows: {missing[:10]}")
    bad = [key for key, text in value.items() if "�" in str(text)]
    if bad:
        raise ValueError(f"Query translations contain replacement characters: {bad[:10]}")
    return {str(key): str(text).strip() for key, text in value.items()}


def first_rule(text: str) -> tuple[str, str]:
    lowered = " " + re.sub(r"\s+", " ", text.lower()) + " "
    for needles, label, summary in CAPABILITY_RULES:
        if any(needle in lowered for needle in needles):
            return label, summary
    return "低信息或未分类候选", "原始 catalog 信息不足或无法可靠归类；请结合英文原名、英文说明和 query 判断其真实能力及有效性"


def service_name_zh(item: dict[str, Any]) -> tuple[str, str]:
    name = str(item.get("service_name") or item.get("service_key") or "未命名服务").strip()
    description = str(item.get("service_description") or "").strip()
    label, summary = first_rule(
        f"{item.get('service_name', '')} {name} {description} {item.get('service_description', '')}"
    )
    exact = SERVICE_NAME_EXACT.get(name)
    if exact:
        return exact, summary
    generic_tokens = {
        "weather": "天气",
        "air quality": "空气质量",
        "geolocation": "地理位置",
        "geocoding": "地理编码",
        "distance calculator": "距离计算",
        "email validator": "电子邮件验证",
        "translator": "翻译",
        "dictionary": "词典",
        "car data": "汽车数据",
        "car api": "汽车数据服务",
        "map tiles": "地图瓦片",
        "astronomy": "天文数据",
        "currency": "货币",
    }
    lowered = name.lower()
    if lowered in generic_tokens:
        return generic_tokens[lowered] + "服务", summary
    if re.fullmatch(r"[A-Za-z0-9_.&+()' -]+", name):
        return f"{name}（{label}）", summary
    return f"{name}（{label}）", summary


def api_name_zh(item: dict[str, Any]) -> tuple[str, str]:
    name = str(item.get("api_name") or item.get("function_name") or item.get("function_key") or "未命名接口").strip()
    description = str(item.get("api_description") or "").strip()
    label, summary = first_rule(
        f"{item.get('service_name', '')} {name} {description} {item.get('service_description', '')}"
    )
    lowered = re.sub(r"[_-]+", " ", name.lower()).strip()
    exact_patterns = [
        (r"get.*place.*ip", "根据 IP 获取地点"),
        (r"get.*place.*id", "根据 ID 获取地点"),
        (r"search.*place", "搜索地点"),
        (r"reverse", "执行逆地理编码"),
        (r"get.*timezone|timezone", "获取时区"),
        (r"current.*weather", "获取当前天气"),
        (r"hourly.*forecast", "获取逐小时天气预报"),
        (r"forecast", "获取天气预报"),
        (r"alert", "获取天气预警"),
        (r"sunrise|sunset|astronomy", "获取天文与日出日落数据"),
        (r"air.*quality", "获取空气质量数据"),
        (r"nearby|nearest", "搜索附近地点"),
        (r"distance", "计算距离"),
        (r"direction|route", "查询路线"),
        (r"geocode", "执行地理编码"),
        (r"postal|postcode|zip", "查询邮政编码"),
        (r"hotel", "搜索酒店"),
        (r"restaurant", "搜索餐厅"),
        (r"validate.*email|verify.*email", "验证电子邮件地址"),
        (r"dns", "查询 DNS 记录"),
        (r"domain|whois", "查询域名或 WHOIS"),
        (r"translate", "翻译文本"),
        (r"text.*speech|speech", "把文本转换为语音"),
        (r"sentiment", "分析文本情感"),
        (r"profanity", "检测不当语言"),
        (r"qr", "生成二维码"),
        (r"uuid|guid", "生成或查询唯一标识符"),
        (r"list.*team|team", "查询球队信息"),
        (r"player", "查询球员信息"),
        (r"artist", "查询艺人信息"),
        (r"track|playlist|album", "查询音乐内容"),
        (r"movie|title", "查询电影信息"),
        (r"news|article", "查询新闻或文章"),
        (r"health", "检查服务健康状态"),
        (r"list|all", f"列出{label.replace('查询', '')}"),
        (r"search|find", f"搜索{label.replace('查询', '')}"),
        (r"create|generate", f"生成{label.replace('查询', '')}"),
        (r"get|fetch|retrieve", f"获取{label.replace('查询', '')}"),
        (r"check|validate|verify", f"检查{label.replace('查询', '')}"),
    ]
    translated = ""
    for pattern, replacement in exact_patterns:
        if re.search(pattern, lowered):
            translated = replacement
            break
    if not translated:
        translated = f"执行{label}操作"
    if name.lower() not in translated.lower() and name not in {"Reverse", "Forecast", "Search", "List", "Get"}:
        translated = f"{translated}（{name}）"
    return translated, summary


def translation_key_service(item: dict[str, Any]) -> str:
    return str(item.get("service_key") or item.get("service_name") or "").strip()


def translation_key_api(item: dict[str, Any]) -> str:
    service = translation_key_service(item)
    function = str(item.get("function_key") or item.get("api_name") or item.get("function_name") or "").strip()
    return f"{service}::{function}"


def iter_json_items(rows: Iterable[dict[str, str]], fields: Iterable[str]) -> Iterable[dict[str, Any]]:
    for row in rows:
        for field in fields:
            yield from parse_json_list(row.get(field, ""), field, row["review_item_id"])


def build_ui_translations(
    rows: list[dict[str, str]], query_translations: dict[str, str]
) -> dict[str, Any]:
    services: dict[str, dict[str, str]] = {}
    apis: dict[str, dict[str, str]] = {}
    for item in iter_json_items(rows, ["candidate_services_json", "provisional_gold_services_json"]):
        key = translation_key_service(item)
        if not key or key in services:
            continue
        name_zh, description_zh = service_name_zh(item)
        services[key] = {
            "name_zh": name_zh,
            "description_zh": description_zh,
            "category_zh": CATEGORY_ZH.get(str(item.get("category") or ""), str(item.get("category") or "未分类")),
        }
    for item in iter_json_items(rows, ["candidate_apis_json", "provisional_gold_apis_json"]):
        key = translation_key_api(item)
        if not key or key in apis:
            continue
        name_zh, description_zh = api_name_zh(item)
        apis[key] = {"name_zh": name_zh, "description_zh": description_zh}
    return {"queries": query_translations, "services": services, "apis": apis}


def b64_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Composable v0.3.3 任务必要性联合人工审核</title>
<style>
:root{--bg:#f4f7f9;--surface:#fff;--surface2:#f8fafb;--line:#d6dee4;--text:#1b2730;--muted:#65727c;--blue:#1769aa;--blue-soft:#e8f2fb;--green:#177245;--green-soft:#e9f7ef;--amber:#9a5d00;--amber-soft:#fff5df;--red:#ad2e2e;--red-soft:#fff0ef;--violet:#6654a3;--violet-soft:#f2effb;--shadow:0 1px 4px rgba(21,42,55,.08)}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{font-family:"Microsoft YaHei UI","Segoe UI",Arial,sans-serif;color:var(--text);background:var(--bg);font-size:14px;letter-spacing:0;overflow:hidden}button,input,textarea{font:inherit;letter-spacing:0}button{border:1px solid var(--line);background:var(--surface);color:var(--text);min-height:36px;padding:7px 11px;border-radius:6px;cursor:pointer}button:hover{border-color:#8ca6b7;background:#f1f6f9}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid rgba(23,105,170,.2);outline-offset:1px}.primary{background:var(--blue);border-color:var(--blue);color:#fff}.primary:hover{background:#12598f}.danger{color:var(--red);border-color:#e1adad}.app{height:100%;display:grid;grid-template-rows:auto 1fr}.topbar{display:flex;align-items:center;gap:16px;padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--line);box-shadow:var(--shadow);z-index:5}.title{min-width:0}.title h1{font-size:19px;margin:0 0 3px}.title p{margin:0;color:var(--muted);font-size:12px}.progress{margin-left:auto;display:flex;align-items:center;gap:10px;min-width:290px}.progress-track{height:8px;flex:1;background:#e7edf1;border-radius:4px;overflow:hidden}.progress-fill{height:100%;width:0;background:var(--green)}.progress-text{white-space:nowrap;font-weight:700}.workspace{min-height:0;display:grid;grid-template-columns:290px minmax(560px,1fr) 420px;gap:0}.sidebar,.main,.review{min-width:0;min-height:0;background:var(--surface)}.sidebar{border-right:1px solid var(--line);display:flex;flex-direction:column}.main{overflow:auto;background:var(--bg);padding:14px 16px 60px}.review{border-left:1px solid var(--line);overflow:auto;padding:12px 14px 80px}.sidebar-head{padding:12px;border-bottom:1px solid var(--line)}.search{width:100%;height:38px;border:1px solid var(--line);border-radius:6px;padding:0 10px;background:#fff}.filter-block{margin-top:10px}.filter-title{font-size:12px;color:var(--muted);margin-bottom:5px}.chip-row{display:flex;flex-wrap:wrap;gap:5px}.filter-chip{min-height:28px;padding:4px 8px;font-size:12px}.filter-chip.active{background:var(--blue-soft);border-color:#79a9d0;color:#114e7d;font-weight:700}.list-summary{padding:8px 12px;color:var(--muted);border-bottom:1px solid var(--line);font-size:12px}.sample-list{overflow:auto;min-height:0}.sample-item{display:block;width:100%;border:0;border-bottom:1px solid #e8edf0;border-radius:0;text-align:left;padding:10px 12px;min-height:80px;background:#fff}.sample-item:hover{background:#f5f9fc}.sample-item.active{background:#eaf3fb;box-shadow:inset 4px 0 var(--blue)}.sample-line{display:flex;align-items:center;gap:7px;margin-bottom:5px}.dot{width:9px;height:9px;border-radius:50%;background:#b7c2ca;flex:none}.dot.done{background:var(--green)}.sample-id{font-weight:700;overflow-wrap:anywhere}.group-tag{margin-left:auto;color:var(--muted);font-size:11px;border:1px solid var(--line);padding:1px 5px;border-radius:4px}.sample-query{color:#4f5e68;font-size:12px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.section{background:var(--surface);border:1px solid var(--line);border-radius:7px;margin:0 0 12px;box-shadow:var(--shadow)}.section-head{display:flex;align-items:center;gap:10px;padding:10px 12px;border-bottom:1px solid var(--line)}.section-head h2{font-size:16px;margin:0}.section-head .sub{margin-left:auto;color:var(--muted);font-size:12px}.section-body{padding:12px}.identity{display:flex;flex-wrap:wrap;gap:7px;align-items:center}.badge{display:inline-flex;align-items:center;min-height:24px;padding:3px 7px;border-radius:4px;background:#eef2f4;color:#3f505b;font-size:12px}.badge.gold{background:var(--amber-soft);color:#754500;border:1px solid #efd39c;font-weight:700}.badge.risk{background:var(--red-soft);color:var(--red)}.badge.good{background:var(--green-soft);color:var(--green)}.bilingual{display:grid;grid-template-columns:1fr 1fr;gap:10px}.language-pane{border:1px solid var(--line);border-radius:6px;padding:11px;min-width:0}.language-pane.zh{background:#eef9f3;border-color:#b9dfc9}.lang-label{font-size:12px;color:var(--muted);font-weight:700;margin-bottom:6px}.query-text{font-size:16px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere}.stat-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.stat{border-left:4px solid #9eb7c8;background:var(--surface2);padding:8px 10px;min-height:58px}.stat strong{display:block;font-size:19px}.stat span{color:var(--muted);font-size:12px}.audit-order{margin:0;padding-left:22px;line-height:1.9}.split-summary{display:grid;grid-template-columns:1fr 1fr;gap:10px}.level-box{border:1px solid var(--line);padding:10px;border-radius:6px}.level-box h3{font-size:14px;margin:0 0 7px}.level-box.service{border-left:4px solid var(--blue)}.level-box.api{border-left:4px solid var(--violet)}.hint-list{margin:0;padding-left:20px;line-height:1.7}.hint-list li{margin:4px 0}.hint-list .warn{color:var(--red);font-weight:700}.hint-list .caution{color:var(--amber);font-weight:700}.hint-list .ok{color:var(--green)}.hierarchy-controls{margin-left:auto;display:flex;gap:5px}.hierarchy-controls button.active{background:var(--blue-soft);border-color:#79a9d0;color:#114e7d;font-weight:700}.service-block{border:1px solid var(--line);border-radius:6px;margin:8px 0;background:#fff}.service-block.gold-service{border-color:#deb760;box-shadow:inset 4px 0 #d49a1f}.service-block>summary{list-style:none;cursor:pointer;padding:10px 12px;display:flex;align-items:flex-start;gap:8px}.service-block>summary::-webkit-details-marker{display:none}.service-main{min-width:0;flex:1}.service-name{font-weight:800;font-size:14px;overflow-wrap:anywhere}.service-zh{color:#165d3b;margin-top:3px;overflow-wrap:anywhere}.service-content{border-top:1px solid var(--line);padding:10px 12px}.description-pair{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:9px}.description{padding:8px;border-radius:5px;background:var(--surface2);line-height:1.55;overflow-wrap:anywhere}.description.zh{background:#eef9f3}.api-list{display:grid;grid-template-columns:1fr;gap:7px}.api-card{border:1px solid #e0e6ea;border-radius:5px;padding:8px 10px;background:#fbfcfd}.api-card.gold-api{border-color:#d8aa48;background:#fffbef}.api-title{display:flex;align-items:flex-start;gap:7px;font-weight:700}.api-title>span:first-child{overflow-wrap:anywhere}.api-zh{color:#165d3b;margin:4px 0}.api-meta{color:var(--muted);font-size:12px;overflow-wrap:anywhere}.warning{background:var(--red-soft);border:1px solid #efc0bd;color:#8a2424;padding:8px;border-radius:5px;margin:7px 0}.dependency-step{border-left:3px solid #7b9fb8;padding:7px 10px;margin:8px 0;background:#f8fafb}.dependency-step.gold-step{border-left-color:#d49a1f}.step-title{font-weight:800}.step-zh{color:#165d3b;margin:3px 0}.kv{display:grid;grid-template-columns:110px 1fr;gap:5px;font-size:12px;margin-top:4px}.kv dt{color:var(--muted)}.kv dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.edge{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:8px;border:1px solid #d6e0e7;background:#f5f9fb;padding:7px 9px;border-radius:5px;margin:6px 0}.edge-arrow{color:var(--blue);font-weight:900}.raw-details{margin:8px 0}.raw-details summary{cursor:pointer;font-weight:700}.raw-pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:Consolas,monospace;font-size:11px;background:#f7f9fa;border:1px solid var(--line);padding:8px;max-height:340px;overflow:auto}.review h2{font-size:16px;margin:0 0 8px}.review-note{background:var(--blue-soft);border-left:4px solid var(--blue);padding:9px;margin-bottom:10px;line-height:1.55}.guide summary{cursor:pointer;font-weight:800;padding:8px 0}.guide-body{font-size:13px;line-height:1.7;color:#3f4f59}.preset-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:8px 0 14px}.preset{min-height:68px;text-align:left;padding:8px}.preset strong{display:block;font-size:13px;margin-bottom:4px}.preset span{display:block;color:var(--muted);font-size:11px;line-height:1.35}.preset.keep{border-color:#86c5a4;background:var(--green-soft)}.preset.hold{border-color:#e3c178;background:var(--amber-soft)}.preset.remove{border-color:#e3aaaa;background:var(--red-soft)}.preset.reclass{border-color:#aaa0d0;background:var(--violet-soft)}.field-group{border-top:1px solid var(--line);padding:11px 0}.field-label{font-weight:800;margin-bottom:3px}.field-help{color:var(--muted);font-size:12px;line-height:1.45;margin-bottom:7px}.option-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px}.option-grid.release{grid-template-columns:1fr 1fr}.option{min-height:36px;padding:6px 5px;font-size:12px;line-height:1.25;overflow-wrap:anywhere}.option.selected{background:var(--blue);color:#fff;border-color:var(--blue);font-weight:700}.text-field{width:100%;border:1px solid var(--line);border-radius:6px;padding:8px;background:#fff}.text-field.notes{min-height:90px;resize:vertical}.eligibility-check{padding:9px;border-radius:5px;margin:8px 0;background:var(--surface2);border:1px solid var(--line);font-size:12px;line-height:1.6}.eligibility-check.bad{background:var(--red-soft);border-color:#efc0bd}.action-bar{position:sticky;bottom:-80px;background:rgba(255,255,255,.97);border-top:1px solid var(--line);margin:12px -14px -80px;padding:10px 14px 18px;box-shadow:0 -2px 8px rgba(21,42,55,.08)}.nav-row,.export-row{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:7px}.export-row{grid-template-columns:1fr 1fr 1fr}.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#1e2d36;color:#fff;padding:10px 16px;border-radius:6px;z-index:20;box-shadow:0 5px 18px rgba(0,0,0,.25);opacity:0;pointer-events:none;transition:opacity .18s}.toast.show{opacity:1}.empty{padding:24px;color:var(--muted);text-align:center}.hidden{display:none!important}
.necessity-principle{background:var(--amber-soft);border:1px solid #e4bf70;border-left:5px solid var(--amber);padding:10px 12px;margin-bottom:10px;line-height:1.65;font-weight:700}.necessity-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.necessity-card{background:var(--surface2);border:1px solid var(--line);border-radius:6px;padding:8px;min-height:62px}.necessity-card strong{display:block;font-size:15px;overflow-wrap:anywhere}.necessity-card span{display:block;color:var(--muted);font-size:11px;margin-top:3px}.necessity-card.risk{background:var(--red-soft);border-color:#efc0bd}.necessity-card.good{background:var(--green-soft);border-color:#b9dfc9}
.hint-list li{overflow-wrap:anywhere;word-break:break-word}
@media(max-width:1300px){.workspace{grid-template-columns:250px minmax(500px,1fr) 380px}.option-grid{grid-template-columns:1fr 1fr}.topbar{gap:10px}.progress{min-width:240px}}
@media(max-width:980px){body{overflow:auto}.app{height:auto;min-height:100%;width:100%;max-width:100vw}.workspace{display:block;width:100%;max-width:100vw;min-width:0}.sidebar,.main,.review{height:auto;overflow:visible;border:0;width:100%;max-width:100vw;min-width:0}.section,.section-body,.service-block,.service-content,.api-list,.api-card{min-width:0;max-width:100%}.api-title,.api-zh,.api-meta,.description,.kv dd,.hint-list li{overflow-wrap:anywhere;word-break:break-word}.kv{grid-template-columns:110px minmax(0,1fr)}.edge{grid-template-columns:auto minmax(0,1fr) auto}.sidebar{max-height:430px}.sample-list{max-height:260px}.topbar{position:sticky;top:0;flex-wrap:wrap;width:100%;max-width:100vw}.topbar .title{flex:1 1 100%}.bilingual,.split-summary,.description-pair{grid-template-columns:minmax(0,1fr)}.necessity-grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.review{border-top:1px solid var(--line)}.action-bar{bottom:0;margin-bottom:0}.progress{min-width:0;flex:1;margin-left:0}.title p{display:none}}
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <div class="title"><h1>Composable v0.3.3 任务必要性联合人工审核</h1><p>97 条结构合格候选（低于 100 条启动门槛） · 中文双语 · Task necessity + Service/API 联合判定 · 离线单文件</p></div>
    <button id="helpTop" type="button">审核说明</button>
    <div class="progress"><div class="progress-track"><div id="progressFill" class="progress-fill"></div></div><div id="progressText" class="progress-text">0 / 97</div></div>
  </header>
  <div class="workspace">
    <aside class="sidebar">
      <div class="sidebar-head">
        <input id="search" class="search" type="search" placeholder="搜索 ID、query、service、API">
        <div class="filter-block"><div class="filter-title">审核状态</div><div id="reviewFilters" class="chip-row"></div></div>
        <div class="filter-block"><div class="filter-title">来源组</div><div id="groupFilters" class="chip-row"></div></div>
        <div class="filter-block"><div class="filter-title">快速定位</div><div id="quickFilters" class="chip-row"></div></div>
      </div>
      <div id="listSummary" class="list-summary"></div>
      <div id="sampleList" class="sample-list"></div>
    </aside>
    <main id="main" class="main"></main>
    <aside id="review" class="review"></aside>
  </div>
</div>
<div id="toast" class="toast"></div>
<input id="importer" class="hidden" type="file" accept=".csv,text/csv">
<script>
"use strict";
const ROWS_B64="__ROWS_B64__";
const UI_B64="__UI_B64__";
const COLUMNS=__COLUMNS_JSON__;
const HUMAN_FIELDS=__HUMAN_FIELDS_JSON__;
const SOURCE_SHA256="__SOURCE_SHA256__";
const STORAGE_KEY="sdbench_composable_review_v033_"+SOURCE_SHA256.slice(0,16);
function decodeB64Json(payload){const bin=atob(payload),bytes=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);return JSON.parse(new TextDecoder().decode(bytes));}
const ROWS=decodeB64Json(ROWS_B64), UI=decodeB64Json(UI_B64);
const $=id=>document.getElementById(id);
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function parseList(raw){if(!raw)return[];try{const v=JSON.parse(raw);return Array.isArray(v)?v:[]}catch{return[]}}
function value(row,key){const d=decisions[row.review_item_id]||{};return Object.prototype.hasOwnProperty.call(d,key)?String(d[key]??""):String(row[key]??"")}
function nonempty(v){return String(v??"").trim()!==""}
function serviceKey(x){return String(x.service_key||x.service_name||"").trim()}
function apiKey(x){return serviceKey(x)+"::"+String(x.function_key||x.api_name||x.function_name||"").trim()}
function serviceTranslation(x){return UI.services[serviceKey(x)]||{name_zh:(x.service_name||"未命名服务")+"（中文能力待结合说明核对）",description_zh:"请依据英文原始说明核对该服务的能力边界。",category_zh:x.category||"未分类"}}
function apiTranslation(x){return UI.apis[apiKey(x)]||{name_zh:"执行该接口对应操作（"+(x.api_name||x.function_name||"未命名接口")+"）",description_zh:"请依据英文原始说明核对该接口的具体输入、输出和能力边界。"}}
let decisions={};try{decisions=JSON.parse(localStorage.getItem(STORAGE_KEY)||"{}")||{}}catch{decisions={}}
let currentId=ROWS[0].review_item_id;
let filtered=[];
let filters={review:"all",group:"all",quick:"all",query:""};
let hierarchyMode="all";
const searchCache=new Map();
function save(){localStorage.setItem(STORAGE_KEY,JSON.stringify(decisions));updateProgress();renderList();}
function toast(message){const t=$("toast");t.textContent=message;t.classList.add("show");clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.classList.remove("show"),1800)}
function isReviewed(row){return nonempty(value(row,"composable_release_action"))}
function reviewedCount(){return ROWS.filter(isReviewed).length}
function updateProgress(){const n=reviewedCount();$("progressText").textContent=n+" / "+ROWS.length;$("progressFill").style.width=(100*n/ROWS.length)+"%"}
function searchBlob(row){if(searchCache.has(row.review_item_id))return searchCache.get(row.review_item_id);const q=UI.queries[row.review_item_id]||"";const ss=parseList(row.candidate_services_json).map(x=>x.service_name+" "+(serviceTranslation(x).name_zh||"")).join(" ");const aa=parseList(row.candidate_apis_json).map(x=>x.api_name+" "+(apiTranslation(x).name_zh||"")).join(" ");const blob=[row.review_item_id,row.source_task_id,row.source_group,row.query_text,q,ss,aa].join(" ").toLowerCase();searchCache.set(row.review_item_id,blob);return blob}
function applyFilters(){const q=filters.query.trim().toLowerCase();filtered=ROWS.filter(row=>{if(filters.review==="done"&&!isReviewed(row))return false;if(filters.review==="pending"&&isReviewed(row))return false;if(filters.group!=="all"&&row.source_group!==filters.group)return false;if(filters.quick==="human"&&String(row.requires_human_dependency_confirmation).toLowerCase()!=="true")return false;if(filters.quick==="risk"&&!nonempty(row.dependency_structure_risk)&&!nonempty(row.query_chain_alignment_risk))return false;if(filters.quick==="service"&&value(row,"service_level_eligible")!=="true")return false;if(filters.quick==="api"&&value(row,"api_level_eligible")!=="true")return false;if(q&&!searchBlob(row).includes(q))return false;return true});if(!filtered.some(x=>x.review_item_id===currentId)&&filtered.length)currentId=filtered[0].review_item_id;renderList();renderCurrent()}
function filterButtons(target,items,key){const box=$(target);box.replaceChildren();items.forEach(([v,label])=>{const b=el("button","filter-chip"+(filters[key]===v?" active":""),label);b.type="button";b.onclick=()=>{filters[key]=v;filterButtons(target,items,key);applyFilters()};box.appendChild(b)})}
function renderFilters(){filterButtons("reviewFilters",[["all","全部"],["pending","未审核"],["done","已审核"]],"review");filterButtons("groupFilters",[["all","全部"],["G1","G1"],["G2","G2"],["G3","G3"]],"group");filterButtons("quickFilters",[["all","不限"],["human","需人审依赖"],["risk","有结构风险"],["service","服务层保留"],["api","API 层保留"]],"quick")}
function renderList(){const list=$("sampleList");list.replaceChildren();$("listSummary").textContent="显示 "+filtered.length+" 条 · 已审 "+reviewedCount()+" 条";if(!filtered.length){list.appendChild(el("div","empty","没有符合条件的样本"));return}filtered.forEach(row=>{const b=el("button","sample-item"+(row.review_item_id===currentId?" active":""));b.type="button";const line=el("div","sample-line");line.appendChild(el("span","dot"+(isReviewed(row)?" done":"")));const shortId=row.review_item_id.replace("COMPOSABLE-PAIRED-REVIEW-V0.3.3-","#").replace("COMPOSABLE-PAIRED-REVIEW-V0.3.2-","#");line.appendChild(el("span","sample-id",shortId));line.appendChild(el("span","group-tag",row.source_group+" · "+row.source_task_id));b.appendChild(line);b.appendChild(el("div","sample-query",UI.queries[row.review_item_id]||row.query_text));b.onclick=()=>{currentId=row.review_item_id;hierarchyMode="all";renderList();renderCurrent();$("main").scrollTop=0;$("review").scrollTop=0};list.appendChild(b)})}
function currentRow(){return ROWS.find(r=>r.review_item_id===currentId)||filtered[0]||ROWS[0]}
function section(title,sub){const s=el("section","section"),h=el("div","section-head");h.appendChild(el("h2","",title));if(sub)h.appendChild(el("span","sub",sub));s.appendChild(h);const body=el("div","section-body");s.appendChild(body);return[s,body,h]}
function badge(text,kind=""){return el("span","badge "+kind,text)}
function truncate(value,max=520){const text=typeof value==="string"?value:JSON.stringify(value,null,2);return text.length>max?text.slice(0,max)+" …":text}
function goldSets(row){const gs=parseList(row.provisional_gold_services_json),ga=parseList(row.provisional_gold_apis_json);return{services:gs,apis:ga,serviceKeys:new Set(gs.map(serviceKey)),serviceNames:new Set(gs.map(x=>x.service_name)),apiKeys:new Set(ga.map(apiKey)),apiNames:new Set(ga.map(x=>serviceKey(x)+"::"+x.api_name))}}
function isGoldService(x,g){return g.serviceKeys.has(serviceKey(x))||g.serviceNames.has(x.service_name)}
function isGoldApi(x,g){return g.apiKeys.has(apiKey(x))||g.apiNames.has(serviceKey(x)+"::"+x.api_name)}
function renderIdentity(row){const[s,b]=section("当前样本","联合审核的同一个 underlying task");const ids=el("div","identity");ids.append(badge(row.review_item_id));ids.append(badge(row.source_task_id));ids.append(badge(row.source_group));ids.append(badge("evidence: "+row.evidence_status,row.evidence_status==="sufficient"?"good":"risk"));ids.append(badge("score: "+row.evidence_score));ids.append(badge("component: "+row.connected_dependency_component_count));b.appendChild(ids);return s}
function renderQuery(row){const[s,b]=section("1. 先看用户需求 Query","中文为逐条直译，判断仍以英文原文为准");const grid=el("div","bilingual");const en=el("div","language-pane");en.appendChild(el("div","lang-label","英文原文"));en.appendChild(el("div","query-text",row.query_text));const zh=el("div","language-pane zh");zh.appendChild(el("div","lang-label","中文直译"));zh.appendChild(el("div","query-text",UI.queries[row.review_item_id]||"缺少中文翻译"));grid.append(en,zh);b.appendChild(grid);return s}
function renderStats(row){const[s,b]=section("2. Service/API 层级概览","先看能力层，再看具体接口层");const grid=el("div","stat-grid");[[row.candidate_service_count,"候选服务"],[row.gold_service_count,"gold 服务"],[row.candidate_api_count,"候选 API"],[row.gold_api_count,"gold API"]].forEach(([n,l])=>{const x=el("div","stat");x.appendChild(el("strong","",String(n)));x.appendChild(el("span","",l));grid.appendChild(x)});b.appendChild(grid);const split=el("div","split-summary");split.style.marginTop="10px";const sb=el("div","level-box service");sb.appendChild(el("h3","","Service-level：选哪些能力提供者"));sb.appendChild(el("div","","判断 query 需要哪些大能力；gold services 是否完整；候选服务是否形成真实选择空间。"));const ab=el("div","level-box api");ab.appendChild(el("h3","","API-level：选哪些具体接口"));ab.appendChild(el("div","","在对应服务下判断具体 endpoint；gold APIs、候选 API 和父服务映射是否正确。"));split.append(sb,ab);b.appendChild(split);return s}
function renderAuditOrder(){const[s,b]=section("3. 建议审核顺序","不确定时选择 uncertain，不要强行通过");const ol=el("ol","audit-order");["依赖是否是完成 query 所必需，而不只是 trace 中碰巧发生？","上游是否已经独立满足该子目标，导致下游只是重复计算？","gold chain 是否覆盖 query 的全部子目标？","是否还存在脱离依赖链的并列子目标？","strong edge 是否真的跨越不同 service？","后一步是否使用了前一步的新输出，而不是共享输入、重试或同源回显？","gold services/APIs 是否完整覆盖所需能力且父子映射正确？","候选 service/API 是否有真实选择空间且没有阻断泄露？","最后再决定两个层级是否 eligible 以及 release action。"].forEach(x=>ol.appendChild(el("li","",x)));b.appendChild(ol);return s}
function renderHints(row){const[s,b]=section("4. Rule-based Hints","只读机械提示，不会自动填写人工结论");const ul=el("ul","hint-list");const add=(text,kind="")=>ul.appendChild(el("li",kind,text));const cs=Number(row.candidate_service_count||0),gs=Number(row.gold_service_count||0),ca=Number(row.candidate_api_count||0),ga=Number(row.gold_api_count||0),se=parseList(row.dependency_edges_json).length,si=parseList(row.shared_input_values_json).length,fc=parseList(row.incidental_or_failed_calls_json).length;add(`candidate_service_count=${cs}, gold_service_count=${gs}`,cs>gs?"ok":"warn");add(cs>gs?"候选服务数大于 gold 服务数，服务层具备表面选择空间。":"候选服务数不大于 gold 服务数，服务发现可能缺少真实选择空间。",cs>gs?"ok":"warn");add(`candidate_api_count=${ca}, gold_api_count=${ga}`,ca>ga?"ok":"warn");add(ca>ga?"候选 API 数大于 gold API 数，API 层具备表面选择空间。":"候选 API 数不大于 gold API 数，API 推荐可能缺少真实选择空间。",ca>ga?"ok":"warn");add(`corrected strong edges=${se}; shared inputs=${si}; failed/error-only calls=${fc}`,(se>0&&fc===0)?"ok":"caution");add(`first edge roles: ${row.upstream_field_role||"?"} → ${row.downstream_field_role||"?"}; source type=${row.edge_source_type||"?"}`);if(si)add("检测到跨调用共享输入。它们单独展示，但不计入依赖证据。","caution");if(fc)add("trace 含失败或 error-only 调用；这些调用未进入 strong edge 和 provisional gold，请核对其是否影响任务完整性。","caution");if(String(row.requires_human_dependency_confirmation).toLowerCase()==="true")add("该样本仍需人工确认依赖链；机器 strong 不等于 human final。","caution");if(nonempty(row.dependency_structure_risk))add("依赖结构风险："+row.dependency_structure_risk,"warn");if(nonempty(row.query_chain_alignment_risk))add("query-chain 对齐风险："+row.query_chain_alignment_risk,"warn");add("service leak detector: "+row.service_leak_status+(nonempty(row.service_leak_signals_json)?"；请展开原始信号核对":""),row.service_leak_status.includes("no_")?"ok":"caution");add("API leak detector: "+row.api_leak_status+(nonempty(row.api_leak_signals_json)?"；请展开原始信号核对":""),row.api_leak_status.includes("no_")?"ok":"caution");add("G3、调用顺序或共享参数都不等于 true_composable；必须看到上游新输出影响后续输入、选择或判断。","caution");b.appendChild(ul);return s}
function renderNecessity(row){const[s,b]=section("5. Task-level Necessity Gate（任务必要性门槛）","机器预筛只负责排除明显不合格结构，不能代替人工语义判断");const principle=el("div","necessity-principle");principle.appendChild(el("div","","A valid trace edge is not sufficient. The dependency must be necessary to satisfy the user query."));principle.appendChild(el("div","","Shared input, retry, redundant recomputation, and parallel subgoals are not true composition."));principle.appendChild(el("div","","有效 trace 边还不够：这条依赖必须是完成用户 query 所必需。共享输入、重试、重复计算和并列子目标都不是真组合。"));b.appendChild(principle);const grid=el("div","necessity-grid");const card=(label,val,kind="")=>{const c=el("div","necessity-card"+(kind?" "+kind:""));c.appendChild(el("strong","",String(val??"")));c.appendChild(el("span","",label));grid.appendChild(c)};const hard=String(row.structural_hard_gate_pass).toLowerCase()==="true";card("machine_review_status",row.machine_review_status,hard?"good":"risk");card("structural_hard_gate_pass",row.structural_hard_gate_pass,hard?"good":"risk");card("distinct gold services / APIs",`${row.distinct_gold_service_count||0} / ${row.distinct_gold_api_count||0}`,Number(row.distinct_gold_service_count)>=2&&Number(row.distinct_gold_api_count)>=2?"good":"risk");card("cross-service / same-service strong edges",`${row.cross_service_strong_edge_count||0} / ${row.same_service_strong_edge_count||0}`,Number(row.cross_service_strong_edge_count)>0?"good":"risk");card("failed/error gold calls / failed dependency",`${row.failed_or_error_gold_call_count||0} / ${row.failed_call_dependency_count||0}`,Number(row.failed_or_error_gold_call_count)===0&&Number(row.failed_call_dependency_count)===0?"good":"risk");card("disconnected query-relevant calls",row.disconnected_query_relevant_call_count||0,Number(row.disconnected_query_relevant_call_count)===0?"good":"risk");card("possible redundant recomputation",row.possible_redundant_recomputation,String(row.possible_redundant_recomputation).toLowerCase()==="true"?"risk":"good");card("upstream already returns requested result",row.upstream_already_returns_requested_result,String(row.upstream_already_returns_requested_result).toLowerCase()==="true"?"risk":"good");card("downstream adds new required information",row.downstream_adds_new_required_information,String(row.downstream_adds_new_required_information).toLowerCase()==="false"?"risk":"");card("parallel / hybrid / incomplete-gold risk",`${row.parallel_subgoal_risk} / ${row.hybrid_composable_multi_risk} / ${row.possible_incomplete_gold_chain}`,String(row.parallel_subgoal_risk).toLowerCase()==="true"||String(row.hybrid_composable_multi_risk).toLowerCase()==="true"||String(row.possible_incomplete_gold_chain).toLowerCase()==="true"?"risk":"good");card("exact service / API leak",`${row.exact_gold_service_name_leak} / ${row.exact_gold_api_name_leak}`,String(row.exact_gold_service_name_leak).toLowerCase()==="true"||String(row.exact_gold_api_name_leak).toLowerCase()==="true"?"risk":"good");card("repeated result type",truncate(row.repeated_result_type_json,100));b.appendChild(grid);const ul=el("ul","hint-list");[["结构不合格原因",row.structural_ineligibility_reasons_json],["必要性风险",row.necessity_risk_reason_json],["冗余证据",row.redundancy_evidence_json],["脱离依赖链的 query 相关调用",row.disconnected_query_relevant_calls_json]].forEach(([label,val])=>{if(nonempty(val)&&val!=="[]")ul.appendChild(el("li","caution",label+"："+truncate(val,520)))});ul.appendChild(el("li","warn","Query 的全部子目标是否由 gold chain 覆盖，必须由人工阅读 query 与链路后判断；机器字段不能自动给出最终答案。"));b.appendChild(ul);return s}
function renderDependency(row){const steps=parseList(row.ordered_steps_json),edges=parseList(row.dependency_edges_json),shared=parseList(row.shared_input_values_json),failed=parseList(row.incidental_or_failed_calls_json),g=goldSets(row);const[s,b]=section("6. 修正后的依赖链证据","只展示 role-valid strong edges；argument → argument 不会显示为依赖边");if(!steps.length)b.appendChild(el("div","warning","没有可展示的 ordered steps。"));steps.forEach((step,i)=>{const key=String(step.service_name||"")+"::"+String(step.function_name||step.api_name||"");let api=parseList(row.provisional_gold_apis_json).find(x=>apiKey(x)===key||x.function_name===step.function_name)||{service_name:step.service_name,service_key:step.service_name,function_key:step.function_name,api_name:step.api_name||step.function_name,api_description:""};const tr=apiTranslation(api),box=el("div","dependency-step"+(isGoldApi(api,g)?" gold-step":""));box.appendChild(el("div","step-title",`Step ${step.step_index||i+1}: ${step.service_name||"?"} / ${step.api_name||step.function_name||"?"}`));box.appendChild(el("div","step-zh",tr.name_zh));const status=step.call_execution_status||"unknown";box.appendChild(badge("调用状态: "+status,status==="success"||status==="partial_success"?"good":"risk"));const dl=el("dl","kv");[["输入参数",truncate(step.arguments,360)],["输出摘要",truncate(step.outputs||step.observation,500)],["请求路径",step.argument_source_path||step.source_json_path||""],["输出路径",step.output_source_path||""]].forEach(([k,v])=>{dl.appendChild(el("dt","",k));dl.appendChild(el("dd","",v))});box.appendChild(dl);b.appendChild(box)});b.appendChild(el("h3","","Machine-proposed dependency edge"));if(!edges.length)b.appendChild(el("div","warning","没有修正后可作为 strong 的依赖边；该样本不应进入本审核包。"));edges.forEach(edge=>{const box=el("div","dependency-step gold-step");box.appendChild(el("div","step-title",`Step ${edge.from_step} → Step ${edge.to_step} · ${edge.edge_source_type||"unknown"}`));const dl=el("dl","kv");[["From step",edge.from_step],["Upstream field role",edge.upstream_field_role],["Upstream JSON path",edge.upstream_source_path],["Upstream value",edge.upstream_value||edge.evidence_value],["To step",edge.to_step],["Downstream field role",edge.downstream_field_role],["Downstream JSON path",edge.downstream_source_path],["Downstream value",edge.downstream_value],["Edge source type",edge.edge_source_type],["Query-known value",String(edge.value_present_in_original_query)],["Upstream-input echo",String(edge.upstream_output_is_echo)],["Upstream call status",edge.upstream_call_execution_status],["Downstream call status",edge.downstream_call_execution_status],["Strong edge eligible",String(edge.strong_edge_eligible)]].forEach(([k,v])=>{dl.appendChild(el("dt","",k));dl.appendChild(el("dd","",truncate(v,420)))});box.appendChild(dl);b.appendChild(box)});b.appendChild(el("h3","","Shared inputs across calls"));b.appendChild(el("div","warning","Shared input is not dependency evidence. 共享的经纬度、城市、日期、语言或其他原始参数不能仅因重复出现就算作前后依赖。"));if(shared.length){const ul=el("ul","hint-list");shared.forEach(value=>ul.appendChild(el("li","caution",String(value))));b.appendChild(ul)}else b.appendChild(el("div","api-meta","当前样本没有记录到跨调用共享输入。"));if(failed.length){b.appendChild(el("h3","","Incidental or failed calls"));failed.forEach(call=>b.appendChild(el("div","warning",`Step ${call.step_index}: ${call.service_name||"?"} / ${call.api_name||"?"} · ${call.call_execution_status||"unknown"}`)))}return s}
function hierarchyControls(head){const box=el("div","hierarchy-controls");[["all","全部候选"],["gold","只看 gold"]].forEach(([v,l])=>{const b=el("button",hierarchyMode===v?"active":"",l);b.type="button";b.onclick=()=>{hierarchyMode=v;renderCurrent()};box.appendChild(b)});head.appendChild(box)}
function renderHierarchy(row){const candidates=parseList(row.candidate_services_json),goldServices=parseList(row.provisional_gold_services_json),candidateApis=parseList(row.candidate_apis_json),goldApis=parseList(row.provisional_gold_apis_json),g=goldSets(row);const[s,b,h]=section("6. Service/API Hierarchy View","按父服务分组，不需要从 JSON 里硬分");hierarchyControls(h);const services=[...candidates];goldServices.forEach(x=>{if(!services.some(y=>serviceKey(y)===serviceKey(x)))services.push(x)});const serviceKeys=new Set(services.map(serviceKey));const allApis=[...candidateApis];goldApis.forEach(x=>{if(!allApis.some(y=>apiKey(y)===apiKey(x)))allApis.push(x)});const missing=allApis.filter(x=>!serviceKeys.has(serviceKey(x)));if(missing.length)b.appendChild(el("div","warning","WARNING: API service name not found in candidate_services_json（"+missing.length+" 条）。请重点核对父服务映射。"));services.filter(x=>hierarchyMode==="all"||isGoldService(x,g)).forEach(service=>{const gold=isGoldService(service,g),tr=serviceTranslation(service),details=el("details","service-block"+(gold?" gold-service":""));details.open=gold;const sum=el("summary","");const main=el("div","service-main");const name=el("div","service-name",`Service: ${service.service_name||service.service_key||"?"}`);if(gold)name.appendChild(badge("GOLD_SERVICE","gold"));main.appendChild(name);main.appendChild(el("div","service-zh","中文："+tr.name_zh));main.appendChild(el("div","api-meta",`类别：${service.category||"未分类"} / ${tr.category_zh||"未分类"}`));sum.appendChild(main);details.appendChild(sum);const content=el("div","service-content");const desc=el("div","description-pair");desc.appendChild(el("div","description","英文说明："+(service.service_description||"原始 catalog 未提供说明")));desc.appendChild(el("div","description zh","中文能力："+tr.description_zh));content.appendChild(desc);const apis=allApis.filter(x=>serviceKey(x)===serviceKey(service)).filter(x=>hierarchyMode==="all"||isGoldApi(x,g));if(!apis.length)content.appendChild(el("div","api-meta","该服务下没有当前模式可展示的 API。"));const list=el("div","api-list");apis.forEach(api=>{const ag=isGoldApi(api,g),at=apiTranslation(api),card=el("div","api-card"+(ag?" gold-api":""));const title=el("div","api-title");title.appendChild(el("span","",`API: ${api.api_name||api.function_name||api.function_key||"?"}`));if(ag)title.appendChild(badge("GOLD_API","gold"));card.appendChild(title);card.appendChild(el("div","api-zh","中文："+at.name_zh));card.appendChild(el("div","api-meta","中文能力："+at.description_zh));card.appendChild(el("div","api-meta","英文说明："+(api.api_description||"原始 catalog 未提供说明")));card.appendChild(el("div","api-meta",`父服务：${api.service_name||api.service_key||"?"} · 方法：${api.method||"?"} · function_key：${api.function_key||"?"}`));list.appendChild(card)});content.appendChild(list);details.appendChild(content);b.appendChild(details)});return s}
function renderRaw(row){const[s,b]=section("7. 原始证据与溯源","默认折叠，必要时再展开");[["dependency_evidence_json",row.dependency_evidence_json],["dependency_components_json",row.dependency_components_json],["service_leak_signals_json",row.service_leak_signals_json],["api_leak_signals_json",row.api_leak_signals_json],["source/provenance",JSON.stringify({query_source_path:row.query_source_path,source_trace_path:row.source_trace_path,source_answer_path:row.source_answer_path,source_record_path:row.source_record_path,catalog_domain_signature:row.catalog_domain_signature,review_content_hash:row.review_content_hash},null,2)]].forEach(([title,raw])=>{const d=el("details","raw-details");d.appendChild(el("summary","",title));let pretty=raw;try{pretty=JSON.stringify(JSON.parse(raw),null,2)}catch{}d.appendChild(el("pre","raw-pre",pretty||"空"));b.appendChild(d)});return s}
function renderCurrent(){const row=currentRow(),main=$("main");main.replaceChildren();if(!row){main.appendChild(el("div","empty","没有样本"));return}main.append(renderIdentity(row),renderQuery(row),renderStats(row),renderAuditOrder(),renderHints(row),renderNecessity(row),renderDependency(row),renderHierarchy(row),renderRaw(row));renderReview(row)}
const FIELD_DEFS=[
 {group:"任务必要性（先判）",key:"dependency_required_for_query",label:"该依赖是否为完成 Query 所必需",help:"即使 trace 边有效，也要判断没有这条跨服务依赖时是否无法满足用户要求。共享输入、调用顺序和关键词都不够。",options:[["true","必需"],["false","非必需"],["uncertain","不确定"]]},
 {group:"任务必要性（先判）",key:"upstream_already_satisfies_subgoal",label:"上游是否已经满足该子目标",help:"若上游输出已直接完成该子目标，下游只是重复距离、格式化或同义计算，应选 true，并阻止其作为真组合。",options:[["true","已满足"],["false","未满足"],["uncertain","不确定"]]},
 {group:"任务必要性（先判）",key:"full_query_subgoals_covered_by_gold_chain",label:"Gold chain 是否覆盖 Query 全部子目标",help:"逐项对照 query；任何核心子目标遗漏、能力错配或只有 support-list endpoint，都不能选 true。",options:[["true","全部覆盖"],["false","未全部覆盖"],["uncertain","不确定"]]},
 {group:"任务必要性（先判）",key:"disconnected_parallel_subgoals_present",label:"是否存在脱离依赖链的并列子目标",help:"若 query 还要求独立翻译、搜索、天气等不参与依赖链的子任务，选 true；这通常是 hybrid 或 ordinary multi 风险。",options:[["true","存在"],["false","不存在"],["uncertain","不确定"]]},
 {group:"任务必要性（先判）",key:"cross_service_dependency_valid",label:"跨服务依赖是否有效",help:"上游和下游必须属于不同 service，且下游确实消费上游的新输出；同一 service 内 API 链只能是 API-only workflow。",options:[["true","有效"],["false","无效"],["uncertain","不确定"]]},
 {group:"依赖与组合",key:"dependency_edge_valid",label:"依赖边是否有效",help:"后一步输入、选择或判断是否真正使用了前一步输出？query 中本来就有的重复值不能算依赖。",options:[["true","有效"],["false","无效"],["uncertain","不确定"]]},
 {group:"依赖与组合",key:"dependency_evidence_sufficient",label:"依赖证据是否充分",help:"source path、复用值和上下游记录是否足以验证该依赖，而不是只凭关键词猜测？",options:[["true","充分"],["false","不充分"],["uncertain","不确定"]]},
 {group:"依赖与组合",key:"composition_final_label",label:"组合任务最终标签",help:"true_composable 要求前一步输出影响后一步；parallel_multi 只是并列；hybrid 同时含依赖链和独立调用。",options:[["true_composable","真组合"],["parallel_multi","并列多任务"],["hybrid_composable_multi","混合型"],["insufficient_evidence","证据不足"],["invalid_task","无效任务"]]},
 {group:"依赖与组合",key:"query_gold_chain_alignment",label:"Query 与 gold 依赖链对齐",help:"gold 服务/API 及其依赖顺序是否覆盖 query 的核心要求？不能只看 trace 能运行。",options:[["aligned","对齐"],["partially_aligned","部分对齐"],["misaligned","不对齐"],["uncertain","不确定"]]},
 {group:"Service-level",key:"service_gold_complete",label:"Gold services 是否完整",help:"是否包含完成 query 中所有必要大能力的服务，且没有错误或多余 gold？",options:[["true","完整"],["false","不完整"],["uncertain","不确定"]]},
 {group:"Service-level",key:"service_candidate_space_valid",label:"候选服务空间是否有效",help:"候选服务是否包含 gold 和合理负例，形成真实选择空间？只有一个或全是 gold 通常无效。",options:[["true","有效"],["false","无效"],["uncertain","不确定"]]},
 {group:"Service-level",key:"service_leakage_final",label:"Service leak 最终判断",help:"query 是否直接点名或唯一暴露 gold service？品牌/专名需结合语境判断。",options:[["no_blocking_leak","无阻断泄露"],["blocking_leak","阻断泄露"],["uncertain","不确定"]]},
 {group:"Service-level",key:"service_level_eligible",label:"服务层是否可进入主任务",help:"只有真组合、依赖与语义对齐、gold 完整、候选有效且无阻断泄露时才可为 true。",options:[["true","可进入"],["false","不可进入"]]},
 {group:"API-level",key:"api_gold_complete",label:"Gold APIs 是否完整",help:"gold API 是否覆盖 query 中每个具体操作，并且没有 dummy、support-list 或能力不匹配接口？",options:[["true","完整"],["false","不完整"],["uncertain","不确定"]]},
 {group:"API-level",key:"api_candidate_space_valid",label:"候选 API 空间是否有效",help:"是否包含 gold 和足够合理负例，而不是只有 gold、全选或无可比接口？",options:[["true","有效"],["false","无效"],["uncertain","不确定"]]},
 {group:"API-level",key:"api_parent_mapping_valid",label:"API 父服务映射是否正确",help:"每个 gold/candidate API 是否挂在正确 service 下？名称相似不能替代实际父子关系。",options:[["true","正确"],["false","错误"],["uncertain","不确定"]]},
 {group:"API-level",key:"api_leakage_final",label:"API leak 最终判断",help:"query 是否直接出现 gold endpoint、函数名、路径或强绑定技术名称？通用自然语言词不要机械判强泄露。",options:[["no_blocking_leak","无阻断泄露"],["blocking_leak","阻断泄露"],["uncertain","不确定"]]},
 {group:"API-level",key:"api_level_eligible",label:"API 层是否可进入主任务",help:"只有真组合、语义对齐、gold 完整、候选有效、父映射正确且无阻断泄露时才可为 true。",options:[["true","可进入"],["false","不可进入"]]},
 {group:"发布动作",key:"composable_release_action",label:"最终 release action",help:"决定该 underlying task 在两个层级如何处理；这是判定审核完成状态的主字段。",release:true,options:[["keep_both_levels","两个层级均保留"],["keep_service_only","仅保留服务层"],["reconstruct_api_then_reaudit","重构 API 后复审"],["reconstruct_service_then_reaudit","重构服务后复审"],["rewrite_query_then_reaudit","改写 query 后复审"],["reclassify_as_multi","改归普通 multi"],["hold","暂缓"],["remove","移除"]]},
 {group:"发布动作",key:"adjudicator_type",label:"审核方式",help:"人工独立确认、人工借助模型辅助，或仅为模型试标。正式人审不要误选 model_pilot_only。",options:[["human_confirmed","人工确认"],["human_with_model_assistance","人工+模型辅助"],["model_pilot_only","仅模型试标"]]}
];
const PRESETS=[
 {cls:"keep",title:"全部符合：两个层级保留",desc:"真组合、对齐、无泄露；填写并下一条",note:"真组合且 service/API 两层均满足冻结资格规则",fields:{dependency_edge_valid:"true",dependency_evidence_sufficient:"true",composition_final_label:"true_composable",query_gold_chain_alignment:"aligned",service_gold_complete:"true",service_candidate_space_valid:"true",service_leakage_final:"no_blocking_leak",service_level_eligible:"true",api_gold_complete:"true",api_candidate_space_valid:"true",api_parent_mapping_valid:"true",api_leakage_final:"no_blocking_leak",api_level_eligible:"true",composable_release_action:"keep_both_levels",adjudicator_type:"human_confirmed"}},
 {cls:"keep",title:"服务层合格，API 需重构",desc:"真组合，service 可保留；API gold 不完整",note:"服务层合格，但 API gold 需要重构后复审",fields:{dependency_edge_valid:"true",dependency_evidence_sufficient:"true",composition_final_label:"true_composable",query_gold_chain_alignment:"aligned",service_gold_complete:"true",service_candidate_space_valid:"true",service_leakage_final:"no_blocking_leak",service_level_eligible:"true",api_gold_complete:"false",api_candidate_space_valid:"true",api_parent_mapping_valid:"true",api_leakage_final:"no_blocking_leak",api_level_eligible:"false",composable_release_action:"reconstruct_api_then_reaudit",adjudicator_type:"human_confirmed"}},
 {cls:"reclass",title:"并列多任务：改归 multi",desc:"多个子任务互不依赖；填写并下一条",note:"多个子任务并列，没有跨步骤输出依赖",fields:{dependency_edge_valid:"false",dependency_evidence_sufficient:"true",composition_final_label:"parallel_multi",query_gold_chain_alignment:"aligned",service_gold_complete:"true",service_candidate_space_valid:"true",service_leakage_final:"no_blocking_leak",service_level_eligible:"false",api_gold_complete:"true",api_candidate_space_valid:"true",api_parent_mapping_valid:"true",api_leakage_final:"no_blocking_leak",api_level_eligible:"false",composable_release_action:"reclassify_as_multi",adjudicator_type:"human_confirmed"}},
 {cls:"hold",title:"依赖证据不足：暂缓",desc:"关键事实无法确认；填写并下一条",note:"依赖关系或证据不足，暂不进入发布池",fields:{dependency_edge_valid:"uncertain",dependency_evidence_sufficient:"uncertain",composition_final_label:"insufficient_evidence",query_gold_chain_alignment:"uncertain",service_gold_complete:"uncertain",service_candidate_space_valid:"uncertain",service_leakage_final:"uncertain",service_level_eligible:"false",api_gold_complete:"uncertain",api_candidate_space_valid:"uncertain",api_parent_mapping_valid:"uncertain",api_leakage_final:"uncertain",api_level_eligible:"false",composable_release_action:"hold",adjudicator_type:"human_confirmed"}},
 {cls:"remove",title:"Query/gold 不对齐：移除",desc:"核心能力不匹配；填写并下一条",note:"query 与 gold 依赖链或核心能力不对齐",fields:{dependency_edge_valid:"uncertain",dependency_evidence_sufficient:"true",composition_final_label:"invalid_task",query_gold_chain_alignment:"misaligned",service_gold_complete:"false",service_candidate_space_valid:"uncertain",service_leakage_final:"uncertain",service_level_eligible:"false",api_gold_complete:"false",api_candidate_space_valid:"uncertain",api_parent_mapping_valid:"uncertain",api_leakage_final:"uncertain",api_level_eligible:"false",composable_release_action:"remove",adjudicator_type:"human_confirmed"}},
 {cls:"hold",title:"存在 Service leak：改写复审",desc:"能力可满足，但服务名阻断泄露",note:"服务层存在阻断泄露，query 需改写后复审",fields:{dependency_edge_valid:"true",dependency_evidence_sufficient:"true",composition_final_label:"true_composable",query_gold_chain_alignment:"aligned",service_gold_complete:"true",service_candidate_space_valid:"true",service_leakage_final:"blocking_leak",service_level_eligible:"false",api_gold_complete:"true",api_candidate_space_valid:"true",api_parent_mapping_valid:"true",api_leakage_final:"no_blocking_leak",api_level_eligible:"true",composable_release_action:"rewrite_query_then_reaudit",adjudicator_type:"human_confirmed"}},
 {cls:"hold",title:"存在 API leak：仅留服务层",desc:"服务层可用，API 层阻断泄露",note:"API 层存在阻断泄露，仅保留服务层",fields:{dependency_edge_valid:"true",dependency_evidence_sufficient:"true",composition_final_label:"true_composable",query_gold_chain_alignment:"aligned",service_gold_complete:"true",service_candidate_space_valid:"true",service_leakage_final:"no_blocking_leak",service_level_eligible:"true",api_gold_complete:"true",api_candidate_space_valid:"true",api_parent_mapping_valid:"true",api_leakage_final:"blocking_leak",api_level_eligible:"false",composable_release_action:"keep_service_only",adjudicator_type:"human_confirmed"}},
 {cls:"remove",title:"无效任务：移除",desc:"依赖无效且 query/gold 不可用",note:"任务无效，移除",fields:{dependency_edge_valid:"false",dependency_evidence_sufficient:"false",composition_final_label:"invalid_task",query_gold_chain_alignment:"misaligned",service_gold_complete:"false",service_candidate_space_valid:"false",service_leakage_final:"uncertain",service_level_eligible:"false",api_gold_complete:"false",api_candidate_space_valid:"false",api_parent_mapping_valid:"false",api_leakage_final:"uncertain",api_level_eligible:"false",composable_release_action:"remove",adjudicator_type:"human_confirmed"}}
];
function setField(row,key,val){if(!decisions[row.review_item_id])decisions[row.review_item_id]={};decisions[row.review_item_id][key]=val;decisions[row.review_item_id].adjudicated_at=new Date().toISOString().slice(0,19);save()}
function makeField(row,def){const box=el("div","field-group");box.appendChild(el("div","field-label",def.label));box.appendChild(el("div","field-help",def.help));const opts=el("div","option-grid"+(def.release?" release":""));def.options.forEach(([v,l])=>{const b=el("button","option"+(value(row,def.key)===v?" selected":""),l);b.type="button";b.title=v;b.onclick=()=>{setField(row,def.key,v);renderReview(row)};opts.appendChild(b)});box.appendChild(opts);return box}
function textField(row,key,label,notes=false,placeholder=""){const box=el("div","field-group");box.appendChild(el("div","field-label",label));const input=notes?document.createElement("textarea"):document.createElement("input");input.className="text-field"+(notes?" notes":"");input.value=value(row,key);input.placeholder=placeholder;input.oninput=()=>{if(!decisions[row.review_item_id])decisions[row.review_item_id]={};decisions[row.review_item_id][key]=input.value;localStorage.setItem(STORAGE_KEY,JSON.stringify(decisions))};input.onchange=()=>{save();renderReview(row)};box.appendChild(input);return box}
function presetPanel(row){const wrap=el("div","");wrap.appendChild(el("h2","","快捷审核方案（点击后自动下一条）"));wrap.appendChild(el("div","field-help","预设只是批量填写工具。点击前仍须看 query、依赖链和 gold；页面不会根据 hints 自动替你决定。"));const grid=el("div","preset-grid");PRESETS.forEach(p=>{const b=el("button","preset "+p.cls);b.type="button";b.appendChild(el("strong","",p.title));b.appendChild(el("span","",p.desc));b.onclick=()=>applyPreset(row,p);grid.appendChild(b)});wrap.appendChild(grid);return wrap}
function necessityPresetFields(preset){const f=preset.fields;if(f.composition_final_label==="parallel_multi")return{dependency_required_for_query:"false",upstream_already_satisfies_subgoal:"uncertain",full_query_subgoals_covered_by_gold_chain:"true",disconnected_parallel_subgoals_present:"true",cross_service_dependency_valid:"false"};if(f.composition_final_label==="insufficient_evidence")return{dependency_required_for_query:"uncertain",upstream_already_satisfies_subgoal:"uncertain",full_query_subgoals_covered_by_gold_chain:"uncertain",disconnected_parallel_subgoals_present:"uncertain",cross_service_dependency_valid:"uncertain"};if(f.composition_final_label==="invalid_task")return{dependency_required_for_query:"false",upstream_already_satisfies_subgoal:"uncertain",full_query_subgoals_covered_by_gold_chain:"false",disconnected_parallel_subgoals_present:"uncertain",cross_service_dependency_valid:"false"};return{dependency_required_for_query:"true",upstream_already_satisfies_subgoal:"false",full_query_subgoals_covered_by_gold_chain:"true",disconnected_parallel_subgoals_present:"false",cross_service_dependency_valid:"true"}}
function applyPreset(row,preset){if(!decisions[row.review_item_id])decisions[row.review_item_id]={};Object.assign(decisions[row.review_item_id],necessityPresetFields(preset),preset.fields);decisions[row.review_item_id].adjudicated_at=new Date().toISOString().slice(0,19);const old=String(decisions[row.review_item_id].adjudication_notes||row.adjudication_notes||"").trim();decisions[row.review_item_id].adjudication_notes=(old?old+"\n":"")+"快捷预设："+preset.note;save();toast("已填写："+preset.title);go(1,true)}
function eligibilityStatus(row){const necessityReq={dependency_required_for_query:"true",upstream_already_satisfies_subgoal:"false",full_query_subgoals_covered_by_gold_chain:"true",disconnected_parallel_subgoals_present:"false",cross_service_dependency_valid:"true"};const serviceReq={...necessityReq,dependency_edge_valid:"true",dependency_evidence_sufficient:"true",composition_final_label:"true_composable",query_gold_chain_alignment:"aligned",service_gold_complete:"true",service_candidate_space_valid:"true",service_leakage_final:"no_blocking_leak"};const apiReq={...necessityReq,dependency_edge_valid:"true",dependency_evidence_sufficient:"true",composition_final_label:"true_composable",query_gold_chain_alignment:"aligned",api_gold_complete:"true",api_candidate_space_valid:"true",api_parent_mapping_valid:"true",api_leakage_final:"no_blocking_leak"};const missing=req=>Object.entries(req).filter(([k,v])=>value(row,k)!==v).map(([k])=>k);return{service:missing(serviceReq),api:missing(apiReq)}}
function eligibilityBox(row){const st=eligibilityStatus(row),serviceClaim=value(row,"service_level_eligible"),apiClaim=value(row,"api_level_eligible"),bad=(serviceClaim==="true"&&st.service.length)||(apiClaim==="true"&&st.api.length);const box=el("div","eligibility-check"+(bad?" bad":""));box.appendChild(el("strong","",bad?"资格字段存在规则冲突":"冻结资格规则即时核对"));box.appendChild(el("div","",st.service.length?"服务层尚未满足："+st.service.join(", "):"服务层前置条件已满足"));box.appendChild(el("div","",st.api.length?"API 层尚未满足："+st.api.join(", "):"API 层前置条件已满足"));box.appendChild(el("div","api-meta","这只是冲突提示，不会自动修改人工字段。"));return box}
function guide(){const d=el("details","guide");d.id="guide";d.appendChild(el("summary","","审核说明与判定边界"));const b=el("div","guide-body");b.innerHTML="<b>先做 Task necessity gate：</b>有效 trace 边不等于真组合。依赖必须是完成 query 所必需，上游不能已经独立满足子目标，gold chain 必须覆盖全部子目标，且不能夹带脱离链路的并列需求。<br><b>Service-level</b> 判断需要哪些工具/服务；<b>API-level</b> 判断这些服务下需要哪些具体接口。<br><b>true_composable</b> 必须能证明不同 service 之间，前一步新输出影响后一步输入、选择或判断；同服务 API 链、多个独立需求、共享输入、重试和冗余重算都不满足。<br>之后再审 gold 完整性、候选空间、父服务映射和 leak。边界不清时选 uncertain 或 hold，不要强行 keep。";d.appendChild(b);return d}
function renderReview(row){const p=$("review");p.replaceChildren();p.appendChild(el("div","review-note","这里只填写人工字段。Rule-based hints、旧 detector 和 provisional gold 都不能自动成为 human final。"));p.appendChild(guide());p.appendChild(presetPanel(row));let last="";FIELD_DEFS.forEach(def=>{if(def.group!==last){p.appendChild(el("h2","",def.group));last=def.group}p.appendChild(makeField(row,def))});p.appendChild(eligibilityBox(row));p.appendChild(textField(row,"adjudicator_id","审核人 ID",false,"例如：CpeterX"));p.appendChild(textField(row,"adjudication_notes","人工备注",true,"写明依赖、语义、gold、候选或泄露的关键理由"));const bar=el("div","action-bar");const nav=el("div","nav-row");const prev=el("button","","← 上一条");prev.onclick=()=>go(-1);const next=el("button","primary","下一条 →");next.onclick=()=>go(1);nav.append(prev,next);bar.appendChild(nav);const exp=el("div","export-row");const ex=el("button","primary","导出完整 CSV");ex.onclick=()=>exportCsv(false);const exf=el("button","","导出当前筛选");exf.onclick=()=>exportCsv(true);const im=el("button","","导入 CSV");im.onclick=()=>$("importer").click();exp.append(ex,exf,im);bar.appendChild(exp);const clr=el("div","nav-row");const cc=el("button","danger","清空当前");cc.onclick=()=>clearCurrent(row);const ca=el("button","danger","清空全部");ca.onclick=clearAll;clr.append(cc,ca);bar.appendChild(clr);p.appendChild(bar)}
function go(delta,fromPreset=false){const pool=filtered.length?filtered:ROWS;let i=pool.findIndex(x=>x.review_item_id===currentId);if(i<0)i=0;let ni=Math.max(0,Math.min(pool.length-1,i+delta));if(ni===i&&fromPreset){toast("已经是当前筛选的最后一条");renderCurrent();return}currentId=pool[ni].review_item_id;hierarchyMode="all";renderList();renderCurrent();$("main").scrollTop=0;$("review").scrollTop=0}
function clearCurrent(row){if(!confirm("确认清空当前样本的全部人工字段？原始字段不会改变。"))return;delete decisions[row.review_item_id];save();renderCurrent();toast("已清空当前样本")}
function clearAll(){if(!confirm("确认清空 "+ROWS.length+" 条样本的全部本地人工判断？此操作不会修改源 CSV，但无法撤销。"))return;decisions={};localStorage.removeItem(STORAGE_KEY);applyFilters();toast("已清空全部人工判断")}
function csvCell(v){const s=String(v??"");return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}
function mergedRow(row){const out={...row},d=decisions[row.review_item_id]||{};HUMAN_FIELDS.forEach(k=>{if(Object.prototype.hasOwnProperty.call(d,k))out[k]=d[k]});return out}
function buildCsv(onlyFiltered=false){const rows=(onlyFiltered?filtered:ROWS).map(mergedRow);return [COLUMNS.map(csvCell).join(","),...rows.map(r=>COLUMNS.map(c=>csvCell(r[c])).join(","))].join("\r\n")}
function exportCsv(onlyFiltered){const csv="\ufeff"+buildCsv(onlyFiltered),blob=new Blob([csv],{type:"text/csv;charset=utf-8"}),url=URL.createObjectURL(blob),a=document.createElement("a");const pending=ROWS.length-reviewedCount();a.href=url;a.download=onlyFiltered?"composable_paired_task_review_items_v0_3_3_filtered.csv":`composable_paired_task_review_items_v0_3_3_${pending===0?"reviewed":"reviewed_draft"}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast("已导出 "+(onlyFiltered?filtered.length:ROWS.length)+" 条 CSV")}
function parseCsv(text){const rows=[];let row=[],cell="",quoted=false;for(let i=0;i<text.length;i++){const c=text[i],n=text[i+1];if(quoted){if(c==='"'&&n==='"'){cell+='"';i++}else if(c==='"')quoted=false;else cell+=c}else if(c==='"')quoted=true;else if(c===','){row.push(cell);cell=""}else if(c==='\n'){row.push(cell.replace(/\r$/,""));rows.push(row);row=[];cell=""}else cell+=c}if(cell.length||row.length){row.push(cell);rows.push(row)}return rows}
function importCsv(file){const reader=new FileReader();reader.onload=()=>{try{const rows=parseCsv(String(reader.result).replace(/^\ufeff/,""));const header=rows.shift()||[],idIndex=header.indexOf("review_item_id");if(idIndex<0)throw new Error("缺少 review_item_id 列");const idx=Object.fromEntries(header.map((h,i)=>[h,i]));let matched=0;rows.forEach(cells=>{const id=cells[idIndex];if(!ROWS.some(r=>r.review_item_id===id))return;if(!decisions[id])decisions[id]={};HUMAN_FIELDS.forEach(k=>{if(idx[k]!==undefined)decisions[id][k]=cells[idx[k]]||""});matched++});save();renderCurrent();toast("已导入 "+matched+" 条人工字段")}catch(e){alert("导入失败："+e.message)}};reader.readAsText(file,"utf-8")}
$("importer").onchange=e=>{const file=e.target.files&&e.target.files[0];if(file)importCsv(file);e.target.value=""};
$("search").oninput=e=>{filters.query=e.target.value;applyFilters()};
$("helpTop").onclick=()=>{$("guide")?.scrollIntoView({behavior:"smooth",block:"start"});$("guide")&&( $("guide").open=true)};
document.addEventListener("keydown",e=>{if(e.target.matches("input,textarea"))return;if(e.key==="ArrowLeft"||e.key.toLowerCase()==="j"){e.preventDefault();go(-1)}if(e.key==="ArrowRight"||e.key.toLowerCase()==="k"){e.preventDefault();go(1)}});
window.__reviewAppTest={rows:ROWS,ui:UI,buildCsv,decisions:()=>decisions,currentRow,applyPreset};
renderFilters();applyFilters();updateProgress();
</script>
</body>
</html>'''


def main() -> int:
    args = parse_args()
    rows, columns = read_rows(args.input)
    query_translations = load_query_translations(args.translations, rows)
    ui_translations = build_ui_translations(rows, query_translations)
    source_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    html = (
        HTML_TEMPLATE.replace("__ROWS_B64__", b64_json(rows))
        .replace("__UI_B64__", b64_json(ui_translations))
        .replace("__COLUMNS_JSON__", json.dumps(columns, ensure_ascii=False))
        .replace("__HUMAN_FIELDS_JSON__", json.dumps(HUMAN_FIELDS, ensure_ascii=False))
        .replace("__SOURCE_SHA256__", source_hash)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    fallback_services = sum(
        1
        for item in ui_translations["services"].values()
        if "低信息或未分类候选" in item["name_zh"] or "低信息或未分类候选" in item["description_zh"]
    )
    fallback_apis = sum(
        1
        for item in ui_translations["apis"].values()
        if "低信息或未分类候选" in item["name_zh"] or "低信息或未分类候选" in item["description_zh"]
    )
    manifest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "input_csv": str(args.input.resolve()),
        "input_sha256": source_hash,
        "input_rows": len(rows),
        "input_columns": len(columns),
        "query_translation_count": len(query_translations),
        "service_translation_count": len(ui_translations["services"]),
        "api_translation_count": len(ui_translations["apis"]),
        "fallback_service_translation_count": fallback_services,
        "fallback_api_translation_count": fallback_apis,
        "human_fields": HUMAN_FIELDS,
        "output_html": str(args.output.resolve()),
        "output_html_bytes": args.output.stat().st_size,
        "single_file_offline": True,
        "source_csv_modified": False,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
