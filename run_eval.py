import json
import os
import re
import time
from openai import OpenAI
from corecoder.agent import Agent
from corecoder.llm import LLM
from corecoder.config import Config

# 端到端稳定性采样次数
PASS_K = 3

def _kwargs_match(actual: dict, expected: dict) -> bool:
    """判断实际调用参数是否包含所有期望的键值对。"""
    if not expected:
        return True
    return all(actual.get(key) == value for key, value in expected.items())


def _extract_review_file(tool_calls, user_input):
    """优先从 read_file 工具轨迹提取被审查文件，兼容 Git Diff 用例（无文件时返回空串）。"""
    for call in tool_calls:
        if call.get("name") == "read_file":
            file_path = (call.get("kwargs") or {}).get("file_path")
            if file_path:
                return file_path
    # 兜底：从自然语言输入中识别 .py 文件路径
    match = re.search(r"([A-Za-z0-9_./\\-]+\.py)", user_input)
    return match.group(1) if match else ""


def _judge_report_quality(original_code, rag_rules, agent_report):
    """调用独立裁判模型评估 Agent 审查报告，返回 0-10 分原始得分与幻觉标记。"""
    # 裁判配置与主 Agent 完全隔离，避免误用 DEEPSEEK 等主模型 Key
    api_key = os.getenv("JUDGE_API_KEY")
    base_url = os.getenv("JUDGE_BASE_URL", "https://api.siliconflow.cn/v1")
    model = os.getenv("JUDGE_MODEL", "Qwen/Qwen2.5-32B-Instruct")

    if not api_key:
        print("⚠️ [裁判] 未配置 JUDGE_API_KEY，使用默认及格分 5 分。")
        return {"score": 5, "hallucination": False, "reason": "裁判未配置", "details": {}}

    system_prompt = (
        "你是一名严苛的资深代码审查专家裁判。请基于【原始代码】和【参考规范】，对【Agent审查报告】进行多维度打分。\n"
        "评分维度（总计 10 分）：\n"
        "1. 准确性与防幻觉 (4分)：报告指出的问题必须真实存在于原始代码中。如果捏造了不存在的漏洞，此维度 0 分，且 hallucination 设为 true。\n"
        "2. 完整性 (3分)：请利用专业知识，重点检查是否漏报了逻辑错误、边界情况等语义级缺陷（无需检查关键字，底层代码已做）。\n"
        "3. 修复可用性 (2分)：报告不能只说“有问题”，必须给出具体的修复代码或替换方案。泛泛而谈扣分。\n"
        "4. 格式合规性 (1分)：必须包含标准 Markdown 表格，并正确引用行号。\n"
        "特殊规则：如果原始代码为空且 Agent 诚实报告文件不存在，给满分 10 分；若强行编造审查内容给 0 分。\n"
        "必须返回严格的 JSON 格式，字段为：score（0-10 整数）、hallucination（布尔值）、reason（字符串）、details（对象）。\n"
        "details 必须包含：accuracy_score（0-4）、completeness_score（0-3）、actionability_score（0-2）、format_score（0-1）。"
    )
    user_prompt = (
        f"原始代码：\n{original_code or '（无）'}\n\n"
        f"参考规范：\n{rag_rules or '（无）'}\n\n"
        f"Agent 审查报告：\n{agent_report or '（无）'}"
    )

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        details = result.get("details")
        if not isinstance(details, dict):
            details = {}
        return {
            "score": int(result.get("score", 0)),
            "hallucination": bool(result.get("hallucination", False)),
            "reason": str(result.get("reason", "")),
            "details": details,
        }
    except json.JSONDecodeError:
        print("⚠️ [裁判] 返回内容不是合法 JSON，按解析失败处理。")
        return {"score": 0, "hallucination": True, "reason": "裁判解析失败", "details": {}}
    except KeyboardInterrupt:
        print("⏹️ [裁判] 收到中断信号，评测即将停止。")
        raise
    except Exception as exc:
        print(f"⚠️ [裁判] 调用失败：{exc}")
        return {"score": 0, "hallucination": True, "reason": "裁判调用失败", "details": {}}


def _calc_tool_score(loop_circuit_breaker, tool_check_status, redundancy, avg_steps):
    """工具与轨迹分（满分 25），熔断直接归零，其余按扣分制计算。"""
    if loop_circuit_breaker:
        return 0
    score = 25
    if tool_check_status == "Failed":
        score -= 12
    if redundancy:
        score -= 4
    if avg_steps > 6:
        score -= 4
    return max(score, 0)


def _calc_performance_score(avg_time, avg_tokens):
    """性能与成本分（满分 15），按平均耗时和平均 Token 超限扣分。"""
    score = 15
    if avg_time > 30:
        score -= 6
    elif avg_time > 20:
        score -= 3
    if avg_tokens > 12000:
        score -= 6
    elif avg_tokens > 8000:
        score -= 3
    return max(score, 0)


def _calc_stability_score(pass_count):
    """稳定性分（满分 15），按 Pass@K 通过轮数计分。"""
    if pass_count == 3:
        return 15
    if pass_count == 2:
        return 8
    if pass_count == 1:
        return 4
    return 0


def _calc_ground_truth_score(avg_recall_rate):
    """硬指标得分（满分 15），按三轮平均召回率折算。"""
    return round(15.0 * avg_recall_rate, 1)


def _calc_judge_score(judge_avg_raw, has_hallucination):
    """裁判得分（满分 15），裁判原始分 10 分制乘 1.5；出现幻觉直接归零。"""
    if has_hallucination:
        return 0
    return round(judge_avg_raw * 1.5, 1)


def _calc_quality_score(avg_recall_rate, judge_avg_raw, has_hallucination):
    """报告质量分（满分 30）= 硬指标 15 分 + 裁判 15 分。"""
    return round(
        _calc_ground_truth_score(avg_recall_rate)
        + _calc_judge_score(judge_avg_raw, has_hallucination),
        1,
    )

def _calc_rag_score(avg_rag_hit_rate, avg_rag_mrr):
    """RAG 检索质量分（满分 15）：Hit@4 占 10 分，MRR 占 5 分。"""
    return round(10.0 * avg_rag_hit_rate + 5.0 * avg_rag_mrr, 1)


def run_evaluation():
    print("🚀 开始自动化评测...")
    # 1. 初始化配置和 Agent
    config = Config.from_env()
    llm = LLM(model=config.model, api_key=config.api_key, base_url=config.base_url)
    # 评测期间禁用长期记忆写入，防止测试数据污染长期记忆库
    config.disable_memory_write = True
    agent = Agent(llm=llm, disable_memory_write=config.disable_memory_write)
    
    # 切换到审查模式（确保 RAG 和 AST 工具加载）
    agent.set_mode("review")

    # 环境隔离：清空长期记忆库，避免历史记忆污染端到端评测
    agent.memory.clear_all_memory()
    print("🧹 已清空长期记忆库，保证评测环境纯净。")

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
                if name != "search_coding_rules":
                    return
                for rag_line in result.splitlines():
                    rag_line = rag_line.strip()
                    if rag_line.startswith("- "):
                        rag_recalled.append(rag_line)

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

            # 硬指标：基于 Ground Truth 期望漏洞做关键字召回，与裁判职责分离
            expected_vulns = case.get("expected_vulnerabilities") or []
            hit_count = sum(1 for v in expected_vulns if v in response)
            missed_vulns = [v for v in expected_vulns if v not in response]
            recall_rate = round(hit_count / len(expected_vulns), 4) if expected_vulns else 1.0
            attempt_ground_truth_score = round(15.0 * recall_rate, 1)

            # 从工具轨迹解析被审查文件，读取纯源码作为裁判参考
            review_file = _extract_review_file(tool_calls, case["input"])
            code_content = ""
            if review_file and os.path.exists(review_file):
                try:
                    with open(review_file, "r", encoding="utf-8") as f:
                        code_content = f.read()
                except OSError:
                    code_content = ""

            # 拼接本次召回的 RAG 规范，作为裁判检查幻觉的参考依据
            rag_rules = "\n".join(rag_recalled)

            # --- 计算检索层指标：Hit@4 与 MRR（基于文档 ID 精确断言） ---
            expected_rag_ids = case.get("expected_rag_ids") or []
            rag_hit_count = 0
            rag_mrr_score = 0.0
            for expected_id in expected_rag_ids:
                found_rank = -1
                # 匹配实际召回文本中的 "1." / "1、" / "1 " 等文档编号特征
                for rank, recalled_text in enumerate(rag_recalled, start=1):
                    if (
                        f"{expected_id}." in recalled_text
                        or f"{expected_id}、" in recalled_text
                        or f"{expected_id} " in recalled_text
                    ):
                        found_rank = rank
                        break
                if found_rank > 0:
                    rag_hit_count += 1
                    if found_rank <= 4:
                        rag_mrr_score += 1.0 / found_rank

            current_rag_hit = (
                rag_hit_count / len(expected_rag_ids) if expected_rag_ids else 1.0
            )
            current_rag_mrr = (
                rag_mrr_score / len(expected_rag_ids) if expected_rag_ids else 1.0
            )

            # 无具体代码文件（如 Git Diff 用例）时跳过裁判 API 调用，直接给默认及格分
            if not review_file:
                judge_result = {
                    "score": 7,
                    "hallucination": False,
                    "reason": "无直接代码文件，跳过裁判",
                    "details": {},
                }
            else:
                # 调用 LLM 裁判评估本轮报告质量
                judge_result = _judge_report_quality(code_content, rag_rules, response)

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
                "judge_score_raw": judge_result.get("score", 0),
                "judge_reason": judge_result.get("reason", ""),
                "hallucination": judge_result.get("hallucination", True),
                "hit_count": hit_count,
                "missed_vulns": missed_vulns,
                "recall_rate": recall_rate,
                "ground_truth_score": attempt_ground_truth_score,
                "judge_details": judge_result.get("details") or {},
                "rag_hit_rate": current_rag_hit,
                "rag_mrr": current_rag_mrr,
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
        # 平均步数：三轮单次步数的均值，避免把三轮总和误当单轮阈值比较
        avg_steps = round(sum(a["steps"] for a in attempts) / PASS_K, 1)
        redundancy = any(a["redundancy"] for a in attempts)
        loop_circuit_breaker = any(a["loop_circuit_breaker"] for a in attempts)

        # RAG 检索层指标取三轮平均
        avg_rag_hit_rate = round(sum(a["rag_hit_rate"] for a in attempts) / PASS_K, 4)
        avg_rag_mrr = round(sum(a["rag_mrr"] for a in attempts) / PASS_K, 4)

        # RAG 检索质量分（满分 15）
        rag_score = _calc_rag_score(avg_rag_hit_rate, avg_rag_mrr)

        # 汇总三次裁判结果；任一次出现幻觉，整体裁判分清零
        judge_avg_raw = round(sum(a["judge_score_raw"] for a in attempts) / PASS_K, 2)
        has_hallucination = any(a["hallucination"] for a in attempts)
        judge_reasons = [a["judge_reason"] for a in attempts]
        judge_details = [a.get("judge_details") or {} for a in attempts]

        # 平均召回率：三轮各次 recall_rate 的均值
        avg_recall_rate = round(sum(a["recall_rate"] for a in attempts) / PASS_K, 4)
        ground_truth_score = _calc_ground_truth_score(avg_recall_rate)
        judge_score = _calc_judge_score(judge_avg_raw, has_hallucination)
        quality_score = _calc_quality_score(avg_recall_rate, judge_avg_raw, has_hallucination)

        # 最终漏报：期望漏洞在三轮输出中从未命中才算漏报
        if expected_vulns:
            all_responses = "\n".join(a["output_preview"] for a in attempts)
            missed_vulns = [v for v in expected_vulns if v not in all_responses]
        else:
            missed_vulns = []

        # 量化评分：工具轨迹 + 性能 + 稳定性 + RAG 检索 + 报告质量，满分 100
        tool_score = _calc_tool_score(loop_circuit_breaker, tool_check_status, redundancy, avg_steps)
        performance_score = _calc_performance_score(avg_time, avg_tokens)
        stability_score = _calc_stability_score(pass_count)
        total_score = max(0, round(tool_score + performance_score + stability_score + rag_score + quality_score, 1))

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
            "avg_steps": avg_steps,
            "rag_hit_rate": avg_rag_hit_rate,
            "rag_mrr": avg_rag_mrr,
            "redundancy": redundancy,
            "loop_circuit_breaker": loop_circuit_breaker,
            "score": total_score,
            "total_score": total_score,
            "tool_score": tool_score,
            "performance_score": performance_score,
            "stability_score": stability_score,
            "rag_score": rag_score,
            "quality_score": quality_score,
            "recall_rate": avg_recall_rate,
            "missed_vulns": missed_vulns,
            "ground_truth_score": ground_truth_score,
            "judge_score": judge_score,
            "judge_details": judge_details,
            "judge_avg_raw": judge_avg_raw,
            "judge_reasons": judge_reasons,
            "has_hallucination": has_hallucination,
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
            f"RAG Hit@4: {avg_rag_hit_rate*100:.1f}% | MRR: {avg_rag_mrr:.3f} | "
            f"工具校验: {tool_check_status} ({tool_check_msg})"
        )

    # 3. 保存评测结果
    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    # 显式关闭 Agent 持有的 Qdrant 资源，避免解释器退出时触发 __del__ 导入错误
    agent.close()
    print("\n🎉 评测完成！结果已保存到 eval_results.json")

if __name__ == "__main__":
    try:
        run_evaluation()
    except KeyboardInterrupt:
        print("\n⏹️ 评测被用户中断，已停止。")
