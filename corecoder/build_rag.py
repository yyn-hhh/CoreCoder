import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from openai import OpenAI

_HERE = os.path.dirname(os.path.abspath(__file__))

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", ""),
    base_url="https://api.siliconflow.cn/v1"
)

# 库建在 corecoder 目录下，用相对当前文件的路径，clone 后无需修改
qdrant = QdrantClient(path=os.path.join(_HERE, "qdrant_data"))
collection_name = "coding_rules"

with open(os.path.join(_HERE, "coding_rules.txt"), "r", encoding="utf-8") as f:
    text = f.read()

chunks = [c.strip() + "。" for c in text.split("。") if c.strip()]

if not qdrant.collection_exists(collection_name):
    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
    )

points = []
for i, chunk in enumerate(chunks):
    res = client.embeddings.create(model="BAAI/bge-m3", input=chunk)
    points.append(PointStruct(id=i, vector=res.data[0].embedding, payload={"text": chunk}))

qdrant.upsert(collection_name=collection_name, points=points)
print("✅ 知识库构建完成！")
