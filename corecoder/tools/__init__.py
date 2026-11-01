"""Tool registry."""

from .bash import BashTool
from .read import ReadFileTool
from .write import WriteFileTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .agent import AgentTool
from .fetch import FetchUrlTool
from .ast_analyzer import ASTAnalyzerTool
from .git_diff import GitDiffTool
from .git_diff_file import GitDiffFileTool
from .mcp_tool import MCPTool
from .rag_tool import RAGSearchTool

ALL_TOOLS = [
    BashTool(),
    ReadFileTool(),
    #WriteFileTool(),
    #EditFileTool(),
    GlobTool(),
    GrepTool(),
    AgentTool(),
    FetchUrlTool(),
    ASTAnalyzerTool(),
    GitDiffTool(),
    GitDiffFileTool(),
    RAGSearchTool(),
]


def get_tool(name: str):
    """Look up a tool by name."""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None
