import os
import re
import jieba
from dotenv import load_dotenv
from openai import OpenAI
from rank_bm25 import BM25Okapi

from .base import Tool
from .qdrant_shared import get_qdrant_client

load_dotenv()

_HERE = os.path.dirname(os.path.abspath(__file__))
# 注意：这里和 build_rag.py 保持一致，都在 corecoder 目录下
_QDRANT_PATH = os.path.abspath(os.path.join(os.path.dirname(_HERE), "qdrant_data"))

# 粗排：双路各取 Top-10 进入融合
_CANDIDATE_LIMIT = 10
# 精排：最终返回给大模型的 Top-4
_MAX_RESULTS = 4
# 向量相似度阈值底线
_SCORE_THRESHOLD = 0.6


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

    def __init__(self):
        # BM25 索引在初始化时构建，避免在线检索时第一次调用才分词建索引
        self._init_bm25_index()

    def _init_bm25_index(self):
        """读取本地规范并按句切块，用 jieba 分词后构建离线 BM25 索引。"""
        rules_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "coding_rules.txt"
        )
        self.bm25 = None
        self.bm25_corpus = []

        if not os.path.exists(rules_path):
            return
        # 先把规范中的核心专业词汇加入 jieba 自定义词典，避免分词时被切碎
        custom_words = [
            "xingyu_http", "xy_logger", "xingyu_time", "xingyu_safe_dict", "safe_get",
            "xingyu_rpc", "xingyu_exceptions", "xingyu_masking", "xingyu_cache",
            "xingyu_threading", "SafeClient", "RetryPolicy", "ThreadPoolManager",
            "XingyuBaseException", "trace_id", "user_id", "get_current_timestamp",
            "N+1", "async/await", "ast.literal_eval", "asyncio.sleep", "time.sleep",
        ]
        for word in custom_words:
            jieba.add_word(word)
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 按换行符切分并过滤空行 / Markdown 标题，保证每条规范是完整行
            self.bm25_corpus = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    self.bm25_corpus.append(line)
            # 使用 jieba 分词，兼容中英文和代码符号
            tokenized_corpus = [jieba.lcut(chunk) for chunk in self.bm25_corpus]
            self.bm25 = BM25Okapi(tokenized_corpus)
        except Exception as e:
            print(f"[RAGSearchTool] BM25 索引构建失败: {e}")
            self.bm25 = None
            self.bm25_corpus = []

    def execute(self, query: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return "RAG search unavailable: OPENAI_API_KEY not set in .env"
        # 防呆截断：仅当 Query 过长（>30字符）时，才提取核心代码符号并截断
        if len(query) > 30:
            tokens = re.findall(r"[a-zA-Z0-9_\.]+", query)
            # 去重并保留前 5 个
            unique_tokens = list(dict.fromkeys(tokens))
            cleaned = " ".join(unique_tokens[:5])
            # 如果成功提取到英文/代码符号，则替换；如果是纯中文长句，保留原样
            if cleaned:
                query = cleaned
        client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")
        qdrant = get_qdrant_client(_QDRANT_PATH)

        # --- 1. 双路召回准备 ---
        # A. Query 分词（供 BM25 使用）
        query_tokens = jieba.lcut(query)

        # B. Query 向量化（供 Qdrant 使用）
        try:
            res = client.embeddings.create(model="BAAI/bge-m3", input=query)
            query_vector = res.data[0].embedding
        except Exception as e:
            return f"向量化失败: {e}"

        vector_hits = []
        bm25_hits = []

        # --- 2. 执行向量检索（带阈值过滤） ---
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

        # 过滤掉低于阈值的向量命中
        for hit in hits:
            if getattr(hit, "score", 0) >= _SCORE_THRESHOLD:
                vector_hits.append(hit.payload.get("text", ""))

        # --- 3. 执行 BM25 检索（带零分过滤） ---
        if self.bm25 and self.bm25_corpus:
            bm25_scores = self.bm25.get_scores(query_tokens)
            # 获取得分大于 0 的索引，并按分数降序排序
            valid_indices = [i for i, score in enumerate(bm25_scores) if score > 0]
            valid_indices.sort(key=lambda i: bm25_scores[i], reverse=True)

            # 截取 Top-CANDIDATE_LIMIT
            for idx in valid_indices[:_CANDIDATE_LIMIT]:
                bm25_hits.append(self.bm25_corpus[idx])

        # --- 4. 防呆拦截：双路全空 ---
        if not vector_hits and not bm25_hits:
            return "未找到相关规范。"

        # --- 5. RRF 融合排序（k=60） ---
        rrf_dict = {}
        k = 60

        # 计算向量路的 RRF 分数
        for rank, text in enumerate(vector_hits):
            rrf_dict[text] = rrf_dict.get(text, 0.0) + (1.0 / (k + rank + 1))

        # 计算 BM25 路的 RRF 分数
        for rank, text in enumerate(bm25_hits):
            rrf_dict[text] = rrf_dict.get(text, 0.0) + (1.0 / (k + rank + 1))

        # --- 6. 最终排序与截断 ---
        sorted_results = sorted(rrf_dict.items(), key=lambda item: item[1], reverse=True)
        final_top = sorted_results[:_MAX_RESULTS]

        # --- 7. 格式化输出 ---
        output_lines = []
        for text, rrf_score in final_top:
            # 为了评测时方便看，把 RRF 分数也打印出来
            output_lines.append(f"- {text} (RRF融合分: {rrf_score:.4f})")

        return "\n".join(output_lines)
