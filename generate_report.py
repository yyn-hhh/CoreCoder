import json
import os


def _clean_table_align(text: str) -> str:
    """压缩 Markdown 表格中的对齐填充空格，避免标题行出现大量空白。"""
    lines = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            lines.append(line)
            continue
        # 暂时保护单元格内的转义管道符，避免被 split 拆开
        safe = line.replace("\\|", "\x00")
        cells = [cell.strip() for cell in safe.split("|")]
        if cells and not cells[0]:
            cells.pop(0)
        if cells and not cells[-1]:
            cells.pop()
        # 分隔行统一为简短连字符
        if cells and all(set(cell) <= {"-", ":"} for cell in cells):
            lines.append("|" + "|".join("---" for _ in cells) + "|")
        else:
            lines.append("| " + " | ".join(cells).replace("\x00", "\\|") + " |")
    return "\n".join(lines)


def generate_markdown_report(json_file="eval_results.json", output_file="review_report.md"):
    if not os.path.exists(json_file):
        print(f"❌ 找不到 {json_file}，请先运行 run_eval.py 跑完评测。")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# 🤖 Agent 自动化评测报告\n\n")
        f.write(f"> 共计 {len(data)} 个测试用例\n\n---\n\n")
        
        for item in data:
            f.write(f"## 用例 {item['id']} - {item['category']}\n")
            # 兼容旧结果：tools_used 缺失或为空时显示“无”
            tools_used = item.get("tools_used") or []
            tools_text = ", ".join(tools_used) if tools_used else "无"
            # 只展示真正命中的 RAG 文本，“未找到相关规范。”等提示视为无召回
            rag_recalled = [
                text for text in (item.get("rag_recalled") or [])
                if isinstance(text, str) and text.startswith("- ")
            ]
            rag_text = f"{len(rag_recalled)} 条" if rag_recalled else "无"
            f.write(
                f"**状态**: {item['status']} | **耗时**: {item['time_cost_sec']}s | "
                f"**Token**: {item['tokens']} | **工具调用**: {tools_text} | "
                f"**RAG 召回**: {rag_text}\n\n"
            )
            
            # 写入完整的审查内容
            f.write("### 📝 审查输出\n\n")
            # 如果 response 存在就用 response，兼容你之前的 output_preview
            content = item.get('full_output', item.get('output_preview', '无输出'))
            content = _clean_table_align(content)
            f.write(content + "\n\n")

            # 写入本用例的 RAG 召回文本
            f.write("### 🔍 RAG 召回\n\n")
            if rag_recalled:
                for recalled in rag_recalled:
                    f.write(recalled + "\n\n")
            else:
                f.write("无\n\n")
            
            f.write("---\n\n")

    print(f"✅ 报告已生成：{output_file}")
    print("👉 请在 VS Code 中右键该文件，选择「打开预览 (Open Preview)」查看排版效果。")

if __name__ == "__main__":
    generate_markdown_report()
