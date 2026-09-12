# corecoder/tools/mcp_tool.py
import json
import subprocess
import threading
import time
from typing import Any, Dict, List
from .base import Tool


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
        self._init_connection()

    def _init_connection(self):
        """启动 MCP Server 并发现工具"""
        try:
            # 启动 Server 子进程（stdio 模式）
            self._process = subprocess.Popen(
                self._server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # 发送 tools/list 请求（简化版，实际 MCP 需要 JSON-RPC）
            # 这里只做示意，完整实现需处理 JSON-RPC 消息格式
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()

            # 读取响应（简化版）
            response_line = self._process.stdout.readline()
            if response_line:
                response = json.loads(response_line)
                if "result" in response and "tools" in response["result"]:
                    self._tools = response["result"]["tools"]
                    self._connected = True

        except Exception as e:
            print(f"[MCPTool] 连接失败: {e}")

    def _call_tool(self, tool_name: str, arguments: Dict) -> str:
        """调用 MCP Server 的 tools/call"""
        if not self._connected:
            return "Error: MCP Server 未连接"

        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        self._process.stdin.write(json.dumps(request) + "\n")
        self._process.stdin.flush()
        response_line = self._process.stdout.readline()
        if response_line:
            response = json.loads(response_line)
            if "result" in response:
                return response["result"].get("content", [{}])[0].get("text", "")
            elif "error" in response:
                return f"Error: {response['error'].get('message', 'unknown')}"
        return "Error: 无响应"

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

    def execute(self, **kwargs) -> str:
        return self._mcp_tool._call_tool(self._mcp_name, kwargs)