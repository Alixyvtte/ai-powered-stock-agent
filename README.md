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
  - [llm.py]：DeepSeek

## 快速开始

1. 安装依赖

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev]"
```

2. 配置环境变量

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
2026-02-15 16:59:24,757 INFO stock_agent.trace plan:start
2026-02-15 16:59:25,727 INFO httpx HTTP Request: POST [https://api.deepseek.com/chat/completions](https://api.deepseek.com/chat/completions) "HTTP/1.1 200 OK"
2026-02-15 16:59:34,568 INFO stock_agent.trace plan:done seconds=9.81 subqueries=8
╭─────────────────────────────────────────────────────────── plan ───────────────────────────────────────────────────────────╮
│ topic: Deep dive on NVDA: key catalysts and risks over the next 6-12 months                                                │
│ subqueries: 8                                                                                                              │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 16:59:34,569 INFO stock_agent.trace market:start
2026-02-15 16:59:58,981 INFO stock_agent.trace market:done seconds=24.41 ticker=NVDA
╭────────────────────────────────────────────────────────── market ──────────────────────────────────────────────────────────╮
│ {                                                                                                                          │
│   "ticker": "NVDA",                                                                                                        │
│   "currency": "USD",                                                                                                       │
│   "price": 182.81,                                                                                                         │
│   "market_cap": 4450875342848.0,                                                                                           │
│   "trailing_pe": 45.25,                                                                                                    │
│   "forward_pe": 23.631657,                                                                                                 │
│   "dividend_yield": 0.02                                                                                                   │
│ }                                                                                                                          │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 16:59:58,986 INFO stock_agent.trace search_web:start iteration=0 queries=8
2026-02-15 17:01:01,933 INFO stock_agent.trace search_web:done seconds=62.95 new_sources=12 total_sources=12
╭──────────────────────────────────────────────────────── search_web ────────────────────────────────────────────────────────╮
│ sources: 12 (+12)                                                                                                          │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 17:01:01,936 INFO stock_agent.trace extract:start
2026-02-15 17:01:02,383 INFO httpx HTTP Request: POST [https://api.deepseek.com/chat/completions](https://api.deepseek.com/chat/completions) "HTTP/1.1 200 OK"
2026-02-15 17:01:12,407 INFO stock_agent.trace extract:done seconds=10.47 new_notes=0 total_notes=0
╭───────────────────────────────────────────────────────── extract ──────────────────────────────────────────────────────────╮
│ evidence notes: 0 (+0)                                                                                                     │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 17:01:12,409 INFO stock_agent.trace decide:start iteration=0 sources=12 notes=0
2026-02-15 17:01:12,559 INFO httpx HTTP Request: POST [https://api.deepseek.com/chat/completions](https://api.deepseek.com/chat/completions) "HTTP/1.1 200 OK"
2026-02-15 17:01:17,496 INFO stock_agent.trace decide:done seconds=5.09 need_more=True followups=5
╭────────────────────────────────────────────────────────── decide ──────────────────────────────────────────────────────────╮
│ need_more: True                                                                                                            │
│ followup_queries: 5                                                                                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 17:01:17,500 INFO stock_agent.trace search_web:start iteration=1 queries=5
2026-02-15 17:01:56,856 INFO stock_agent.trace search_web:done seconds=39.36 new_sources=11 total_sources=23
╭──────────────────────────────────────────────────────── search_web ────────────────────────────────────────────────────────╮
│ sources: 23 (+11)                                                                                                          │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 17:01:56,857 INFO stock_agent.trace extract:start
2026-02-15 17:01:57,373 INFO httpx HTTP Request: POST [https://api.deepseek.com/chat/completions](https://api.deepseek.com/chat/completions) "HTTP/1.1 200 OK"
2026-02-15 17:02:07,467 INFO stock_agent.trace extract:done seconds=10.61 new_notes=0 total_notes=0
╭───────────────────────────────────────────────────────── extract ──────────────────────────────────────────────────────────╮
│ evidence notes: 0 (+0)                                                                                                     │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 17:02:07,470 INFO stock_agent.trace decide:start iteration=1 sources=23 notes=0
2026-02-15 17:02:07,644 INFO httpx HTTP Request: POST [https://api.deepseek.com/chat/completions](https://api.deepseek.com/chat/completions) "HTTP/1.1 200 OK"
2026-02-15 17:02:12,490 INFO stock_agent.trace decide:done seconds=5.02 need_more=True followups=4
╭────────────────────────────────────────────────────────── decide ──────────────────────────────────────────────────────────╮
│ need_more: True                                                                                                            │
│ followup_queries: 4                                                                                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-02-15 17:02:12,492 INFO stock_agent.trace write_report:start
2026-02-15 17:02:12,665 INFO httpx HTTP Request: POST [https://api.deepseek.com/chat/completions](https://api.deepseek.com/chat/completions) "HTTP/1.1 200 OK"
2026-02-15 17:02:43,251 INFO stock_agent.trace write_report:done seconds=30.76 chars=6455
╭─────────────────────────────────────────────────────── write_report ───────────────────────────────────────────────────────╮
│ final_report chars: 6455                                                                                                   │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭──────────────────────────────────────────────────── Deep Search Report ────────────────────────────────────────────────────╮
│ **Research Memo: NVIDIA Corporation (NVDA)** │
│ **Date:** [Current Date]                                                                                                   │
│ **Ticker:** NVDA | **Price:** $182.81 | **Market Cap:** $4.45T                                                             │
│ **Currency:** USD | **Trailing P/E:** 45.25 | **Forward P/E:** 23.63 | **Dividend Yield:** 0.02%                           │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ ### **Executive Summary** │
│ NVIDIA’s financial and competitive position remains exceptionally strong, driven by unprecedented demand for AI            │
│ accelerators in data centers. Q3 FY2026 results set records, with Data Center revenue reaching $57 billion, underscoring   │
│ robust hyperscale investment in AI infrastructure [S2][S4]. The upcoming Blackwell GPU platform is expected to sustain     │
│ growth, supported by a deep CUDA ecosystem moat [S11][S12]. However, risks are concentrated in geopolitical exposure       │
│ (China export controls) and potential cyclical demand shifts. Over the next 6–12 months, key catalysts include Blackwell’s │
│ ramp and continued hyperscale capex allocation to AI, while risks center on regulatory tightening and competitive          │
│ pressures.                                                                                                                 │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ ### **Bull Case** │
│ 1. **Record Data Center Growth:** Data Center revenue hit $57 billion in Q3 FY2026, representing ~95% of total revenue and │
│ 60% year-over-year growth, indicating unrelenting demand for AI training and inference [S2][S4][S5].                       │
│ 2. **Blackwell Transition:** The Blackwell GPU platform (B100, B200, GB200) is launching with significant performance and  │
│ efficiency gains, and early indications suggest strong hyperscaler adoption, potentially driving the next wave of upgrades │
│ [S11][S12].                                                                                                                │
│ 3. **Ecosystem Moat:** NVIDIA’s CUDA software stack remains a significant barrier to entry, locking in developers and      │
│ enterprises despite emerging multi-vendor accelerator architectures [S8][S10].                                             │
│ 4. **Hyperscale Capex Priority:** Cloud providers continue to prioritize AI infrastructure investments, with 2026          │
│ predictions pointing to massive accelerator market growth [S6][S7][S9].                                                    │
│ 5. **Financial Strength:** The company guided Q4 revenue above consensus, reflecting sustained momentum and pricing power  │
│ [S2][S13].                                                                                                                 │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ ### **Bear Case** │
│ 1. **China Export Restrictions:** Tighter U.S. export controls have already eliminated NVIDIA’s data center GPU sales in   │
│ China, with estimated revenue impacts of $5.5–$8 billion annually [S17][S18][S21]. Further restrictions could expand to    │
│ other regions.                                                                                                             │
│ 2. **Competitive Threats:** Hyperscalers are increasingly developing in-house AI chips (e.g., Google TPU, AWS Inferentia), │
│ and industry efforts toward open standards could erode CUDA’s lock-in over time [S8][S9].                                  │
│ 3. **Valuation and Cyclicality:** At a forward P/E of ~23.6, expectations are high. Any slowdown in hyperscale AI capex or │
│ a cyclical correction in demand could pressure the stock [S13][S15].                                                       │
│ 4. **Concentration Risk:** ~95% of revenue comes from Data Center, exposing NVIDIA to a single demand driver [S5].         │
│ 5. **Supply Chain Disruptions:** While not currently cited as an issue, any delay in Blackwell supply or advanced          │
│ packaging could hinder growth.                                                                                             │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ ### **Key Catalysts (Next 6–12 Months)** │
│ 1. **Blackwell Ramp:** Full-scale production and customer shipments of Blackwell GPUs in 2026, potentially driving a       │
│ refresh cycle among hyperscalers and large enterprises [S11][S12].                                                         │
│ 2. **Hyperscale Capex Cycles:** Continued AI infrastructure investments by cloud providers in 2026, as indicated by market │
│ predictions [S6][S7][S9].                                                                                                 │
│ 3. **Software & Ecosystem Expansion:** Further monetization of AI software and services, leveraging the CUDA platform to   │
│ drive higher margins [S10][S23].                                                                                           │
│ 4. **Geopolitical Clarity:** Resolution or stabilization of U.S.-China chip trade policies could reduce uncertainty [S20]. │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ ### **Key Risks** │
│ 1. **Geopolitical:** Expansion or tightening of U.S. export controls beyond China, affecting other key markets             │
│ [S17][S19][S21].                                                                                                           │
│ 2. **Competitive:** Accelerated adoption of alternative AI accelerators or open software frameworks challenging CUDA       │
│ dominance [S8][S9].                                                                                                        │
│ 3. **Demand Cyclicality:** A potential peak in AI infrastructure spending by hyperscalers, leading to a digestion period   │
│ [S13][S15].                                                                                                                │
│ 4. **Execution:** Supply chain constraints or execution missteps in the Blackwell transition [S11].                        │
│ 5. **Regulatory:** Antitrust or regulatory scrutiny due to NVIDIA’s dominant market position [S22].                        │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ ### **Open Questions** │
│ 1. What is the precise timeline and customer adoption curve for Blackwell, and will it support Data Center revenue growth  │
│ amid a potential H100/H200 digestion phase? [S11][S12]                                                                     │
│ 2. How effectively can NVIDIA mitigate China revenue loss through other regions or product segments? [S17][S18][S21]       │
│ 3. To what extent are hyperscalers’ in-house AI chips cannibalizing NVIDIA’s potential market share? [S8][S9]              │
│ 4. Will the AI accelerator market growth in 2026 meet current lofty expectations? [S6][S7]                                 │
│ 5. How durable is the CUDA moat against emerging open-standard software ecosystems? [S8][S10]                              │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ ### **Sources** │
│ [S1] Seeking Alpha – Nvidia's Q2 2026 Results Show Strength In AI                                                          │
│ [S2] Futurum Group – NVIDIA Q3 FY 2026: Record Data Center Revenue, Higher Q4 Guide                                        │
│ [S3] MarketBeat – NVDA Q3 2026 Earnings Report                                                                             │
│ [S4] Beancount – NVIDIA Q3 FY2026 Earnings Analysis                                                                        │
│ [S5] Stock Analysis – NVIDIA Revenue by Segment                                                                            │
│ [S6] HPCwire – 2026 Semiconductor Predictions                                                                              │
│ [S7] AI CERTs – AI Accelerators Drive Massive Market Growth in 2026                                                        │
│ [S8] Medium – Assessing Viability of Multi-Vendor Accelerator Architectures                                                │
│ [S9] Business20Channel – Top 7 AI Chips Priorities Hyperscalers Accelerate for 2026                                        │
│ [S10] MLQ.ai – AI Chips & Accelerators                                                                                     │
│ [S11] Yahoo Finance – Will NVDA's Blackwell Platform Support Its Data Center Revenue                                       │
│ [S12] CUDO Compute – NVIDIA introduces Blackwell GPU lineup                                                                │
│ [S13] Economic Times – Nvidia shares surged 4% today                                                                       │
│ [S14] MarketBeat – HIVE Digital Technologies Q2 2026 Earnings                                                              │
│ [S15] Economic Times – Multibagger Stocks                                                                                  │
│ [S16] InfoSec Industry                                                                                                     │
│ [S17] The CFO – Nvidia faces $5.5b hit as US tightens chip export rules to China                                           │
│ [S18] CXO Digital Pulse – Nvidia Faces $8 Billion Sales Impact                                                             │
│ [S19] Investopedia – Nvidia Could Be Affected by Expanded US Export Controls                                               │
│ [S20] New York Times – Nvidia's Chief Says U.S. Chip Controls on China Have Backfired                                      │
│ [S21] Tekedia – Nvidia's China Market Share Crashes to Zero                                                                │
│ [S22] Statista – Nvidia statistics & facts                                                                                 │
│ [S23] National CIO Review – NVIDIA Continues Maximizing on AI Datacenter Growth                                            │
│                                                                                                                            │
│ ---                                                                                                                        │
│                                                                                                                            │
│ **For research purposes only. Not investment advice.** │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
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
