"""Search-and-replace file editing (Claude Code's key innovation).

The core idea: instead of sending whole-file rewrites or line-number patches,
the LLM specifies an *exact* substring to find and its replacement. The
substring must appear exactly once in the file, which eliminates ambiguity
and makes edits safe and reviewable.
"""

import difflib
from ._file_lock import get_file_lock, _changed_files
from pathlib import Path

from .base import Tool

# track files changed this session for /diff
#_changed_files: set[str] = set()


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file by replacing an exact string match. "
        "old_string must appear exactly once in the file for safety. "
        "Include enough surrounding context to ensure uniqueness."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find (must be unique in file)",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def execute(self, file_path: str, old_string: str, new_string: str) -> str:
        try:
            p = Path(file_path).expanduser().resolve() # 转换为绝对路径
            if not p.exists():
                return f"Error: {file_path} not found"

            lock = get_file_lock(str(p))
            with lock:
                try:
                    content = p.read_text(encoding="utf-8") # 读取文件内容
                except UnicodeDecodeError:
                    return f"Error: {file_path} is not a UTF-8 text file (edit_file only edits text files)"
                occurrences = content.count(old_string) # 统计 old_string 出现的次数

                if occurrences == 0:
                    preview = content[:500] + ("..." if len(content) > 500 else "")
                    return (
                        f"Error: old_string not found in {file_path}.\n" 
                        f"File starts with:\n{preview}"
                    )
                if occurrences > 1:
                    return (
                        f"Error: old_string appears {occurrences} times in {file_path}. "
                        f"Include more surrounding lines to make it unique."
                    )

                new_content = content.replace(old_string, new_string, 1) # 替换 old_string 为 new_string，只替换一次
                p.write_text(new_content, encoding="utf-8") # 写入新内容到文件，覆盖原内容
                _changed_files.add(str(p)) 

            # generate a unified diff so the user/LLM can see exactly what changed
            # (diff only processes strings, no file I/O, keep outside lock)
            diff = _unified_diff(content, new_content, str(p)) 
            return f"Edited {file_path}\n{diff}"
        except Exception as e:
            return f"Error: {e}"


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    """Generate a compact unified diff between old and new file content."""
    old_lines = old.splitlines(keepends=True)   #按行拆分成列表，因为 difflib.unified_diff 需要列表输入
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}", tofile=f"b/{filename}",
        n=context,
    )
    result = "".join(diff)  #diflib.unified_diff 返回的是一个迭代器，使用 join 将其连接成字符串
    # truncate enormous diffs
    if len(result) > 3000:
        result = result[:2500] + "\n... (diff truncated)\n"
    return result
