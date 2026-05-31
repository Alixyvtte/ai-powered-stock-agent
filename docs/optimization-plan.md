# Stock Agent 优化方案（参赛版）

> 状态：**待团队评审**。本文是对现有实现的透彻诊断 + 分阶段优化方案。
> 评审通过后按 Phase 落地。文末「待确认决策」需要团队先拍板。

---

## 0. 一句话结论

架构干净、工程底子好，但**研究深度被一个核心瓶颈卡住**：Agent 只在「搜索摘要 + 一份很薄的行情快照」上推理，**从未读过来源正文，也没有财务报表/历史价格/新闻/分析师预期**。
`report.txt` 实测可证：最终来源 S1–S5 被标注为「not used to substantiate conclusions」，整篇报告几乎只靠 prompt 里的假设和行情数据撑起来 —— 这就是当前质量天花板。

**优化主线**：先把「证据质量」做厚（正文抓取 + 财务数据），再把「Agent 推理」做深（综合判断 + 自我校验），最后把「Demo 表现力」做足（可视化 + 来源透明 + 导出）。

---

## 1. 现状诊断

### 1.1 架构与优点（保留）
- LangGraph 工作流节点分离清晰：`plan → market → search_web → extract → decide →（loop｜write_report）`。
- 来源分级（primary/secondary/aggregator）、来源打分启发式（`_source_priority`）、证据笔记校验（`_is_meaningful_note`）都已具备雏形。
- `evidence_confidence` + `refusal_reason` 的「负责任拒答」机制是加分项，要保留并放大。
- 并发搜索 + 超时截断、yfinance/akshare 双源行情、FastAPI + SSE 实时工作台、前端时间线 —— 工程完成度高于一般 demo。
- 图逻辑测试覆盖较好（extract 去重/配额/失败隔离等）。

### 1.2 核心瓶颈（按影响排序）

| # | 瓶颈 | 影响 | 证据 |
|---|------|------|------|
| **B1** | **只用搜索摘要，不抓正文** | 🔴 决定性 | Serper/SerpApi/DDG 只回 ~150–300 字 snippet；只有 Tavily 带 `raw_content`。extract 基本在 snippet 上做，结论空泛 |
| **B2** | **财务数据极薄** | 🔴 高 | market 只取 price/PE/市值等点值，无财报三表、历史价格、盈利历史、分析师预期、新闻 |
| **B3** | **无最终报告自检** | 🔴 高 | `write_node` 直接 `llm.invoke`，`[S#]` 引用不校验是否对应真实来源 → 幻觉引用风险 |
| **B4** | **无「综合判断」环节** | 🟠 中高 | 缺少结构化论点（明确结论/评级/多空权重/估值视角/置信度），直接写自由文本 |
| **B5** | **子查询被砍到 4 条** | 🟠 中 | `plan.subqueries[:4]`，与「deep research」定位不符，覆盖维度不足 |
| **B6** | **market 路由浪费** | 🟠 中 | 对每个 ticker 同时跑 yfinance + akshare，NVDA 这种也会触发 akshare（必失败 + 占线程/超时窗口）；`market_type` 已检测却没用于路由 |
| **B7** | **无缓存** | 🟠 中 | 每次重跑全量重新抓取，Demo 慢、烧额度、无法稳定复现 |
| **B8** | **单 run / 内存存储 / 无持久化** | 🟡 中 | 重启即丢，Demo 风险；无历史、无取消 |

### 1.3 具体问题清单（实现细节）
- `datetime.utcnow()` 已废弃（`market_data.py`、`deep_search_graph.py`），应改 `datetime.now(timezone.utc)`。
- `search_node` 里 `import concurrent.futures` 重复两次（415、423 行）。
- `decide_node` 把嵌套行情大 dict 直接拼进 prompt，噪声大、占 token。
- `dividendYield`（yfinance）口径不稳定（有时是百分数有时是小数），未归一化。
- 固定 `temperature=0.2`，未按节点区分（规划/抽取要低、写作可略高）。
- 除 JSON 解析重试外，LLM/网络调用无重试与退避。
- 无可观测性（无 LangSmith / 结构化 trace），调参靠盲猜。
- 输出强制英文，即使提问是中文（参赛若面向中文评委是减分项）。
- 无评测基线，无法量化「优化前后好了多少」。

---

## 2. 优化总览

| 主题 | 关键动作 | 影响 | 工作量 | 优先级 |
|------|----------|------|--------|--------|
| **T1 证据深度** | 正文抓取 + 财务数据扩展 + 新闻 | 🔴🔴🔴 | 中 | **P0** |
| **T2 Agent 推理** | 综合判断节点 + 报告自校验/修订节点 | 🔴🔴 | 中 | **P0** |
| **T3 Demo 表现力** | 来源/证据透明展示 + 图表 + 结论卡 + 导出 | 🔴🔴 | 中 | **P1** |
| **T4 工程健壮性** | 缓存 + 重试 + 持久化 + market 路由/修 bug | 🟠 | 低中 | **P1** |
| **T5 评测与可观测** | eval 评测集 + LangSmith trace + 测试扩展 | 🟠 | 低中 | **P2** |
| **T6 差异化亮点** | 多股对比 + 估值同行对照 + 催化日历 | 🟠 | 中 | **P2** |

---

## 3. 分阶段方案

### Phase 0 — 地基与速赢（低成本、立刻提质）
**目标**：修 bug + 拉满现有能力，为后续铺路。
1. **修复**：废弃 API、重复 import、`dividendYield` 归一化、market 异常处理。
2. **`market_type` 路由**：`us_equity`→仅 yfinance；`a_share`→akshare(+yfinance 备份)；`macro`→跳过个股；`multi_market`→双源。省时、去噪。
3. **子查询提到 5–6 条**且按维度（业务/财务估值/催化/风险/监管/宏观）覆盖，不再粗暴 `[:4]`。
4. **缓存层**（disk cache，TTL）：搜索结果、URL 正文、行情分别缓存 → Demo 秒级复现、省额度。
5. **重试 + 退避**：LLM 调用与 HTTP 抓取统一加 `tenacity` 风格重试；超时收敛。
6. **按节点配置 temperature** + 模型可注入（为多模型/换 Claude 预留）。

**验收**：同一 query 二次运行走缓存 <5s；US ticker 不再触发 akshare；测试全绿。

---

### Phase 1 — 证据深度（T1，质量天花板）⭐ 最高优先
1. **正文抓取节点 `fetch_content`**（新增，插在 search 与 extract 之间）
   - 对排序后 Top-N 来源用 `trafilatura`（首选）抓正文，失败回退 `readability-lxml`/BeautifulSoup。
   - 并发 + 超时 + 缓存；正文截断到合理长度（如 6–8k 字符）。
   - extract 改为在「正文」而非 snippet 上抽证据。
2. **财务数据扩展**：`MarketSnapshot` → 富化 `CompanyProfile`
   - 美股（yfinance）：财报三表（`financials`/`balance_sheet`/`cashflow`）、季度趋势、盈利历史、分析师预期与评级变动（`recommendations`）、历史价格 + 技术指标（均线/52周位置/波动）、`.news`。
   - A 股（akshare）：`stock_zh_a_hist` 历史、`stock_financial_abstract` 财务摘要、`stock_news_em` 新闻。
   - 行情对象统一加 `retrieved_at`、来源标注，UI/报告可引用。
3. **新闻检索增强**：plan 增加「近 N 天新闻/催化」类查询；可选接入新闻 API。
4. **证据结构升级**：`EvidenceNote` 增加 `metric`（量化数据）、`as_of`（时间）、`tier`（来源等级）字段，便于后续校验与展示。

**验收**：报告中至少 X 条关键结论带可追溯的正文级证据；行情面板含历史/财务/分析师维度。

---

### Phase 2 — Agent 推理与输出（T2）⭐ 最高优先
1. **综合判断节点 `synthesize`**（新增，decide 收敛后、write 之前）
   - 产出结构化 `InvestmentThesis`：`verdict`（看多/看空/中性）、`conviction`（评级/分数）、`bull_points`/`bear_points`（带权重与证据 id）、`valuation_view`、`key_catalysts`、`key_risks`、`confidence`。
   - 写报告时以 thesis 为骨架，保证「先有判断、再有论述」。
2. **报告自校验节点 `verify`**（新增，write 之后）
   - 校验每个 `[S#]` 是否映射真实来源；标注无证据支撑的论断；检查七大段落齐全、免责声明在位。
   - 不达标 → 路由回 `write_report` 做一次**修订**（最多 1–2 次），形成轻量对抗式自我审查。
3. **结构化报告 schema + 渲染**：段落与引用规范化，避免幻觉引用。
4. **语言匹配/双语**：检测提问语言，输出对应语言（或中英双语摘要）。
5. **多股对比模式**（差异化亮点）：同一问题并行分析 2+ ticker，输出对照结论 —— Demo 极出彩。

**验收**：`[S#]` 引用 100% 可追溯；评级/结论卡稳定产出；中文 query 默认中文报告。

---

### Phase 3 — Demo 表现力（T3）
> 比赛在 Demo 现场见真章，这一阶段直接影响观感分。
1. **来源 & 证据透明面板**：实时展示「找到的来源 / 抽到的证据笔记 / 每条引用对应链接」，让评委看见 Agent「在思考」。
2. **可视化图表**：历史价格走势、估值同行对照、财务趋势（前端 ECharts/Chart.js，或后端 matplotlib→base64）。
3. **结论卡 + 指标仪表盘**：把 `verdict / conviction / confidence / 关键指标` 做成醒目卡片，置顶展示。
4. **报告导出**：Markdown / PDF 一键下载。
5. **置信度可视化**：低/不足置信度时全局醒目提示（已有逻辑，强化展示）。

**验收**：一次完整 run 在 UI 上可看到来源链路 + 图表 + 结论卡 + 导出。

---

### Phase 4 — 工程健壮性（T4，与 P1 并行推进）
1. **持久化**：内存 RunStore → SQLite，支持历史列表、重启不丢、刷新可恢复。
2. **取消/重试控制**：UI 可中止当前 run。
3. **并发解锁**（可选）：从「单 run」放宽到有限并发（线程池 + 队列）。
4. **配置中心化**：把硬编码常量（batch size、超时、Top-N、缓存 TTL）收进 `AgentConfig`。

---

### Phase 5 — 评测与可观测（T5）
1. **Eval 评测集**：固定 8–12 个代表性 query，自动打分：
   - 引用可追溯率、段落完整率、拒答正确性、平均耗时、来源等级分布。
   - 形成「优化前 vs 优化后」对比表 —— 答辩时的硬数据。
2. **LangSmith trace**（或结构化日志）：每个节点耗时/输入输出可视化，便于调参与排障。
3. **测试扩展**：`fetch_content`、财务数据解析、`synthesize`/`verify` 节点、Web/SSE 集成。

---

## 4. 目标工作流（新架构）

```
understand(plan+ticker解析+语言+market_type)
        ↓
   market(富化:财报/历史/新闻/预期, 按market_type路由)
        ↓
   search_web(5-6查询, 提高caps)
        ↓
   fetch_content(新增: 抓正文)
        ↓
   extract(在正文上抽证据, 带量化/时间/等级)
        ↓
   decide(按"未验证假设"驱动追加查询) ──loop──┐
        ↓                                      │
   synthesize(新增: 结构化投资论点)            │
        ↓                                      │
   write_report(以thesis为骨架)               │
        ↓                                      │
   verify(新增: 引用/完整性校验) ──不达标──→ write_report(修订)
        ↓
       END
```

---

## 5. 比赛加分项清单
- ✅ 正文级证据 + 可追溯引用（直接拉开与「snippet 拼接型」demo 的差距）
- ✅ 自我校验/修订闭环（对抗式自审，技术叙事亮眼）
- ✅ 结构化投资论点 + 评级 + 置信度（产品感强）
- ✅ 多股对比模式（现场 demo 冲击力）
- ✅ 图表可视化 + 报告导出（观感分）
- ✅ Eval 量化对比（答辩硬证据）
- ✅ 负责任拒答（合规叙事，已有，放大）

---

## 6. 风险与取舍
- **正文抓取**：部分站点反爬/付费墙 → 必须优雅降级回 snippet，不能阻塞流程。
- **Token 成本**：正文 + 财务进 prompt 会显著增加 token → 用缓存 + 摘要压缩 + 上下文裁剪控制；这也是「换更强模型 vs 成本」的权衡点（见决策项）。
- **akshare 稳定性**：依赖东财网页接口，偶发慢/失败 → 超时 + 缓存 + 降级。
- **范围控制**：持久化/多并发/对比模式属增量，若工期紧可后置；P0/P1 必须先稳。

---

## 7. 待团队确认的关键决策（先拍这 4 个）

1. **评审重点**：报告质量 / 技术创新 / Demo 体验 / 均衡 —— 决定 Phase 优先级权重。
2. **输出语言**：英文 / 中文 / 跟随提问语言 / 中英双语。
3. **Demo 载体**：Web 工作台为主（重投 UI+图表）/ CLI+API / 两者都要。
4. **模型与预算**：继续 DeepSeek 为主 / 接入 Claude 提升推理 / OpenAI GPT-4o / 多模型可切换。
   - 影响：结构化输出可靠性、推理质量、正文进 prompt 的成本上限。

---

## 附：建议的依赖增项
`trafilatura`（正文抽取）、`readability-lxml`、`diskcache` 或 `requests-cache`（缓存）、`tenacity`（重试）、`weasyprint` 或前端打印（PDF）、`pytest-asyncio`/`respx`（测试）、可选 `langchain-anthropic`（Claude 路径）、可选 `langsmith`（trace）。
