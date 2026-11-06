"""长期记忆（Long-term Memory）工具。

基于 Qdrant + BGE-M3 实现跨会话记忆：
- save_memory: 保存用户偏好 / 关键信息
- recall_memory: 根据当前输入召回相关历史记忆
- clear_all_memory: 全量清空长期记忆
- delete_memory_by_query: 按相似度删除相关长期记忆
"""

import os
import uuid

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()

# 与 rag_tool.py 使用同一个 Qdrant 数据目录
_HERE = os.path.dirname(os.path.abspath(__file__))
_QDRANT_PATH = os.path.abspath(os.path.join(os.path.dirname(_HERE), "qdrant_data"))

_COLLECTION_NAME = "long_term_memory"
_EMBEDDING_MODEL = "BAAI/bge-m3"
_DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
_SCORE_THRESHOLD = 0.6
# 保存前去重阈值：与现有记忆相似度高于该值视为重复
_DEDUP_THRESHOLD = 0.9
# 删除指令黑名单：单独出现时过于宽泛，无法确认具体删除目标
_GENERIC_DELETE_KEYWORDS = {"代码", "项目", "框架", "语言", "开发"}
# 单次删除最多匹配数，超过则视为删除目标过宽
_DELETE_MAX_MATCHES = 5


class MemoryManager:
    """跨会话长期记忆管理器。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or _DEFAULT_BASE_URL
        self._available = False

        # 没有 API Key 时无法向量化，直接禁用，不创建本地集合
        if not self.api_key:
            return

        try:
            self.qdrant = QdrantClient(path=_QDRANT_PATH)
            self._ensure_collection()
            self._available = True
        except Exception as e:
            print(f"[MemoryManager] 初始化失败，长期记忆功能已禁用: {e}")

    def _ensure_collection(self):
        """确保 long_term_memory 集合存在。"""
        if not self.qdrant.collection_exists(_COLLECTION_NAME):
            self.qdrant.create_collection(
                collection_name=_COLLECTION_NAME,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
            )

    def _embed(self, text: str) -> list[float]:
        """用 BGE-M3 将文本向量化。"""
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY 未配置，无法向量化")
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        res = client.embeddings.create(model=_EMBEDDING_MODEL, input=text)
        return res.data[0].embedding

    def _search(self, query_vector: list[float], limit: int) -> list:
        """向量检索集合，兼容新版 query_points 与旧版 search API。"""
        try:
            search_result = self.qdrant.query_points(
                collection_name=_COLLECTION_NAME,
                query=query_vector,
                limit=limit,
            )
            return search_result.points
        except AttributeError:
            return self.qdrant.search(
                collection_name=_COLLECTION_NAME,
                query_vector=query_vector,
                limit=limit,
            )

    def save_memory(self, text: str) -> bool:
        """保存一条长期记忆，返回是否保存成功。"""
        if not self._available or not text.strip():
            return False
        try:
            # 只需向量化一次：先用向量检索最相似的一条历史记忆
            vector = self._embed(text)
            hits = self._search(vector, limit=1)
            if hits:
                hit = hits[0]
                score = getattr(hit, "score", 0)
                duplicate = hit.payload.get("text") if hit.payload else None
                if score > _DEDUP_THRESHOLD and duplicate:
                    print(f"♻️ [记忆已存在，跳过重复保存]: {duplicate}")
                    return False

            point = PointStruct(id=str(uuid.uuid4()), vector=vector, payload={"text": text})
            self.qdrant.upsert(collection_name=_COLLECTION_NAME, points=[point])
            print(f"🧠 [长期记忆已保存]: {text}")
            return True
        except Exception as e:
            print(f"[MemoryManager] 保存记忆失败: {e}")
            return False

    def recall_memory(self, query: str, limit: int = 2) -> str:
        """根据 query 召回相关记忆，未命中或出错时返回空字符串。"""
        if not self._available or not query.strip():
            return ""
        try:
            query_vector = self._embed(query)
            hits = self._search(query_vector, limit)

            texts = [
                hit.payload.get("text", "")
                for hit in hits
                if getattr(hit, "score", 0) > _SCORE_THRESHOLD and hit.payload.get("text")
            ]
            if not texts:
                return ""

            recalled = "\n".join(texts)
            print(f"💡 [触发长期记忆]: {recalled}")
            return recalled
        except Exception as e:
            print(f"[MemoryManager] 召回记忆失败: {e}")
            return ""

    def clear_all_memory(self) -> bool:
        """全量清空长期记忆：删除集合后重建空表，返回是否执行成功。"""
        if not self._available:
            print("[MemoryManager] 长期记忆未启用，无法清空")
            return False
        try:
            # 先删除整个集合，再重新创建，确保留下一个空的长期记忆集合
            self.qdrant.delete_collection(collection_name=_COLLECTION_NAME)
            self._ensure_collection()
            print("🗑️ [记忆已全部清空]")
            return True
        except Exception as e:
            print(f"[MemoryManager] 清空记忆失败: {e}")
            return False

    @staticmethod
    def _is_topic_match(text: str, keyword: str) -> bool:
        """关键词包含匹配，兼容“开发框架 / 框架开发”这类中文词序变体。"""
        if not text or not keyword:
            return False
        if keyword in text or keyword[::-1] in text:
            return True
        # 单字无法构成可靠主题；2 字以上短词同样允许 N-gram 匹配
        if len(keyword) < 2:
            return False
        shingles = {keyword[i : i + 2] for i in range(len(keyword) - 1)}
        overlap = sum(1 for shingle in shingles if shingle in text)
        return overlap >= min(2, len(shingles))

    def _block_delete(self, message: str) -> bool:
        """拒绝删除并记录原因，返回 False。"""
        self.last_delete_message = message
        print(f"[MemoryManager] {message}")
        return False

    def delete_memory_by_query(self, query: str, threshold: float = 0.85) -> bool:
        """删除与 query 相关的历史记忆，返回是否删除成功。

        删除指令往往是短关键词（如“开发框架”），与完整偏好句的向量相似度
        可能达不到 threshold，所以相似度未命中时再按“文本包含关键词”兜底，
        避免“忘记我关于XXX的偏好”失效。过于泛化的删除词会直接拒绝，
        单次匹配超过 5 条也会熔断，防止误删过多记忆。
        """
        self.last_delete_message = ""
        query = query.strip()
        if not self._available or not query:
            return False
        if query in _GENERIC_DELETE_KEYWORDS:
            return self._block_delete("删除目标过于宽泛，请指定具体技术栈")
        try:
            query_vector = self._embed(query)
            # 取稍多结果再过滤，避免阈值较高时搜不到足够候选
            hits = self._search(query_vector, limit=50)
            matched_ids = [
                hit.id
                for hit in hits
                if getattr(hit, "score", 0) > threshold and hit.payload.get("text")
            ]
            if not matched_ids:
                # 相似度阈值未命中时，按用户删除意图的明确关键词兜底匹配；
                # 用 scroll 遍历全部记忆，避免检索 topN 之外的记录漏删
                matched_ids = []
                offset = None
                while True:
                    points, next_offset = self.qdrant.scroll(
                        collection_name=_COLLECTION_NAME,
                        limit=100,
                        offset=offset,
                    )
                    matched_ids.extend(
                        p.id
                        for p in points
                        if self._is_topic_match(p.payload.get("text", ""), query)
                    )
                    if len(matched_ids) > _DELETE_MAX_MATCHES:
                        return self._block_delete(
                            "匹配到过多泛化记忆，请提供更精确的删除关键词"
                        )
                    if next_offset is None:
                        break
                    offset = next_offset
                if not matched_ids:
                    print("[MemoryManager] 未找到需要删除的历史记忆")
                    return False

            if len(matched_ids) > _DELETE_MAX_MATCHES:
                return self._block_delete("匹配到过多泛化记忆，请提供更精确的删除关键词")

            # 按 point id 精确删除命中的记忆
            self.qdrant.delete(
                collection_name=_COLLECTION_NAME,
                points_selector=matched_ids,
            )
            print("🗑️ [已删除相关记忆]")
            return True
        except Exception as e:
            self.last_delete_message = f"删除记忆失败: {e}"
            print(f"[MemoryManager] {self.last_delete_message}")
            return False

    def close(self):
        """显式关闭 Qdrant 客户端，避免解释器退出时触发 __del__ 导入错误。"""
        qdrant = getattr(self, "qdrant", None)
        if qdrant is not None:
            try:
                qdrant.close()
            except Exception as e:
                print(f"[MemoryManager] 关闭失败: {e}")
