import json
import os


def _clean_table_align(text: str) -> str:
    """压缩 Markdown 表格中的对齐填充空格，避免标题行出现大量空白。"""
    lines = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            lines.append(line.rstrip())
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


def _score_with_color(score):
    """按 0-100 分数区间返回带颜色标识的展示文本，避免 HTML 在 Markdown 中被转义。"""
    if score >= 80:
        marker = "🟢"
    elif score >= 60:
        marker = "🟡"
    else:
        marker = "🔴"
    return f"{marker} {score}"

def generate_markdown_report(json_file="eval_results.json", output_file="review_report.md"):
    if not os.path.exists(json_file):
        print(f"❌ 找不到 {json_file}，请先运行 run_eval.py 跑完评测。")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# 🤖 Agent 自动化评测报告\n\n")
        valid_scores = [
            item.get("score") for item in data
            if isinstance(item.get("score"), (int, float))
        ]
        if valid_scores:
            avg_score = round(sum(valid_scores) / len(valid_scores), 1)
            avg_score_text = f"{_score_with_color(avg_score)} 分 / 100 分"
            qualified_count = sum(1 for score in valid_scores if score >= 60)
            excellent_count = sum(1 for score in valid_scores if score >= 80)
            qualified_text = f"{qualified_count / len(valid_scores) * 100:.0f}%"
            excellent_text = f"{excellent_count / len(valid_scores) * 100:.0f}%"
        else:
            avg_score_text = "N/A"
            qualified_text = "N/A"
            excellent_text = "N/A"
        f.write(f"> 共计 {len(data)} 个测试用例 | 综合平均得分：{avg_score_text} | 合格率（>=60分）：{qualified_text} | 优秀率（>=80分）：{excellent_text}\n\n---\n\n")
        
        for item in data:
            f.write(f"## 用例 {item['id']} - {item['category']}\n")
            case_score = item.get("score")
            if isinstance(case_score, (int, float)):
                if case_score >= 80:
                    conclusion = "[✅ 优秀]"
                elif case_score >= 60:
                    conclusion = "[🟡 合格]"
                else:
                    conclusion = "[❌ 不合格]"
                f.write(f"**综合得分**: {_score_with_color(case_score)} / 100\n\n")
            else:
                conclusion = ""
                f.write("**综合得分**: N/A / 100\n\n")

            # 维度得分与报告质量得分（旧结果缺失时显示 N/A）
            tool_score = item.get("tool_score", "N/A")
            performance_score = item.get("performance_score", "N/A")
            stability_score = item.get("stability_score", "N/A")
            quality_score = item.get("quality_score", "N/A")
            f.write(f"**维度得分**: 工具 {tool_score} / 性能 {performance_score} / 稳定性 {stability_score} / 质量 {quality_score}\n\n")
            f.write(f"**报告质量得分**: {quality_score} / 30\n\n")

            # 兼容旧结果：tools_used 缺失或为空时显示“无”
            tools_used = item.get("tools_used") or []
            tools_text = ", ".join(tools_used) if tools_used else "无"
            # 只展示真正命中的 RAG 文本，“未找到相关规范。”等提示视为无召回
            rag_recalled = [
                text for text in (item.get("rag_recalled") or [])
                if isinstance(text, str) and text.startswith("- ")
            ]
            rag_text = f"{len(rag_recalled)} 条" if rag_recalled else "无"
            tool_check_status = item.get("tool_check_status", "N/A")
            tool_check_msg = item.get("tool_check_msg", "N/A")
            steps = item.get("steps", "N/A")
            redundancy = item.get("redundancy", "N/A")
            loop_circuit_breaker = item.get("loop_circuit_breaker", "N/A")
            # 新增 Pass@K 字段；旧结果缺失时回退到单次值
            e2e_status = item.get("e2e_status", item.get("status", "N/A"))
            pass_count = item.get("pass_count", "N/A")
            pass_k = item.get("pass_k", "N/A")
            avg_time = item.get("time_cost_sec", "N/A")
            avg_tokens = item.get("tokens", "N/A")
            f.write(
                f"**状态**: {e2e_status}{(' ' + conclusion) if conclusion else ''} | **通过**: {pass_count}/{pass_k} | "
                f"**平均耗时**: {avg_time}s | **平均Token**: {avg_tokens} | "
                f"**步骤**: {steps} | "
                f"**冗余**: {redundancy} | **熔断**: {loop_circuit_breaker} | "
                f"**工具调用**: {tools_text} | "
                f"**RAG 召回**: {rag_text} | **工具校验**: "
                f"{tool_check_status} ({tool_check_msg})\n\n"
            )
            
            # 写入完整的审查内容
            f.write("### 📝 审查输出\n\n")
            # 如果 response 存在就用 response，兼容你之前的 output_preview
            content = item.get('full_output', item.get('output_preview', '无输出'))
            content = _clean_table_align(content)
            f.write(content + "\n\n")

            # 裁判点评：取三次采样中第一次的评语展示
            judge_reasons = item.get("judge_reasons") or []
            judge_reason = judge_reasons[0] if judge_reasons else "N/A"
            f.write(f"> **裁判点评**: {judge_reason}\n\n")

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
