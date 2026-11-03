import json
import time
from corecoder.agent import Agent
from corecoder.llm import LLM
from corecoder.config import Config

def run_evaluation():
    print("🚀 开始自动化评测...")
    # 1. 初始化配置和 Agent
    config = Config.from_env()
    llm = LLM(model=config.model, api_key=config.api_key, base_url=config.base_url)
    agent = Agent(llm=llm)
    
    # 切换到审查模式（确保 RAG 和 AST 工具加载）
    agent.set_mode("review")

    # 2. 读取测试用例
    with open("test_cases.json", "r", encoding="utf-8") as f:
        test_cases = json.load(f)

    results = []
    for case in test_cases:
        print(f"\n[{case['id']}/6] 正在评测: {case['category']} ...")
        
        start_time = time.time()
        # 记录初始 Token
        initial_prompt_tokens = agent.llm.total_prompt_tokens
        initial_completion_tokens = agent.llm.total_completion_tokens
        
        # 运行 Agent
        try:
            response = agent.chat(case["input"])
            status = "Success"
        except Exception as e:
            response = str(e)
            status = "Failed"

        end_time = time.time()
        
        # 统计消耗
        used_tokens = (agent.llm.total_prompt_tokens - initial_prompt_tokens) + \
                      (agent.llm.total_completion_tokens - initial_completion_tokens)
        
        result = {
            "id": case["id"],
            "category": case["category"],
            "status": status,
            "time_cost_sec": round(end_time - start_time, 2),
            "tokens": used_tokens,
            "output_preview": response
        }
        results.append(result)
        print(f"✅ 状态: {status} | 耗时: {result['time_cost_sec']}s | Token: {used_tokens}")

    # 3. 保存评测结果
    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n🎉 评测完成！结果已保存到 eval_results.json")

if __name__ == "__main__":
    run_evaluation()