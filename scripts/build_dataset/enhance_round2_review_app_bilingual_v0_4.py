#!/usr/bin/env python
"""Add direct Chinese translations to the Round2 HTML review app.

The translations are local, offline, and only intended to support manual audit.
This script does not run full cleaning, baseline, training, split, or search.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUT_CSV = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "round2_selected_80.csv"
HTML_PATH = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "main_four_tasks_round2_review_app_80.html"
BACKUP_PATH = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "main_four_tasks_round2_review_app_80.before_bilingual.html"
REPORT_PATH = ROOT / "docs" / "phase1" / "main_four_tasks_round2_review_app_bilingual_update_report_v0_4.md"
SUMMARY_PATH = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "round2_bilingual_update_summary_v0_4.json"
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-26_round2_review_app_bilingual_v0_4"


QUERY_ZH_BY_TASK_ID = {
    "ToolBench_G2_198": "我和朋友正在计划一次徒步旅行，想探索当地的野生动物。请获取旧金山附近国家公园中动物的详细信息和特征；另外，请分析“wildlife in San Francisco”的搜索结果页（SERP），并提供关键词概览。",
    "ToolBench_G2_199": "我正在计划一次公司出游，我们想去当地动物园。请帮我找到洛杉矶市中心办公室附近最近的动物园；另外，请提供与公司相关钱包地址的投资组合详情，用来探索 NFT 资产。",
    "ToolBench_G2_201": "我和家人正在计划去国家公园旅行，想了解当地野生动物。请提供国家公园中常见动物的列表；另外，请分析我们要去的国家公园网站的 SEO 指标。",
    "ToolBench_G2_202": "我是野生动物爱好者，想了解动物王国中的顶级捕食者。请提供这些捕食者面临的最大威胁信息；另外，请分析查询“top predators”的搜索结果页，并给出关键词概览。",
    "ToolBench_G2_203": "我是野生动物研究者，想研究动物在自然栖息地中的情况。请提供不同动物物种栖息地的详细信息；另外，请获取某个与野生动物保护组织相关的钱包地址的投资组合详情。",
    "ToolBench_G2_205": "我正在计划一次家庭野生动物保护区旅行，想了解会遇到的不同动物群体。请提供各种动物物种群体行为的信息；另外，请分析我们要去的野生动物保护区网站的 SEO 指标。",
    "ToolBench_G2_206": "我是野生动物博主，想找一些有趣的动物知识分享给读者。请提供最多 10 条有趣的动物事实；另外，请分析查询“amazing animal facts”的搜索结果页并给出关键词概览。",
    "ToolBench_G2_214": "我是历史爱好者，正在计划去瓦伦西亚旅行。请提供与瓦伦西亚相关的历史人物详情；另外，为了安全起见，请查找附近是否有超级基金污染场地。",
    "ToolBench_G2_228": "我的公司正在旧金山组织一场人工智能会议。请帮我寻找适合举办活动的房产/场地，并提供附近餐厅列表；另外，请检查受邀演讲者邮箱地址是否存在。",
    "ToolBench_G2_234": "我是音乐爱好者，正在寻找我所在地区即将举办的音乐会。请提供音乐会列表，包括场馆和票价；另外，请获取音乐行业的最新新闻。",
    "ToolBench_G2_241": "我正在计划去优胜美地国家公园徒步旅行。请提供公园 20 英里范围内最近的替代燃料站；另外，请给出优胜美地的经纬度。",
    "ToolBench_G2_268": "我想组织家庭游戏之夜，需要一些科技相关的问答题。请检索全球排名前 10 网站使用的技术；另外，找一个有科技相关爱好的随机用户资料作为灵感。",
    "ToolBench_G2_269": "我正在创办科技博客，需要研究热门网站使用的主要技术。请提供使用 G-Suite 的域名；另外，请获取关于 COVID-19 的最新新闻文章以便了解动态。",
    "ToolBench_G2_271": "我是科技爱好者，想探索技术版图。请获取某个特定域名的技术详情，并提供一个在该技术方面有专长的随机用户资料。",
    "ToolBench_G2_272": "我的公司即将发布新的科技产品，需要分析竞争对手使用的技术。请检索某个特定域名的技术详情，并找一个了解我们产品相关技术的随机用户资料。",
    "ToolBench_G2_273": "我正在计划一个科技主题派对，需要一些灵感。请获取某个特定域名的技术详情，并提供一个有科技相关爱好的随机用户资料。",
    "ToolBench_G2_274": "我想更多了解技术版图。请检索某个特定域名的技术详情，并提供 COVID-19 的最新新闻文章让我保持了解。",
    "ToolBench_G2_275": "我是科技博主，需要研究最新技术趋势。请检索某个特定域名的技术详情，并提供一个该技术领域专家的随机用户资料。",
    "ToolBench_G2_276": "我正在组织技术 meetup，需要收集参会者使用技术的信息。请检索某个特定域名的技术详情，并提供一个在该技术方面有专长的随机用户资料。",
    "ToolBench_G2_317": "我的公司计划扩大业务，并想招聘具备特定技能的专业人士。请帮我们在 LinkedIn 上搜索潜在候选人；另外，如果能提供 10 个代理样本用于数据分析就更好了。",
    "ToolBench_G2_1": "我正在开一家电商公司，需要集成包裹追踪功能。请提供 shipment ID 为 6045e2f44e1b233199a5e77a 的追踪数据；另外，我想检查 SQUAKE 认证系统的健康状态。",
    "ToolBench_G2_9": "我正在和朋友计划自驾旅行，需要追踪包裹投递进度。追踪号是 DEF456。请提供最新状态和位置更新；另外，请用邮编 98765 查找沿途加油站的地址详情。",
    "ToolBench_G2_13": "我正在组织公司活动，需要追踪活动物资的配送。请帮我追踪号为 PQR678 的包裹，并提供最新状态和位置更新；另外，请用邮编 43210 查找活动场地的地址详情。",
    "ToolBench_G2_14": "我正在计划家庭假期，需要追踪旅行必需品的配送。请帮我追踪号为 STU901 的包裹，并提供最新状态和位置更新；另外，请用邮编 98765 查找附近酒店的地址详情。",
    "ToolBench_G2_15": "我是学生，需要追踪教材的配送。请帮我追踪号为 VWX234 的包裹，并提供最新状态和位置更新；另外，请用邮编 54321 查找附近图书馆的地址详情。",
    "ToolBench_G2_16": "我正在为父母的周年纪念策划惊喜派对。请帮我追踪装有派对装饰品的包裹，追踪号是 YZA567；另外，请提供最新状态和位置更新，并用邮编 13579 查找附近面包店的地址详情。",
    "ToolBench_G2_17": "我需要追踪一个来自 Pack & Send、参考号为 ReferenceNumberHere 的包裹。请为我获取相关信息；另外，请从 Pridnestrovie Post 获取追踪号 RB413450335SG 的包裹追踪信息。",
    "ToolBench_G2_18": "我正在组织慈善活动，需要追踪号为 YT2003521266065328 的包裹。请提供最新状态；另外，请从 Pridnestrovie Post 获取追踪号 RB413450335SG 的包裹追踪信息。",
    "ToolBench_G2_28": "我需要追踪一个寄给我的包裹。请提供 shipment ID 为 6045e2f44e1b233199a5e77a 的追踪信息；另外，我想知道该 shipment 的当前状态、参考号以及相关错误信息。",
    "ToolBench_G2_47": "我正在做一个项目，需要一些巴西地址相关数据。请使用邮政编码 75094080 提供地址详情；另外，我想知道 SQUAKE API 的健康状态。",
    "ToolBench_G2_50": "我正在计划去巴西旅行，并需要发送一些重要文件。请追踪号为 RB413450335SG 的包裹；另外，我想知道 SQUAKE API 的健康状态。",
    "ToolBench_G2_85": "我需要帮助追踪 ID 为 6045e2f44e1b233199a5e77a 的包裹配送。请持续更新当前状态；另外，请获取 Pack & Send 参考号 ReferenceNumberHere 的相关信息；还要确认 suivi-colis API 正常运行。",
    "ToolBench_G2_87": "我朋友生日快到了，我想追踪 ID 为 6045e2f44e1b233199a5e77a 的礼物包裹配送。请告知当前状态；另外，请获取 Pack & Send 参考号 ReferenceNumberHere 的最新更新；最后确认 suivi-colis API 正常运行。",
    "ToolBench_G2_94": "我想查询邮政编码 75094080 对应地址的详情。请提供街区、城市、州和街道名称；另外，我需要获取 Pridnestrovie Post 关于某个特定包裹的联系信息。谢谢。",
    "ToolBench_G2_105": "我计划从伊斯坦布尔寄包裹到安卡拉，需要查找 courier 服务 correo_argentino、oca 和 andreani 在伊斯坦布尔最近的办公室。请提供这些办公室地址和联系方式；另外，我想知道伊斯坦布尔、安卡拉以及办公室所在城市的邮政编码。",
    "ToolBench_G2_38": "我正在计划去土耳其出差，需要邮政编码信息。请提供伊斯坦布尔（34）的土耳其邮政编码；另外，推荐一些可靠的国际运输代理。",
    "ToolBench_G2_40": "我正在计划去巴西家庭度假，需要了解将访问城市的信息。请提供每个城市的运输代理；另外，请给出 CEP 编号 75094080 对应城市的地址详情。",
    "ToolBench_G2_41": "我正在为好友生日组织惊喜派对，想给她寄一份特别礼物。请帮我追踪 ID 为 6045e2f44e1b233199a5e77a 的包裹，并寻找最合适的国际运输代理；另外，请提供追踪号 RB413450335SG 的包裹追踪信息。",
    "ToolBench_G2_42": "我和家人正急切等待一个包裹送达。请用 Pack & Send 参考号 ReferenceNumberHere 追踪包裹；另外，我还想知道 colis ID 为 CA107308006SI 的包裹最新状态。",
    "ToolBench_G2_43": "我和朋友正急切等待一个包裹送达。请用 Pack & Send 参考号 ReferenceNumberHere 追踪包裹；另外，我还想知道 colis ID 为 CA107308006SI 的包裹最新状态。",
    "ToolBench_G1_5": "我需要追踪一个通过 Correo Argentino 寄出的包裹，追踪码是 ABC123。请创建一个任务来获取包裹历史，并给我任务 ID；之后我会用该任务 ID 检索结果。",
    "ToolBench_G1_6": "我计划从布宜诺斯艾利斯寄包裹到科尔多瓦。请提供 Correo Argentino 和 OCA 的可用报价列表。包裹重量为 2 公斤，寄件地邮编为 1000。",
    "ToolBench_G1_7": "我计划从布宜诺斯艾利斯寄包裹到科尔多瓦。请提供 Correo Argentino 和 OCA 的可用报价列表。包裹重量为 2 公斤，寄件地邮编为 1000，商品价值为 500 阿根廷比索。",
    "ToolBench_G1_8": "我计划从布宜诺斯艾利斯寄包裹到科尔多瓦。请提供 Correo Argentino 的可用报价列表。包裹重量为 2 公斤，寄件地邮编为 1000，目的地邮编为 5000；另外，我需要某个任务 ID 的结果。",
    "ToolBench_G1_25": "能否获取追踪号为 YT2003521266065328 的包裹追踪信息？我还需要检测这个追踪号对应的承运商。",
    "ToolBench_G1_26": "请提供追踪号为 YT2003521266065328 的包裹追踪信息；另外，请检测这个追踪号对应的承运商。",
    "ToolBench_G1_27": "请追踪号为 YT2003521266065328 的包裹并提供追踪详情；另外，请检测这个追踪号对应的承运商。",
    "ToolBench_G1_28": "我正在追踪 ID 为 CA107308006SI 的包裹。请提供该包裹的最新信息和定位详情；另外，我想知道与该包裹相关的国家和事件类型。",
    "ToolBench_G1_29": "我的公司需要监控多个包裹的进度。请帮我们统计每个包裹历史中的步骤数量，这将帮助我们优化资源和网络消耗。",
    "ToolBench_G1_30": "我的朋友正在急切等待 ID 为 CA107308006SI 的包裹。请提供该包裹最新状态和定位详情；如果还能分享相关国家和事件类型就更好了。",
    "ToolBench_G1_31": "我的公司想追踪多个包裹的进度。请帮我们统计每个包裹历史中的步骤数量，这些信息会帮助我们优化资源和网络使用。",
    "ToolBench_G1_32": "我想知道 ID 为 CA107308006SI 的包裹最新更新和定位详情；另外，我想知道与该包裹相关的国家和事件类型。",
    "ToolBench_G2_156": "请获取 Efteling 游乐园中 ID 为 12 的特定景点排队长度；另外，我还想找到 Clearbit 博客中题为“Company Name to Domain API”的文章作者邮箱地址。",
    "ToolBench_G2_157": "我正在组织公司去 Efteling 游乐园出游，需要查看所有景点的排队时间；另外，请获取域名 stripe.com 关联的邮箱地址用于沟通。",
    "ToolBench_G2_159": "我想了解 Efteling 游乐园的排队时间。请获取所有景点的排队长度；另外，请找到 Clearbit 博客最新文章作者的邮箱地址。",
    "ToolBench_G2_160": "我正在计划去 Efteling 游乐园旅行，想知道所有景点当前排队时间；另外，请获取域名 stripe.com 关联的邮箱地址，以便进一步联系。",
    "ToolBench_G2_162": "我需要找到域名 stripe.com 关联的邮箱地址用于公司沟通；另外，请获取 Efteling 游乐园中 ID 为 12 的特定景点排队长度。",
    "ToolBench_G2_178": "我的朋友是健身爱好者，想制定个性化饮食计划。请帮他找到附近最好的健身教练；另外，提供不同食物的营养信息，并建议一份健康蛋白奶昔食谱。",
    "ToolBench_G2_179": "我正在组织慈善活动，需要为活动寻找赞助商。请提供我所在城市以公益活动闻名的公司列表；另外，推荐一些支持慈善事业的有影响力人士的 Instagram 账号。",
    "ToolBench_G2_180": "我正在创业，需要寻找可靠的网站托管服务。请推荐一些提供实惠套餐的热门网站托管公司；另外，提供使用这些托管服务搭建的网站列表。",
    "ToolBench_G2_181": "我是时尚爱好者，需要为下一套穿搭找灵感。请推荐一些在 Instagram 上展示独特潮流风格的热门时尚博主；另外，提供最新时尚新闻和趋势。",
    "ToolBench_G2_182": "我的公司正在组织团队建设活动，需要寻找合适场地。请推荐一些能容纳大量人员的城市活动空间；另外，提供能协助组织活动的活动策划人的联系方式。",
    "ToolBench_G2_183": "我正在计划一次欧洲自驾旅行，需要寻找最佳旅行目的地。请推荐不同欧洲国家的热门旅游景点；另外，推荐一些拍摄壮丽风景的旅行摄影师 Instagram 账号。",
    "ToolBench_G2_184": "我是书虫，正在寻找新书阅读。请推荐我所在城市的一些热门书店；另外，推荐一些最近出版有趣书籍的作者 Instagram 账号。",
    "ToolBench_G2_2": "我正在计划去伊斯坦布尔旅行，需要知道不同区的邮政编码。请提供车牌编号为 34 的伊斯坦布尔各区邮政编码；另外，我想用任务 ID 987654321 追踪一个包裹。",
    "ToolBench_G2_3": "我正在计划去布宜诺斯艾利斯旅行，需要知道不同区的邮政编码。请提供车牌编号为 1 的布宜诺斯艾利斯各区邮政编码；另外，我想用任务 ID 987654321 追踪一个包裹。",
    "ToolBench_G2_6": "我正在计划去土耳其旅行，需要伊斯坦布尔的邮政编码信息。请提供车牌编号为 34 的伊斯坦布尔省邮政编码和区名；另外，我想知道伊斯坦布尔是否有可用的运输代理，并请获取它们的名称和联系电话。",
    "ToolBench_G2_8": "我正在为好友生日策划惊喜派对。请帮我追踪为派对订购的包裹，参考号是 ABC123；另外，请提供包裹最新状态和配送历史，并用邮编 12345 查找派对地点的地址详情。",
    "ToolBench_G2_11": "我是小企业主，需要追踪产品配送。请帮我追踪号为 JKL012 的包裹，并提供最新状态和位置更新；另外，请用邮编 13579 查找附近邮局的地址详情。",
    "ToolBench_G2_12": "我正在为伴侣计划惊喜约会之夜。请帮我追踪装有惊喜礼物的包裹，追踪号是 MNO345；另外，请提供最新状态和位置更新，并用邮编 56789 查找附近餐厅的地址详情。",
}


SERVICE_ZH = {
    "Turkey Postal Codes": "土耳其邮政编码服务",
    "suivi-colis": "包裹追踪服务（suivi-colis）",
    "Pack & Send": "Pack & Send 包裹追踪服务",
    "CEP Brazil": "巴西 CEP 邮编地址服务",
    "Animals by API-Ninjas": "API-Ninjas 动物信息服务",
    "Email and internal links scraper": "邮箱和内部链接抓取服务",
    "IP to Income": "IP 收入估计服务",
    "Opensea_v2": "OpenSea/NFT 数据服务",
    "Domain SEO Analysis": "域名 SEO 分析服务",
    "Amex Australia (Fastway Australia) Tracking": "澳大利亚 Fastway/Amex 包裹追踪服务",
    "runs.tech": "网站技术栈检测服务",
    "Create Container Tracking": "集装箱追踪创建服务",
    "Pridnestrovie Post": "德涅斯特河沿岸邮政追踪服务",
    "CountryByIP": "IP 所属国家查询服务",
    "AI Random User Generator": "随机用户资料生成服务",
    "COVID-19 News": "COVID-19 新闻服务",
    "Transportistas de Argentina": "阿根廷物流/邮政运输服务",
    "Transitaires": "运输代理查询服务",
    "Linkedin Company and Profile Data": "LinkedIn 公司和个人资料数据服务",
    "100% Success Instagram API - Scalable & Robust": "Instagram 数据接口服务",
    "Sample API": "样例 API 服务",
    "TrackingMore_v2": "TrackingMore 包裹追踪服务",
    "SQUAKE": "SQUAKE 出行/认证系统服务",
    "Unofficial Efteling API": "Efteling 游乐园非官方接口",
    "Tomba": "邮箱查找和验证服务",
    "Cloudflare bypass": "Cloudflare 绕过请求服务",
    "Crops": "农作物数据服务",
    "EPA Superfunds": "美国 EPA 超级基金污染场地服务",
    "Historical Figures by API-Ninjas": "API-Ninjas 历史人物服务",
    "Currents News": "Currents 新闻服务",
    "IP reputation, geoip and detect VPN": "IP 信誉、地理位置和 VPN 检测服务",
    "VRM STR Tools": "短租房源评价工具服务",
    "MLS Router": "房产/用户存在性查询服务",
    "Real-Time News Data": "实时新闻数据服务",
    "Kargom Nerede": "土耳其包裹承运商查询服务",
    "GS1Parser": "GS1 条码解析服务",
    "Orderful": "交易数据服务",
    "Weather": "天气服务",
    "Real Estate Records": "房地产记录服务",
    "Captcha": "验证码求解服务",
    "NREL National Renewable Energy Laboratory": "美国 NREL 替代燃料站/能源数据服务",
    "Indian Names": "印度姓名/状态数据服务",
    "VinHub": "VIN/车辆相关服务",
    "Linkedin Profiles": "LinkedIn 个人资料检索服务",
    "IYS Skill API ": "技能搜索 API 服务",
    "Proxy-Spider Proxies": "代理样本服务",
}


API_ZH = {
    "il": "土耳其车牌省份编号查询",
    "Latest": "获取包裹最新状态",
    "Health": "服务健康检查",
    "/api/Tracking/": "Pack & Send 参考号追踪",
    "Retorna Dados do Endereço através do CEP": "通过 CEP 返回地址数据",
    "Count": "统计包裹历史步骤数量",
    "/v1/animals": "动物信息查询接口",
    "GET request": "网页 GET 抓取请求",
    "IP address": "IP 地址查询",
    "x2y2 GET": "x2y2 GET 请求",
    "Retreive portfolio": "获取钱包投资组合详情",
    "Asset Information": "获取 NFT 资产页面信息",
    "Domain SEO Analysis": "域名 SEO 指标分析",
    "SERP Analysis": "搜索结果页/关键词概览分析",
    "All": "获取全部追踪历史",
    "Track Package": "包裹追踪",
    "getTechDomains": "查询使用某项技术的域名",
    "getDomainTech": "查询某域名使用的技术",
    "getAllTech": "获取全部技术列表",
    "Get Tracking Data": "获取追踪数据",
    "Get track info": "获取包裹追踪信息",
    "getCountryByIP": "通过 IP 查询国家",
    "Get Random User": "获取随机用户资料",
    "/v1/covid": "COVID-19 新闻查询",
    "/tracking/correo_argentino/result_task/:task_id": "按任务 ID 获取 Correo Argentino 追踪结果",
    "/quotes/postcode/oca/:cuit/:operativa/:cost/:weight/:volume/:postCodeSrc/:postCodeDst": "按邮编查询 OCA 运费报价",
    "Transitaires": "运输代理列表",
    "Transitaire": "单个运输代理详情",
    "Supported Locations": "支持地区列表",
    "user-feed": "Instagram 用户动态",
    "user-info": "Instagram 用户信息",
    "fbsearch-places": "地点搜索",
    "about": "服务说明",
    "packages/track (Deprecated)": "旧版包裹追踪",
    "packages/v2/track": "新版包裹追踪",
    "Checkhealth": "健康状态检查",
    "Projects": "项目列表",
    "carriers/list": "承运商列表",
    "/offices/postcode/:service/:postCode": "按服务和邮编查询办公室",
    "/cities/search/:stateIsoCode/:keyword": "按州代码和关键词搜索城市",
    "Retrieve all Queue times": "获取所有景点排队时间",
    "Retrieve specific Queue time": "获取指定景点排队时间",
    "Retrieve the latest blogs": "获取最新博客",
    "EmailFinder": "邮箱查找",
    "AuthorFinder": "作者邮箱查找",
    "EmailVerifier": "邮箱验证",
    "/offices/search/:service/:stateIsoCode/:keyword": "按服务、州代码和关键词搜索办公室",
    "carriers/detect": "检测承运商",
    "/cities/states/:stateIsoCode": "查询指定州的城市/地区",
    "/cities/postcode/:stateIsoCode/:postCode": "按州和邮编查询城市",
    "/cities/states": "查询州列表",
    "/quotes/city/correo_argentino/:weight/:stateIsoCodeSrc/:normalizeCityNameSrc/:stateIsoCodeDst/:normalizeCityNameDst": "按城市查询 Correo Argentino 报价",
    "/quotes/postcode/correo_argentino/:weight/:postCodeSrc/:postCodeDst": "按邮编查询 Correo Argentino 报价",
    "/tracking/correo_argentino/create_task/:service/:tracking_code": "创建 Correo Argentino 包裹追踪任务",
    "GET Requests": "GET 请求",
    "Crops list": "农作物列表",
    "Superfund Search": "超级基金污染场地搜索",
    "/v1/historicalfigures": "历史人物查询",
    "Latest news": "最新新闻",
    "Search": "搜索",
    "ip-reputation": "IP 信誉/地理位置/VPN 检测",
    "Get VRBO Listing Reviews": "获取 VRBO 房源评论",
    "Get Airbnb Listing Ratings": "获取 Airbnb 房源评分",
    "Get Airbnb Listing Reviews": "获取 Airbnb 房源评论",
    "Check User Existence": "检查用户是否存在",
    "List Properties": "列出房产/场地",
    "Topic Headlines": "主题新闻头条",
    "Language List": "语言列表",
    "Top Headlines": "头条新闻",
    "companies": "公司列表/承运商公司",
    "/parse": "解析 GS1 条码",
    "Transactions": "交易记录",
    "5 day Forecast": "5 日天气预报",
    "Current Weather Data of a location.": "指定地点当前天气",
    "120 Hour Forecast": "120 小时天气预报",
    "summary": "摘要信息",
    "detail": "详细信息",
    "address": "地址信息",
    "solve": "验证码求解",
    "Nearest Stations": "最近站点",
    "All Stations": "全部站点",
    "Utility Rates": "公用事业费率",
    "Get Status": "获取状态",
    "Get Names": "获取姓名",
    "Check": "检查",
    "Balance": "余额",
    "Orders": "订单",
    "/extract": "提取 LinkedIn 资料",
    "/search": "搜索 LinkedIn 资料",
    "Skills Search": "技能搜索",
    "Functional Areas": "职能领域",
    "Skill Tree": "技能树",
    "/proxies.example.json": "代理样本 JSON",
}


DESC_HINTS = [
    ("Returns up to 10 results matching the input name parameter", "根据输入名称参数返回最多 10 条匹配结果。"),
    ("Pass URL as", "传入 URL 参数，抓取页面中的邮箱和内部链接。"),
    ("IP address", "根据 IP 地址进行查询。"),
    ("retrieve portfolio details", "用于获取单个钱包地址的投资组合详情。"),
    ("Scrape all the HTML information", "抓取 NFT 资产页面中的 HTML、图片和元数据。"),
    ("Get popular SEO metrics", "获取给定域名的常用 SEO 指标。"),
    ("SERP Analysis", "分析搜索结果页，发现关键词想法、排名难度和流量潜力。"),
    ("Track a package shipping details", "使用包裹追踪号查询运输详情。"),
    ("Retorna dados", "返回地址数据。"),
    ("Get the API's health", "检查 API 健康状态。"),
    ("Turkish plates", "土耳其省份车牌编号查询。"),
    ("queue", "获取景点排队长度或排队时间。"),
    ("email", "查找或验证邮箱地址。"),
    ("postal", "查询邮政编码或地址相关信息。"),
    ("quotes", "查询物流/快递报价。"),
    ("carrier", "查询或检测承运商。"),
]


def read_rows() -> list[dict[str, str]]:
    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_json(text: str) -> list:
    try:
        data = json.loads(text or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def service_zh(name: str) -> str:
    return SERVICE_ZH.get(name or "", f"{name}（服务名，待人工确认含义）" if name else "")


def api_zh(name: str) -> str:
    return API_ZH.get(name or "", f"{name}（接口名，待人工确认含义）" if name else "")


def description_zh(description: str, api_name: str) -> str:
    if not description:
        return api_zh(api_name)
    for needle, zh in DESC_HINTS:
        if needle.lower() in description.lower():
            return zh
    return f"{api_zh(api_name)}；原英文说明较长，请结合英文 description 判断。"


def enrich_services_json(text: str) -> str:
    data = parse_json(text)
    for item in data:
        if isinstance(item, dict):
            item["service_name_zh"] = service_zh(str(item.get("service_name", "")))
    return json.dumps(data, ensure_ascii=False)


def enrich_apis_json(text: str) -> str:
    data = parse_json(text)
    for item in data:
        if isinstance(item, dict):
            service = str(item.get("service_name", ""))
            api = str(item.get("api_name", ""))
            item["service_name_zh"] = service_zh(service)
            item["api_name_zh"] = api_zh(api)
            item["api_description_zh"] = description_zh(str(item.get("api_description", "")), api)
    return json.dumps(data, ensure_ascii=False)


def enrich_gold_services(text: str) -> str:
    services = parse_json(text)
    enriched = [{"service_name": str(s), "service_name_zh": service_zh(str(s))} for s in services]
    return json.dumps(enriched, ensure_ascii=False)


def enrich_row(row: dict[str, str]) -> dict[str, str]:
    enriched = dict(row)
    enriched["query_text_zh"] = QUERY_ZH_BY_TASK_ID.get(row.get("task_id", ""), "【待补译】" + row.get("query_text", ""))
    enriched["candidate_services_json"] = enrich_services_json(row.get("candidate_services_json", ""))
    enriched["candidate_apis_json"] = enrich_apis_json(row.get("candidate_apis_json", ""))
    enriched["gold_services_zh_json"] = enrich_gold_services(row.get("gold_services_json", ""))
    enriched["gold_apis_json"] = enrich_apis_json(row.get("gold_apis_json", ""))
    return enriched


def replace_data(html: str, rows: list[dict[str, str]]) -> str:
    data_js = "const DATA = " + json.dumps(rows, ensure_ascii=False) + ";\nconst STORAGE_KEY ="
    updated, count = re.subn(
        r"const DATA = .*?;\nconst STORAGE_KEY =",
        lambda _match: data_js,
        html,
        flags=re.S,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Could not replace DATA block in HTML")
    return updated


def inject_css(html: str) -> str:
    css = """
.bilingual-grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:12px; }
.lang-card { border:1px solid #e5e7eb; border-radius:6px; padding:10px; background:#fafafa; }
.lang-label { font-size:12px; font-weight:700; color:#374151; margin-bottom:6px; }
.zh-text { color:#111827; line-height:1.65; }
.api-zh { color:#1d4ed8; font-weight:600; margin-left:6px; }
.api-desc-zh { color:#374151; margin-top:4px; }
.translation-note { background:#eef2ff; border:1px solid #c7d2fe; border-radius:6px; padding:8px 10px; margin-top:8px; color:#3730a3; }
"""
    if ".bilingual-grid" in html:
        return html
    return html.replace("</style>", css + "</style>", 1)


def replace_hierarchy(html: str) -> str:
    new_func = r'''function hierarchy(row) {
  const services = parseJson(row.candidate_services_json, []);
  const apis = parseJson(row.candidate_apis_json, []);
  const goldApis = parseJson(row.gold_apis_json, []);
  const goldServices = new Set(parseJson(row.gold_services_json, []).map(String));
  const serviceNames = new Set(services.map(s => s.service_name));
  const serviceZh = {};
  services.forEach(s => { serviceZh[s.service_name] = s.service_name_zh || ""; });
  apis.forEach(a => { if (a.service_name && a.service_name_zh) serviceZh[a.service_name] = a.service_name_zh; });
  const goldApiKeys = new Set(goldApis.map(a => `${a.service_name}|||${a.api_name}`));
  const grouped = {};
  apis.forEach(api => {
    const svc = api.service_name || "(missing service_name)";
    if (!grouped[svc]) grouped[svc] = [];
    grouped[svc].push(api);
  });
  return Object.keys(grouped).map(svc => {
    const warn = serviceNames.has(svc) ? "" : `<div class="warning">WARNING: API service name not found in candidate_services_json</div>`;
    const svcGold = goldServices.has(svc) ? ` <span class="gold">[GOLD_SERVICE]</span>` : "";
    const svcZh = serviceZh[svc] ? `<div class="small zh-text">中文服务：${serviceZh[svc]}</div>` : "";
    const items = grouped[svc].map(api => {
      const isGold = goldApiKeys.has(`${api.service_name}|||${api.api_name}`) ? ` <span class="gold">[GOLD_API]</span>` : "";
      const apiZh = api.api_name_zh ? `<span class="api-zh">中文：${api.api_name_zh}</span>` : "";
      const descZh = api.api_description_zh ? `<div class="api-desc-zh">说明中文：${api.api_description_zh}</div>` : "";
      return `<li><div>API: ${api.api_name || "(empty)"}${apiZh}${isGold}</div><div class="small">EN description: ${api.api_description || ""}</div>${descZh}</li>`;
    }).join("");
    return `<div class="tree-service"><strong>Service: ${svc}${svcGold}</strong>${svcZh}${warn}<ul>${items}</ul></div>`;
  }).join("");
}'''
    updated, count = re.subn(r"function hierarchy\(row\) \{.*?\n\}", new_func, html, flags=re.S, count=1)
    if count != 1:
        raise RuntimeError("Could not replace hierarchy function")
    return updated


def replace_query_block(html: str) -> str:
    old = '<h3>Query</h3><pre>${row.query_text}</pre>'
    new = '''<h3>Query / 需求</h3>
    <div class="bilingual-grid">
      <div class="lang-card"><div class="lang-label">English Original</div><pre>${row.query_text}</pre></div>
      <div class="lang-card"><div class="lang-label">中文翻译</div><pre class="zh-text">${row.query_text_zh || "【待补译】"}</pre></div>
    </div>
    <div class="translation-note">翻译只用于提高人工审核速度；最终判断仍以英文 query、gold service/API 和候选层级为准。</div>'''
    if old not in html:
        if "Query / 需求" in html and "query_text_zh" in html:
            return html
        raise RuntimeError("Could not find query block")
    return html.replace(old, new, 1)


def replace_gold_services_raw(html: str) -> str:
    old = '<div><h3>Gold Services</h3><pre>${JSON.stringify(parseJson(row.gold_services_json, []), null, 2)}</pre></div>'
    new = '<div><h3>Gold Services</h3><pre>${JSON.stringify(parseJson(row.gold_services_zh_json || row.gold_services_json, []), null, 2)}</pre></div>'
    return html.replace(old, new, 1)


def write_report(rows: list[dict[str, str]]) -> None:
    missing_query = [r["round2_review_id"] for r in rows if str(r.get("query_text_zh", "")).startswith("【待补译】")]
    report = f"""# Round2 Review App Bilingual Update v0.4

生成时间：{datetime.now().isoformat(timespec="seconds")}

## 本次改了什么

- 在 HTML 审核页中为每条 query 增加中文翻译，显示为英文原文和中文翻译双栏。
- 在 Service/API Hierarchy View 中为 service name、API name、API description 增加中文说明。
- 在 Raw Candidate/Gold JSON 中保留翻译增强字段，方便必要时检查原始结构。
- 保留原有 assistant draft、localStorage、筛选、搜索、上一条/下一条和导出 CSV 功能。

## 翻译覆盖

- 输入样本数：{len(rows)}
- query 中文翻译覆盖：{len(rows) - len(missing_query)} / {len(rows)}
- query 待补译样本：{missing_query}
- service/API 翻译方式：基于本地 service/API 词典和接口语义说明，不调用外部翻译 API。

## 注意事项

- 中文翻译只是人工审核辅助，不是最终标签。
- 如果中文翻译和英文原文理解冲突，以英文原始 query、candidate service/API 和 gold 为准。
- 本次没有跑 full cleaning。
- 本次没有跑 baseline。
- 本次没有训练模型。
- 本次没有 split。
- 本次没有继续 top200。
- 本次没有重新搜索 full G3。

## 输出

- 更新 HTML：`{HTML_PATH}`
- 备份 HTML：`{BACKUP_PATH}`
- summary JSON：`{SUMMARY_PATH}`
- 归档目录：`{ARCHIVE_DIR}`
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Missing input CSV: {INPUT_CSV}")
    if not HTML_PATH.exists():
        raise FileNotFoundError(f"Missing HTML: {HTML_PATH}")

    rows = [enrich_row(r) for r in read_rows()]
    if not BACKUP_PATH.exists():
        shutil.copy2(HTML_PATH, BACKUP_PATH)

    html = HTML_PATH.read_text(encoding="utf-8")
    html = replace_data(html, rows)
    html = inject_css(html)
    html = replace_hierarchy(html)
    html = replace_query_block(html)
    html = replace_gold_services_raw(html)
    HTML_PATH.write_text(html, encoding="utf-8")

    missing_query = [r["round2_review_id"] for r in rows if str(r.get("query_text_zh", "")).startswith("【待补译】")]
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(rows),
        "query_translation_covered_rows": len(rows) - len(missing_query),
        "missing_query_translation_review_ids": missing_query,
        "html_file": str(HTML_PATH),
        "backup_file": str(BACKUP_PATH),
        "report_file": str(REPORT_PATH),
        "archive_dir": str(ARCHIVE_DIR),
        "no_full_cleaning": True,
        "no_baseline": True,
        "no_training": True,
        "no_split": True,
        "no_top200": True,
        "no_full_g3_research": True,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(rows)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [HTML_PATH, BACKUP_PATH, REPORT_PATH, SUMMARY_PATH, Path(__file__)]:
        if path.exists():
            shutil.copy2(path, ARCHIVE_DIR / path.name)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
