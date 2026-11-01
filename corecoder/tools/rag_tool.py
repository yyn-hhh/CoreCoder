import os
from qdrant_client import QdrantClient
from openai import OpenAI
from .base import Tool

_HERE = os.path.dirname(os.path.abspath(__file__))
# 注意：这里和 build_rag.py 保持一致，都在 corecoder 目录下
# 把 _QDRANT_PATH 写死
_QDRANT_PATH = "D:/develop/MyAgent/CoreCoder/corecoder/qdrant_data"

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
        client = OpenAI(api_key="sk-zfzzzviasdyfkyviwjfnstbfamovgoncecnldyzyblvuxkjd", base_url="https://api.siliconflow.cn/v1")
        qdrant = QdrantClient(path=_QDRANT_PATH)
        
        res = client.embeddings.create(model="BAAI/bge-m3", input=query)
        query_vector = res.data[0].embedding
        
        try:
            search_result = qdrant.query_points(
                collection_name="coding_rules",
                query=query_vector,
                limit=2
            )
            hits = search_result.points
        except AttributeError:
            hits = qdrant.search(
                collection_name="coding_rules",
                query_vector=query_vector,
                limit=2
            )
        
        if not hits:
            return "未找到相关规范。"
        
        return "\n".join([f"- {hit.payload.get('text', '')} (相似度: {hit.score:.2f})" for hit in hits])