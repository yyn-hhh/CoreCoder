# corecoder/tools/git_diff.py
import subprocess
from .base import Tool

def split_diff_blocks(diff_text: str) -> list[str]:
    """将 diff 文本按 @@ 块拆分成多个独立的 diff 块"""
    blocks = []
    current_block = []
    lines = diff_text.splitlines(keepends=True)

    for line in lines:
        if line.startswith("@@") and current_block:
            blocks.append("".join(current_block))
            current_block = []
        current_block.append(line)

    if current_block:
        blocks.append("".join(current_block))

    return blocks


def truncate_diff_blocks(diff_text: str, max_chars: int = 8000) -> str:
    """按完整 diff 块截断，保留尽可能多的完整块"""
    blocks = split_diff_blocks(diff_text)
    result = []
    current_len = 0
    total_blocks = len(blocks)

    for i, block in enumerate(blocks):
        block_len = len(block)
        # 检查加上当前块后是否超过限制（但至少要保留一个块）
        if current_len + block_len > max_chars and i > 0:
            result.append(f"\n... (剩余 {total_blocks - i} 个 diff 块已截断，可使用 git_diff_file 查看单个文件的完整改动)")
            break
        result.append(block)
        current_len += block_len

    return "".join(result)

class GitDiffTool(Tool):
    name = "git_diff"
    description = "获取 Git 仓库中的代码改动。不传参数默认查看工作区未提交的改动。"
    parameters = {
        "type": "object",
        "properties": {
            "base": {
                "type": "string",
                "description": "可选：基准分支/提交（如 main, HEAD~1）",
                "default": "",
            },
            "head": {
                "type": "string",
                "description": "可选：目标分支/提交（如 HEAD, dev）",
                "default": "",
            },
        },
        "required": [],
    }


    def execute(self, base: str = "", head: str = "") -> str:
        try:
            # 检查是否是 Git 仓库
            check = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                encoding="utf-8",  # 添加
                timeout=5
            )
            if check.returncode != 0:
                return "Error: 当前目录不是 Git 仓库"

            # 根据参数决定命令
            if base and head:
                cmd = ["git", "diff", base, head]
                label = f"{base} 和 {head} 的差异"
                files_cmd = ["git", "diff", base, head, "--name-only"]
            elif base:
                cmd = ["git", "diff", base]
                label = f"工作区相对于 {base} 的改动"
                files_cmd = ["git", "diff", base, "--name-only"]
            else:
                cmd = ["git", "diff"]
                label = "工作区未提交的改动"
                files_cmd = ["git", "diff", "--name-only"]

            # 获取文件列表
            files_result = subprocess.run(
                files_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",  # 添加
                timeout=30
            )
            if files_result.returncode != 0:
                return f"Error: git diff --name-only 失败\n{files_result.stderr}"

            files = [f.strip() for f in files_result.stdout.splitlines() if f.strip()]
            if not files:
                return f"没有发现改动（{label}）"

            output = f"## {label}\n\n"
            output += f"改动文件 ({len(files)} 个):\n"
            for f in files:
                output += f"- {f}\n"

            # 获取 diff 内容，使用按块截断
            diff_result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60
            )
            diff_content = diff_result.stdout
            if len(diff_content) > 8000:
                diff_content = truncate_diff_blocks(diff_content, 8000)

            output += f"\n## Diff 内容\n```diff\n{diff_content}\n```"
            return output

        except subprocess.TimeoutExpired:
            return "Error: git diff 超时"
        except Exception as e:
            return f"Error: {e}"