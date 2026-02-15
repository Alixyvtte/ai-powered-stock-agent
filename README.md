# stock-agent

基于 LangGraph 的股票投资研究 Deep Search Agent（初版）。

仅用于信息检索与研究辅助，不构成任何投资建议。

## 架构概览

这个项目把“深度研究”拆成一个可迭代的 LangGraph 工作流：先生成研究计划，再检索与提炼证据，必要时补充检索，最后生成带引用的英文研究报告。

- Agent 封装与入口
  - [agent.py]：`DeepSearchAgent` 封装图的构建与运行，返回 `final_report` 和完整 `state`
  - [cli.py]：命令行入口 `stock-agent`
- LangGraph 工作流（核心）
  - [deep_search_graph.py]：`plan -> market(optional) -> search_web -> extract -> decide(loop) -> write_report`
    - plan：把用户问题结构化成 `ResearchPlan`（topic / tickers / subqueries / assumptions）
    - search_web：按 subqueries（或 followup_queries）检索网页，生成 sources（带 id）
    - extract：从 sources 中提取可核查证据点 `EvidenceNote`（绑定 source_id）
    - decide：判断证据是否足够；不足则生成 followup_queries 进入下一轮检索
    - write_report：生成英文报告，并在关键陈述后使用 `[S#]` 引用来源编号
- 工具层
  - [web_search.py]：Tavily 优先；否则 DuckDuckGo 兜底；并做去重与截断
  - [market_data.py]：yfinance 市场快照（当前仅使用 plan 中第一个 ticker）
- 配置与模型
  - [config.py]：迭代次数、每次搜索结果数、超时等
  - [llm.py]：DeepSeek（OpenAI 兼容）优先，OpenAI 兜底

## 快速开始

1. 安装依赖

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev]"
```

2. 配置环境变量（二选一/可组合）

- DeepSeek

```bash
export DEEPSEEK_API_KEY="..."
export DEEPSEEK_MODEL="deepseek-chat"
export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
```

- Tavily（用于更稳定的网页检索；没有也可用 DuckDuckGo 兜底）

```bash
export TAVILY_API_KEY="..."
```

3. 运行

```bash
./.venv/bin/stock-agent "Deep dive on NVDA: key catalysts and risks over the next 6-12 months"
```

## 运行预期输出（Trace）

默认运行会输出每个步骤的 trace。如果你不想看到过程日志，可加 `--no-trace`。

下面是一段典型输出（内容会随网络与模型而变化；重点看步骤与计数）：

```text
2026-02-15 16:27:36 INFO stock_agent.trace search_web:start iteration=0 queries=8
2026-02-15 16:29:03 INFO stock_agent.trace search_web:done seconds=87.15 new_sources=12 total_sources=12
╭────────────── search_web ──────────────╮
│ sources: 12 (+12)                      │
╰────────────────────────────────────────╯

2026-02-15 16:29:03 INFO stock_agent.trace extract:start
2026-02-15 16:29:12 INFO stock_agent.trace extract:done seconds=8.58 new_notes=0 total_notes=0
╭──────────────── extract ───────────────╮
│ evidence notes: 0 (+0)                 │
╰────────────────────────────────────────╯

2026-02-15 16:29:12 INFO stock_agent.trace decide:start iteration=0 sources=12 notes=0
2026-02-15 16:29:17 INFO stock_agent.trace decide:done seconds=4.96 need_more=True followups=5
╭───────────────── decide ───────────────╮
│ need_more: True                        │
│ followup_queries: 5                    │
╰────────────────────────────────────────╯

2026-02-15 16:29:17 INFO stock_agent.trace search_web:start iteration=1 queries=5
2026-02-15 16:29:57 INFO stock_agent.trace search_web:done seconds=40.51 new_sources=12 total_sources=24
╭────────────── search_web ──────────────╮
│ sources: 24 (+12)                      │
╰────────────────────────────────────────╯

2026-02-15 16:30:11 INFO stock_agent.trace write_report:start
```

`search_web` 会最多循环多少次：
- 最大次数由 `STOCK_AGENT_MAX_ITERATIONS` 控制（默认 2）
- 实际是否继续取决于 `decide` 步骤里的 `need_more`

## 输出结构

- Executive Summary
- Bull Case / Bear Case
- Key Catalysts
- Key Risks
- Open Questions (to validate next)
- Sources (with [S#] references)
