"""Core agent loop.

This is the heart of CoreCoder.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.
"""

import concurrent.futures
import inspect
import re
from .llm import LLM
from .tools import ALL_TOOLS
from .tools.base import Tool
from .tools.agent import AgentTool
from .tools.memory_tool import MemoryManager
from .prompt import system_prompt
from .context import ContextManager
from rich.console import Console

console = Console()

class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
    ):
        self.llm = llm
        self.tools = tools if tools is not None else ALL_TOOLS
        self._tool_by_name = {t.name: t for t in self.tools}
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self._system = system_prompt(self.tools)
        self._system_override = None

        # 从 LLM 客户端中提取 API Key / Base URL，用于长期记忆的 BGE-M3 向量化
        llm_api_key = getattr(llm, "api_key", None) or getattr(
            getattr(llm, "client", None), "api_key", None
        )
        llm_base_url = getattr(llm, "base_url", None) or getattr(
            getattr(llm, "client", None), "base_url", None
        )
        self.memory = MemoryManager(api_key=llm_api_key, base_url=llm_base_url)

        # wire up sub-agent capability
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self


    def set_mode(self, mode: str):
        """切换 Agent 模式"""
        from .prompt import code_review_prompt
        
        if mode == "review":
            self._system_override = code_review_prompt(self.tools)
            print(f"[Mode] Switched to: review")
        elif mode == "default":
            self._system_override = None
            print(f"[Mode] Switched to: default")
        else:
            print(f"[Mode] Unknown mode: {mode}")

    def _full_messages(self, memory: str = "") -> list[dict]:
        system = self._system_override if self._system_override else self._system
        if memory:
            system = f"{system}\n\n# 历史记忆（重要参考）:\n{memory}"
        return [{"role": "system", "content": system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        # 记忆管理指令在最前面拦截，不进入普通对话，也不触发 LLM 或偏好保存
        if "清空记忆" in user_input or "忘记一切" in user_input:
            cleared = self.memory.clear_all_memory()
            return "长期记忆已全部清空。" if cleared else "长期记忆未启用或清空失败，请检查日志。"

        match = re.search(r"忘记我关于(.+?)的偏好", user_input)
        if match:
            keyword = match.group(1).strip()
            deleted = self.memory.delete_memory_by_query(keyword)
            if deleted:
                return f"已删除与“{keyword}”相关的偏好记忆。"
            reason = getattr(self.memory, "last_delete_message", "")
            return reason or "未找到需要删除的偏好记忆。"

        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        # 调用 LLM 前召回相关长期记忆，拼接到 System Prompt 末尾
        recalled_memory = self.memory.recall_memory(user_input)

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(memory=recalled_memory),
                tools=self._tool_schemas(),
                on_token=on_token,
            )

            # print(f"[DEBUG] resp.content: {repr(resp.content)}")
            # print(f"[DEBUG] resp.tool_calls: {resp.tool_calls}")

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                self._maybe_save_memory(user_input)
                return resp.content

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            try:
                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)
                    result = self._exec_tool(tc)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    # parallel execution for multiple tool calls
                    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                    for tc, result in zip(resp.tool_calls, results):
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
            except KeyboardInterrupt:
                # Ctrl+C mid-execution would leave the assistant tool_calls
                # message without replies, poisoning the next request; backfill
                self._answer_pending_tool_calls(resp.tool_calls)
                raise

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

        self._maybe_save_memory(user_input)
        return "(reached maximum tool-call rounds)"

    def _maybe_save_memory(self, user_input: str):
        """根据关键词判断是否需要保存长期记忆。"""
        # 疑问句一律不保存，避免“我喜欢什么框架”被误当成偏好
        if self._is_question(user_input):
            return

        active_keywords = ("记住", "以后都", "请记下")
        preference_keywords = ("我喜欢", "我的项目是", "我习惯", "以后请用")
        try:
            if any(k in user_input for k in active_keywords):
                self.memory.save_memory(user_input)
            elif any(k in user_input for k in preference_keywords):
                self.memory.save_memory(f"[用户偏好] {user_input}")
        except Exception as e:
            print(f"[Agent] 保存长期记忆失败: {e}")

    def _is_question(self, text: str) -> bool:
        """判断输入是否像疑问句。"""
        question_markers = ("吗", "呢", "什么", "哪些", "？", "?", "怎么")
        return any(marker in text for marker in question_markers)

    def _exec_tool(self, tc) -> str:
        """Execute a single tool call, returning the result string."""
        tool = self._tool_by_name.get(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"

        if tc.name in ("edit_file", "write_file"):
            console.print(f"\n[yellow]即将修改文件: {tc.arguments.get('file_path')}[/yellow]")
            confirm = input("确认修改？(y/n): ").strip().lower()
            if confirm != "y":
                return "用户取消修改"
        # validate arguments first so a TypeError raised *inside* the tool isn't
        # mislabelled as a bad-arguments error from the caller
        try:
            inspect.signature(tool.execute).bind(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        try:
            return tool.execute(**tc.arguments)
            # result = tool.execute(**tc.arguments)
            # print(f"[DEBUG] Tool result: {repr(result[:200])}")  # 加这行
            # return result
        except Exception as e:
            #print(f"[DEBUG] Tool error: {e}")
            return f"Error executing {tc.name}: {e}"

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
        """Run multiple tool calls concurrently using threads.

        This is inspired by Claude Code's StreamingToolExecutor which starts
        executing tools while the model is still generating.  We simplify to:
        when the model returns N tool calls at once, run them in parallel.
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self._exec_tool, tc) for tc in tool_calls]
            return [f.result() for f in futures]

    def _answer_pending_tool_calls(self, tool_calls):
        """Backfill a tool reply for every call that didn't get one.

        OpenAI-compatible APIs reject a request where an assistant message has
        tool_calls without a matching tool reply for each id, so this keeps the
        history valid when execution is interrupted partway through.
        """
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for tc in tool_calls:
            if tc.id not in answered:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[interrupted]",
                })

    def reset(self):
        """Clear conversation history."""
        self.messages.clear()

    def close(self):
        """关闭 Agent 持有的资源。"""
        try:
            self.memory.close()
        except Exception as e:
            print(f"[Agent] 关闭长期记忆失败: {e}")

    # test: 测试 PR 审查功能
