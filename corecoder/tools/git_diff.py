# corecoder/tools/git_diff.py
import subprocess
from .base import Tool


class GitDiffTool(Tool):
    name = "git_diff"
    description = "获取 Git 仓库中的代码改动。支持对比分支、提交，或查看工作区未提交的改动。"
    parameters = {
        "type": "object",
        "properties": {
            "base": {
                "type": "string",
                "description": "基准引用（分支名/提交哈希），默认 main。例如: main, HEAD~3",
                "default": "main",
            },
            "head": {
                "type": "string",
                "description": "目标引用，默认 HEAD（当前分支）。例如: feature-branch, HEAD",
                "default": "HEAD",
            },
            "include_content": {
                "type": "boolean",
                "description": "是否包含改动内容（diff）。默认 True。设为 False 只返回文件名列表",
                "default": True,
            },
        },
        "required": [],
    }

    def execute(self, base: str = "main", head: str = "HEAD", include_content: bool = True) -> str:
        try:
            # 先检查是否是 Git 仓库
            check = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, timeout=5
            )
            if check.returncode != 0:
                return "Error: 当前目录不是 Git 仓库"

            # 获取改动文件列表
            files_result = subprocess.run(
                ["git", "diff", base, head, "--name-only"],
                capture_output=True, text=True, timeout=30
            )
            if files_result.returncode != 0:
                return f"Error: git diff 失败\n{files_result.stderr}"

            files = [f.strip() for f in files_result.stdout.splitlines() if f.strip()]
            if not files:
                return f"没有改动（{base} 和 {head} 之间无差异）"

            output = f"## 改动文件 ({len(files)} 个)\n\n"
            for f in files:
                output += f"- {f}\n"

            if include_content:
                diff_result = subprocess.run(
                    ["git", "diff", base, head],
                    capture_output=True, text=True, timeout=60
                )
                diff_content = diff_result.stdout
                # 限制 diff 内容长度，避免输出太大
                if len(diff_content) > 8000:
                    diff_content = diff_content[:8000] + "\n... (diff 截断)"
                output += f"\n## Diff 内容\n```diff\n{diff_content}\n```"

            return output

        except subprocess.TimeoutExpired:
            return "Error: git diff 超时"
        except Exception as e:
            return f"Error: {e}"