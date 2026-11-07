import os
from dotenv import load_dotenv
from openai import OpenAI
from .base import Tool
from .qdrant_shared import get_qdrant_client

load_dotenv()

_HERE = os.path.dirname(os.path.abspath(__file__))
# 注意：这里和 build_rag.py 保持一致，都在 corecoder 目录下
# 路径基于当前文件，clone 后无需修改
_QDRANT_PATH = os.path.abspath(os.path.join(os.path.dirname(_HERE), "qdrant_data"))
# 相似度低于该值的检索结果直接丢弃，避免弱相关规范混入上下文
_SCORE_THRESHOLD = 0.6
# 候选数量放大，保留字面包含关键词的兜底可能性；最终仍最多输出 2 条
_CANDIDATE_LIMIT = 20
_MAX_RESULTS = 2

class RAGSearchTool(Tool):
    name = "search_coding_rules"
    description = "当需要查阅团队代码规范、安全守则时使用此工具"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"]
    }

    def execute(self, query: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return "RAG search unavailable: OPENAI_API_KEY not set in .env"
        client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")
        qdrant = get_qdrant_client(_QDRANT_PATH)

        res = client.embeddings.create(model="BAAI/bge-m3", input=query)
        query_vector = res.data[0].embedding

        try:
            search_result = qdrant.query_points(
                collection_name="coding_rules",
                query=query_vector,
                limit=_CANDIDATE_LIMIT
            )
            hits = search_result.points
        except AttributeError:
            hits = qdrant.search(
                collection_name="coding_rules",
                query_vector=query_vector,
                limit=_CANDIDATE_LIMIT
            )

        # 相似度高于阈值，或规范文本中字面包含查询关键词时，视为有效命中
        valid_hits = [
            hit for hit in hits
            if hit.payload.get("text")
            and (
                getattr(hit, "score", 0) > _SCORE_THRESHOLD
                or query.lower() in hit.payload.get("text", "").lower()
            )
        ]
        # 混合检索后按相似度降序排列，确保返回高分优先的 Top-2
        valid_hits.sort(key=lambda hit: getattr(hit, "score", 0), reverse=True)
        valid_hits = valid_hits[:_MAX_RESULTS]

        if not valid_hits:
            return "未找到相关规范。"

        return "\n".join([f"- {hit.payload.get('text', '')} (相似度: {hit.score:.2f})" for hit in valid_hits])
