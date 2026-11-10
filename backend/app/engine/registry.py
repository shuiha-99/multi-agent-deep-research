"""The nine business tools. Framework planning tools are deliberately excluded."""

TOOL_REGISTRY = [
    {"name": "search_web", "agent": "web", "label": "联网搜索", "description": "搜索公开资料并保留来源；示例模式使用离线虚构资料。"},
    {"name": "fetch_page", "agent": "web", "label": "网页读取", "description": "读取实际网页或明确标记的离线页面。"},
    {"name": "extract_page_tables", "agent": "web", "label": "网页表格", "description": "提取页面表格、标题和来源。"},
    {"name": "search_knowledge", "agent": "knowledge", "label": "知识检索", "description": "仅检索本任务选中的知识文档。"},
    {"name": "read_chunk", "agent": "knowledge", "label": "原文核查", "description": "读取文档片段及相邻上下文。"},
    {"name": "get_document_metadata", "agent": "knowledge", "label": "文档信息", "description": "读取文档版本、时间和适用范围。"},
    {"name": "inspect_dataset", "agent": "data", "label": "数据检查", "description": "检查数据结构、缺失值、粒度和时间覆盖。"},
    {"name": "aggregate_metrics", "agent": "data", "label": "指标统计", "description": "按明确口径执行受限统计，避免模型计算数字。"},
    {"name": "create_chart", "agent": "data", "label": "图表生成", "description": "根据实际统计结果生成图表产物。"},
]

AGENTS = {
    "web": {"name": "联网研究助手", "tools": [t["name"] for t in TOOL_REGISTRY if t["agent"] == "web"]},
    "knowledge": {"name": "知识库助手", "tools": [t["name"] for t in TOOL_REGISTRY if t["agent"] == "knowledge"]},
    "data": {"name": "数据分析助手", "tools": [t["name"] for t in TOOL_REGISTRY if t["agent"] == "data"]},
}

TOOL_AGENTS = {t["name"]: t["agent"] for t in TOOL_REGISTRY}
