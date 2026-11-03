import json
import os

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
            f.write(f"**状态**: {item['status']} | **耗时**: {item['time_cost_sec']}s | **Token**: {item['tokens']}\n\n")
            
            # 写入完整的审查内容
            f.write("### 📝 审查输出\n\n")
            # 如果 response 存在就用 response，兼容你之前的 output_preview
            content = item.get('full_output', item.get('output_preview', '无输出'))
            f.write(content + "\n\n")
            
            f.write("---\n\n")

    print(f"✅ 报告已生成：{output_file}")
    print("👉 请在 VS Code 中右键该文件，选择「打开预览 (Open Preview)」查看排版效果。")

if __name__ == "__main__":
    generate_markdown_report()