# CoreCoder 二次开发版 · 操作指南

> 本项目基于 MIT 开源的 [CoreCoder](https://github.com/he-yufeng/CoreCoder) 二次开发。在原有 Agent 主循环的基础上，扩展了代码审查模式、RAG 团队规范检索、AST 分析、Git Diff 感知、MCP 工具接入和自动化评测能力。

## 1. 功能概览

- **Agent 主循环**：用户输入 -> LLM + 工具调用 -> 执行工具 -> 继续循环，直到模型给出最终回答。
- **12 个内置工具**：文件读写、命令执行、代码检索、Git Diff、AST 分析、RAG 规范检索等。
- **代码审查模式**：`/review` 一键切换到专职代码审查提示词。
- **RAG 规范检索**：按团队规范构建向量库，审查时自动检索 `coding_rules.txt`。
- **MCP 扩展**：运行时通过 `/mcp` 接入外部 MCP Server。
- **自动化评测**：一键跑测试用例，自动生成 Markdown 评测报告。

## 2. 环境要求

- Python 3.10 或更高版本
- Git
- （可选）Node.js / npx：仅在使用 MCP 外部 Server 时需要

## 3. 安装

```bash
git clone https://github.com/yyn-hhh/CoreCoder.git
cd CoreCoder

# 创建并激活虚拟环境
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

# 安装项目
pip install -e .

# 如需 LiteLLM 后端，额外安装
pip install -e ".[litellm]"
```

## 4. 配置

复制环境变量模板：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```dotenv
OPENAI_API_KEY=你的APIKey
# OPENAI_BASE_URL=https://api.deepseek.com
# CORECODER_MODEL=deepseek-chat
```

常用配置项：

| 变量 | 必填 | 说明 |
|------|------|------|
| `OPENAI_API_KEY` | 是 | 主 API Key；也用于 RAG Embedding |
| `CORECODER_API_KEY` | 否 | 优先级最高，替代 `OPENAI_API_KEY` |
| `DEEPSEEK_API_KEY` | 否 | DeepSeek 单独配置时使用 |
| `OPENAI_BASE_URL` | 否 | OpenAI 兼容接口地址 |
| `CORECODER_MODEL` | 否 | 模型名，默认 `gpt-5.5` |
| `CORECODER_MAX_TOKENS` | 否 | 单次生成上限，默认 `4096` |
| `CORECODER_TEMPERATURE` | 否 | 采样温度，默认 `0` |
| `CORECODER_MAX_CONTEXT` | 否 | 上下文上限，默认 `128000` |
| `CORECODER_PROVIDER` | 否 | `openai` 或 `litellm` |

API Key 读取优先级：`CORECODER_API_KEY` > `OPENAI_API_KEY` > `DEEPSEEK_API_KEY`。

**注意：`.env` 已被 `.gitignore` 排除，不要把它提交到 GitHub。**

## 5. 快速开始

启动交互式终端：

```bash
corecoder
```

一次性模式，直接执行一个任务：

```bash
corecoder -p "检查当前仓库的代码风格问题"
```

指定模型 / 接口：

```bash
corecoder -m deepseek-chat --base-url https://api.deepseek.com
```

恢复已保存的会话：

```bash
corecoder -r <session_id>
```

查看版本：

```bash
corecoder -v
```

## 6. REPL 内置命令

| 命令 | 作用 |
|------|------|
| `/help` | 查看帮助 |
| `/reset` | 清空当前对话 |
| `/model` | 显示当前模型 |
| `/model <名称>` | 会话中切换模型 |
| `/tokens` | 查看 token 用量和费用估算 |
| `/compact` | 手动压缩上下文 |
| `/diff` | 查看本次会话修改过的文件 |
| `/save` | 保存会话 |
| `/sessions` | 列出已保存的会话 |
| `/review` | 切换到代码审查模式 |
| `/default` | 切换回默认模式 |
| `/mcp <server_command>` | 动态加载 MCP Server 工具 |
| `quit` / `exit` | 退出 |

输入技巧：

- `Enter`：提交消息
- `Esc + Enter`：换行，便于粘贴多行代码
- `Ctrl+C`：取消当前回合

## 7. 内置工具

| 工具名 | 用途 |
|--------|------|
| `bash` | 执行 shell 命令 |
| `read_file` | 读取文件 |
| `write_file` | 写入文件 |
| `edit_file` | 按唯一匹配做代码修改 |
| `glob` | 按文件名模式查找文件 |
| `grep` | 搜索文件内容 |
| `agent` | 派生子 Agent 处理子任务 |
| `fetch_url` | 抓取 URL 内容 |
| `ast_analyze` | 使用 AST 分析代码结构和依赖 |
| `git_diff` | 获取 Git 修改概览 |
| `git_diff_file` | 获取单个文件的完整 Diff |
| `search_coding_rules` | 检索团队 RAG 规范 |

## 8. 代码审查模式

在交互终端输入：

```text
/review
```

之后 Agent 会：

1. 使用 `git_diff` 获取改动文件列表；
2. 如果 Diff 被截断，使用 `git_diff_file` 查看完整内容；
3. 使用 `ast_analyze` 分析目标文件结构；
4. 读取目标文件全文；
5. 使用 `grep` 搜索 `eval`、`print`、裸异常等可疑模式；
6. 调用 `search_coding_rules` 检索团队规范；
7. 输出结构化审查报告，包括严重问题、改进建议和总结。

回到默认模式：

```text
/default
```

也可以在代码中直接切换：

```python
from corecoder.agent import Agent
agent.set_mode("review")
```

## 9. RAG 团队规范检索

规范源文件位于：

```text
corecoder/coding_rules.txt
```

仓库已携带构建好的向量库：

```text
corecoder/qdrant_data/
```

正常情况下无需重建。修改 `coding_rules.txt` 后，需要重建索引：

```bash
python -m corecoder.build_rag
```

重建时会调用 SiliconFlow 的 `BAAI/bge-m3` Embedding 模型，使用的 Key 同样是 `.env` 中的 `OPENAI_API_KEY`。如果未配置，`search_coding_rules` 会返回明确提示。

## 10. 自动化评测

测试用例定义在 `test_cases.json`，评测会自动切换到代码审查模式并统计耗时、token 消耗。

运行评测：

```bash
python run_eval.py
```

生成 Markdown 报告：

```bash
python generate_report.py
```

产物文件：

- `eval_results.json`：原始评测结果
- `review_report.md`：格式化后的 Markdown 评测报告

## 11. 二次开发说明

添加新工具：

1. 在 `corecoder/tools/` 下新建工具类，继承 `corecoder/tools/base.py` 中的 `Tool`；
2. 实现 `execute(**kwargs) -> str`；
3. 在 `corecoder/tools/__init__.py` 的 `ALL_TOOLS` 中注册。

修改团队规范：

1. 编辑 `corecoder/coding_rules.txt`；
2. 执行 `python -m corecoder.build_rag` 重建向量库。

接入外部 MCP Server：

```text
/mcp npx -y @modelcontextprotocol/server-filesystem .
```

Windows 下若 `npx` 无法启动，可以尝试：

```text
/mcp npx.cmd -y @modelcontextprotocol/server-filesystem .
```

## 12. 项目结构

```text
CoreCoder/
├── corecoder/
│   ├── agent.py                 # Agent 主循环
│   ├── cli.py                   # 命令行与 REPL
│   ├── config.py                # .env 配置加载
│   ├── llm.py                   # LLM 客户端
│   ├── context.py               # 上下文压缩
│   ├── prompt.py                # 系统提示词
│   ├── build_rag.py             # RAG 索引构建
│   ├── coding_rules.txt         # 团队规范源文件
│   ├── qdrant_data/             # RAG 向量库
│   └── tools/
│       ├── ast_analyzer.py      # AST 分析
│       ├── git_diff.py          # Git Diff 概览
│       ├── git_diff_file.py     # 单文件 Diff
│       ├── mcp_tool.py          # MCP 客户端
│       ├── rag_tool.py          # RAG 检索工具
│       └── ...
├── run_eval.py                  # 自动化评测
├── generate_report.py           # 评测报告生成
├── test_cases.json              # 评测用例
├── test_cases/                  # 示例代码文件
├── tests/                       # 单元测试
├── .env.example                 # 环境变量模板
└── pyproject.toml
```

## 13. 运行测试

```bash
pytest tests -q
```

编译检查：

```bash
python -m compileall -q corecoder tests
```

如果安装了 Ruff：

```bash
ruff check corecoder tests
```

## 14. 常见问题

**启动提示 `No API key found.`**

在 `.env` 中配置 `OPENAI_API_KEY`，或通过环境变量 / CLI 参数传入。

**RAG 提示 `OPENAI_API_KEY not set in .env`**

说明 RAG 检索时没有读取到 Key，检查 `.env` 是否位于项目根目录、变量名是否拼写正确。

**向量库损坏或检索结果异常**

删除 `corecoder/qdrant_data/` 后重新执行：

```bash
python -m corecoder.build_rag
```

**密钥安全问题**

`.env` 不要提交到 Git。如果密钥曾出现在 Git 历史中，应在对应平台吊销并重新生成。

## License

MIT License。本项目基于 [CoreCoder](https://github.com/he-yufeng/CoreCoder) 二次开发，使用和修改时请保留原作者与项目出处。
