# corecoder/tools/mcp_tool.py
import json
import subprocess
import threading
import time
from typing import Any, Dict, List
from .base import Tool
import concurrent.futures


class MCPTool(Tool):
    """通过 MCP 协议连接外部工具 Server，自动发现并展开工具"""

    name = "mcp"
    description = "MCP 协议工具 — 连接外部 MCP Server，动态发现并调用工具"

    def __init__(self, server_command: List[str], name: str = "mcp"):
        self._server_command = server_command
        self._name = name
        self._process = None
        self._tools: List[Dict] = []
        self._connected = False
        self._running = False              # 新增：控制后台线程
        self._pending = {}                 # 新增：id -> Future
        self._next_id = 1                  # 新增：请求 id 计数器
        self._lock = threading.Lock()      # 新增：保护 _pending 和 _next_id
        self._init_connection()

    def _init_connection(self):
        try:
            self._process = subprocess.Popen(
                self._server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",      # ← 加这一行
                errors="replace",
                bufsize=1,
            )
            self._running = True
            # 启动后台线程持续读取消息
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

            # MCP 握手
            self._initialize()

            # 发现工具
            response = self._request("tools/list", {})
            if response and "result" in response:
                self._tools = response["result"].get("tools", [])
                self._connected = True

        except Exception as e:
            print(f"[MCPTool] 连接失败: {e}")
            self._running = False

    def _read_loop(self):
        """后台线程：持续读取 Server 消息，分发到 pending 或通知处理器"""
        while self._running:
            try:
                msg = self._read_message(timeout=1)
                if msg is None:
                    continue

                if "id" in msg:  #带id的是响应
                    # 响应：匹配 pending 请求
                    with self._lock:
                        future = self._pending.pop(msg["id"], None)
                    if future and not future.done():
                        future.set_result(msg)
                else:
                    # 通知：Server 主动推送
                    self._handle_notification(msg)
            except Exception as e:
                if self._running:
                    print(f"[MCPTool] 读取消息失败: {e}")
                break

    def _read_message(self, timeout: int = 30) -> dict | None:
        """读取一条完整的 JSON-RPC 消息（带超时）"""
        start = time.time()
        while time.time() - start < timeout:
            if not self._running:
                return None
            line = self._process.stdout.readline()
            if not line:
                time.sleep(0.01)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                # 不是完整 JSON，继续读
                continue
        return None

    def _request(self, method: str, params: dict, timeout: int = 30) -> dict | None:
        """发送 JSON-RPC 请求，等待响应"""
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            future = concurrent.futures.Future()
            self._pending[req_id] = future

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        try:
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            print(f"[MCPTool] 发送请求失败: {e}")
            return None

        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                self._pending.pop(req_id, None)
            print(f"[MCPTool] 请求超时: {method}")
            return None

    def _initialize(self):
        """MCP 握手：发送 initialize 请求，再发 initialized 通知"""
        response = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "CoreCoder", "version": "0.4.0"},
        })
        if not response:
            raise RuntimeError("MCP initialize 失败")

        # 发送 initialized 通知
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        self._process.stdin.write(json.dumps(notification) + "\n")
        self._process.stdin.flush()

    def _handle_notification(self, msg: dict):
        """处理 Server 主动推送的通知"""
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "notifications/message":
            level = params.get("level", "info")
            message = params.get("message", "")
            print(f"[MCP {level}] {message}")
        elif method == "notifications/progress":
            progress = params.get("progress", 0)
            total = params.get("total", 100)
            print(f"[MCP] 进度: {progress}/{total}")
        else:
            print(f"[MCP] 通知: {method}")

    def _call_tool(self, tool_name: str, arguments: Dict) -> str:
        if not self._connected:
            return "Error: MCP Server 未连接"

        response = self._request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if response is None:
            return "Error: 无响应"

        if "result" in response:
            content = response["result"].get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "")
            return str(response["result"])
        elif "error" in response:
            return f"Error: {response['error'].get('message', 'unknown')}"
        return "Error: 未知响应格式"

    def close(self):
        self._running = False
        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._connected = False

    def execute(self, **kwargs) -> str:
        """执行 MCP 工具（动态分派）"""
        # 从 kwargs 中提取 tool_name 和 arguments
        tool_name = kwargs.get("tool_name")
        args = kwargs.get("arguments", {})
        if not tool_name:
            return "Error: 缺少 tool_name 参数"
        return self._call_tool(tool_name, args)

    def get_expanded_tools(self) -> List[Tool]:
        """返回展开后的工具列表，供 MCPTool 自动注册到 ALL_TOOLS"""
        expanded = []
        for t in self._tools:
            # 为每个 MCP 工具创建一个动态代理工具
            expanded.append(_MCPProxyTool(
                name=t.get("name"),
                description=t.get("description", ""),
                mcp_tool=self,
                schema=t.get("inputSchema", {})
            ))
        return expanded


class _MCPProxyTool(Tool):
    """MCP 工具的代理类，每个 MCP 工具对应一个实例"""
    name = "mcp_proxy"
    description = "MCP 代理工具"

    def __init__(self, name: str, description: str, mcp_tool: MCPTool, schema: Dict):
        self._mcp_name = name
        self._mcp_tool = mcp_tool
        self._schema = schema
        self.name = f"mcp_{name}"
        self.description = f"[MCP] {description}"
        self.parameters = schema   # ← 补这一行

    def execute(self, **kwargs) -> str:
        return self._mcp_tool._call_tool(self._mcp_name, kwargs)