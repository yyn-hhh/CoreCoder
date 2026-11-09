import json
import time
from corecoder.agent import Agent
from corecoder.llm import LLM
from corecoder.config import Config

# 端到端稳定性采样次数
PASS_K = 3

def _kwargs_match(actual: dict, expected: dict) -> bool:
    """判断实际调用参数是否包含所有期望的键值对。"""
    return all(actual.get(key) == value for key, value in expected.items())


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
        print(f"\n[{case['id']}/{len(test_cases)}] 正在评测: {case['category']} ...")

        attempts = []
        for _ in range(PASS_K):
            # 每次采样前清空会话，保证样本相互独立
            agent.reset()

            tools_used = []
            tool_calls = []
            rag_recalled = []

            def on_tool(name, kwargs):
                if name not in tools_used:
                    tools_used.append(name)
                tool_calls.append({"name": name, "kwargs": kwargs})

            def on_tool_result(name, _args, result):
                if name == "search_coding_rules" and result.startswith("- "):
                    rag_recalled.append(result)

            start_time = time.time()
            initial_prompt_tokens = agent.llm.total_prompt_tokens
            initial_completion_tokens = agent.llm.total_completion_tokens

            try:
                response = agent.chat(
                    case["input"],
                    on_tool=on_tool,
                    on_tool_result=on_tool_result,
                )
                exec_status = "Success"
            except Exception as e:
                response = str(e)
                exec_status = "Failed"

            end_time = time.time()
            used_tokens = (agent.llm.total_prompt_tokens - initial_prompt_tokens) + \
                          (agent.llm.total_completion_tokens - initial_completion_tokens)

            expected_tools = case.get("expected_tools") or []
            forbidden_tools = case.get("forbidden_tools") or []
            missing_tools = [tool for tool in expected_tools if tool not in tools_used]
            forbidden_used = [tool for tool in forbidden_tools if tool in tools_used]

            expected_tool_args = case.get("expected_tool_args") or []
            args_mismatch = []
            for expected_call in expected_tool_args:
                exp_name = expected_call.get("name")
                exp_kwargs = expected_call.get("kwargs") or {}
                if exp_name in tools_used and not any(
                    call["name"] == exp_name
                    and _kwargs_match(call.get("kwargs") or {}, exp_kwargs)
                    for call in tool_calls
                ):
                    args_mismatch.append(exp_name)

            tool_check_problems = [f"Missing: {tool}" for tool in missing_tools]
            tool_check_problems += [f"Args_Mismatch: {tool}" for tool in args_mismatch]
            tool_check_problems += [f"Forbidden: {tool}" for tool in forbidden_used]
            tool_check_status = "Passed" if not tool_check_problems else "Failed"
            tool_check_msg = "; ".join(tool_check_problems) if tool_check_problems else "OK"

            steps = len(tool_calls)
            redundancy = any(
                tool_calls[i] == tool_calls[i - 1]
                for i in range(1, len(tool_calls))
            )
            loop_circuit_breaker = "(reached maximum tool-call rounds)" in response

            attempts.append({
                "exec_status": exec_status,
                "time_cost_sec": round(end_time - start_time, 2),
                "tokens": used_tokens,
                "output_preview": response,
                "tools_used": list(tools_used),
                "rag_recalled": list(rag_recalled),
                "tool_check_status": tool_check_status,
                "tool_check_msg": tool_check_msg,
                "steps": steps,
                "redundancy": redundancy,
                "loop_circuit_breaker": loop_circuit_breaker,
            })

        # 端到端成功 = 执行成功且工具校验通过
        pass_count = sum(
            1 for attempt in attempts
            if attempt["exec_status"] == "Success"
            and attempt["tool_check_status"] == "Passed"
        )
        if pass_count == PASS_K:
            e2e_status = "Stable"
        elif pass_count == 0:
            e2e_status = "Failed"
        else:
            e2e_status = "Flaky"

        avg_time = round(sum(a["time_cost_sec"] for a in attempts) / PASS_K, 2)
        avg_tokens = round(sum(a["tokens"] for a in attempts) / PASS_K)

        tools_used = []
        for attempt in attempts:
            for tool in attempt["tools_used"]:
                if tool not in tools_used:
                    tools_used.append(tool)

        rag_recalled = []
        for attempt in attempts:
            for text in attempt["rag_recalled"]:
                if text not in rag_recalled:
                    rag_recalled.append(text)

        tool_check_status = (
            "Passed"
            if all(a["tool_check_status"] == "Passed" for a in attempts)
            else "Failed"
        )
        tool_check_problems = []
        for attempt in attempts:
            for message in attempt["tool_check_msg"].split("; "):
                if message and message != "OK" and message not in tool_check_problems:
                    tool_check_problems.append(message)
        tool_check_msg = "; ".join(tool_check_problems) if tool_check_problems else "OK"

        steps = sum(a["steps"] for a in attempts)
        redundancy = any(a["redundancy"] for a in attempts)
        loop_circuit_breaker = any(a["loop_circuit_breaker"] for a in attempts)

        # 报告展示最后一次成功输出，全部失败时展示最后一次尝试
        last_success = next(
            (a for a in reversed(attempts) if a["exec_status"] == "Success"),
            attempts[-1],
        )

        result = {
            "id": case["id"],
            "category": case["category"],
            "status": e2e_status,
            "e2e_status": e2e_status,
            "pass_count": pass_count,
            "pass_k": PASS_K,
            "time_cost_sec": avg_time,
            "tokens": avg_tokens,
            "output_preview": last_success["output_preview"],
            "tools_used": tools_used,
            "rag_recalled": rag_recalled,
            "tool_check_status": tool_check_status,
            "tool_check_msg": tool_check_msg,
            "steps": steps,
            "redundancy": redundancy,
            "loop_circuit_breaker": loop_circuit_breaker,
            "attempts": attempts,
        }
        results.append(result)

        tool_trace = ", ".join(tools_used) if tools_used else "无"
        rag_trace = f"{len(rag_recalled)} 条" if rag_recalled else "无"
        print(
            f"✅ 稳定性: {e2e_status} | 通过: {pass_count}/{PASS_K} | "
            f"平均耗时: {avg_time}s | 平均Token: {avg_tokens} | "
            f"步骤: {steps} | 冗余: {redundancy} | 熔断: {loop_circuit_breaker} | "
            f"工具调用: {tool_trace} | RAG 召回: {rag_trace} | "
            f"工具校验: {tool_check_status} ({tool_check_msg})"
        )

    # 3. 保存评测结果
    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    # 显式关闭 Agent 持有的 Qdrant 资源，避免解释器退出时触发 __del__ 导入错误
    agent.close()
    print("\n🎉 评测完成！结果已保存到 eval_results.json")

if __name__ == "__main__":
    run_evaluation()
