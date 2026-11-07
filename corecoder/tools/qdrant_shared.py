"""进程内共享的本地 Qdrant 客户端。

MemoryManager 与 RAGSearchTool 共用同一个 Qdrant 客户端，
避免本地 Qdrant 数据目录同时被多个客户端实例打开导致锁冲突。
"""

import atexit
import os
import threading

from qdrant_client import QdrantClient

_lock = threading.Lock()
_client: QdrantClient | None = None
_client_path: str | None = None


def get_qdrant_client(path: str) -> QdrantClient:
    """返回当前进程的共享 Qdrant 客户端，必要时重新创建。"""
    global _client, _client_path
    abs_path = os.path.abspath(path)
    with _lock:
        if _client is not None and _client_path != abs_path:
            _client.close()
            _client = None
            _client_path = None
        if _client is None:
            _client = QdrantClient(path=abs_path)
            _client_path = abs_path
        return _client


def close_qdrant_client():
    """关闭共享客户端；重复调用安全。"""
    global _client, _client_path
    with _lock:
        if _client is not None:
            _client.close()
            _client = None
            _client_path = None


atexit.register(close_qdrant_client)
