# corecoder/tools/git_diff_file.py
import subprocess
from .base import Tool
from .git_diff import truncate_diff_blocks


class GitDiffFileTool(Tool):
    name = "git_diff_file"
    description = "获取单个文件在 Git 仓库中的完整改动内容。当 git_diff 截断时，用此工具查看特定文件的完整 diff。"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要查看改动的文件路径",
            },
            "base": {
                "type": "string",
                "description": "可选：对比基准（默认 HEAD，即查看工作区改动）",
                "default": "",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, base: str = "", head: str = "") -> str:
        try:
            # 检查是否是 Git 仓库
            check = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5
            )
            if check.returncode != 0:
                return "Error: 当前目录不是 Git 仓库"

            if base and head:
                cmd = ["git", "diff", base, head, "--", file_path]
            elif base:
                cmd = ["git", "diff", base, "--", file_path]
            else:
                cmd = ["git", "diff", "--", file_path]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30
            )

            if result.returncode != 0:
                return f"Error: git diff 失败\n{result.stderr}"

            output = result.stdout
            if not output.strip():
                return f"文件 {file_path} 无改动"

            # 单文件 diff 通常不会太长，但以防万一
            if len(output) > 15000:
                output = output[:15000] + "\n... (该文件 diff 过大，已截断)"
            return output

        except subprocess.TimeoutExpired:
            return "Error: git diff 超时"
        except Exception as e:
            return f"Error: {e}"