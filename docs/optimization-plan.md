# Stock Agent 优化方案（参赛版）

> 状态：**待团队评审**。本文是对现有实现的透彻诊断 + 分阶段优化方案。
> 评审通过后按 Phase 落地。文末「待确认决策」需要团队先拍板。

---

## 0. 一句话结论

架构干净、工程底子好，但**研究深度被一个核心瓶颈卡住**：Agent 只在「搜索摘要 + 一份很薄的行情快照」上推理，**从未读过来源正文，也没有财务报表/历史价格/新闻/分析师预期**。
`report.txt` 实测可证：最终来源 S1–S5 被标注为「not used to substantiate conclusions」，整篇报告几乎只靠 prompt 里的假设和行情数据撑起来 —— 这就是当前质量天花板。

**优化主线**：先把「证据质量」做厚（正文抓取 + 财务数据），再把「Agent 推理」做深（综合判断 + 自我校验），最后把「Demo 表现力」做足（可视化 + 来源透明 + 导出）。同时把**响应速度**作为一条贯穿始终的硬指标（见文末「⚡ 响应速度专项」）。

---

## ✅ 实施进度（截至最近一次更新）

**团队已确认方向**：评审四维均衡（报告质量 + 技术创新/Agent 能力 + Demo 体验 + 合规负责任）；输出语言**跟随提问语言**；Demo 以 **Web 工作台**为主；模型用主办方 **miromind**（`mirothinker-1-7-deepresearch-mini`，OpenAI 兼容接口）。

**已落地（已提交到 `feat/miromind-and-report-download`）**：
- 环境：Python 3.12 + 全部依赖，测试基线 **50 passed**
- **miromind 接入**：多 provider 自动识别（优先级 miromind > deepseek > openai），默认走稳健的手写 JSON 解析路径；附能力探测脚本 `scripts/probe_miromind.py`
- **报告下载**：MD / PDF 双格式（fpdf2 + 系统 CJK 字体，中文正确渲染），Web 报告面板加下载按钮

**待团队 review 后统一实施**：本文下述各项 —— 证据深度、Agent 推理（synthesize/verify）、Web 可视化、持久化、评测，以及 **⚡ 响应速度专项**（团队本轮重点关注）。

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
`trafilatura`（正文抽取）、`readability-lxml`、`diskcache` 或 `requests-cache`（缓存）、`tenacity`（重试）、`fpdf2`（PDF，已用）、`pytest-asyncio`/`respx`（测试）、可选 `langsmith`（trace）。

---

# ⚡ 响应速度专项优化（团队本轮重点）

> 目标：在**不牺牲报告质量**的前提下，把一次完整研究的墙钟时间和**体感延迟**大幅压低。
> 竞赛维度上「响应快」和「UI 呈现好」是两条可并行加分线，本专项覆盖前者 + 部分体感优化。

## S1. 时间都花在哪（延迟拆解）

当前流程：`plan → market → search_web → extract → decide →（loop）→ write_report`

| 阶段 | LLM 调用 | 串行/并行 | 估算耗时* | 备注 |
|---|---|---|---|---|
| plan | 1 | — | ~3–6s | 单次模型调用 |
| market | 0 | yfinance+akshare 并行(15s 超时) | ~10–15s | **美股也跑 akshare，必失败但等满超时** |
| search_web | 0 | 多 query×多 provider 并行(~25s 截断) | ~5–25s | 长尾在超时 |
| **extract** | **最多 8** | **❗串行**（`for source in batch`） | **~8×单次** | **最大瓶颈** |
| decide | 1 | — | ~3–6s | |
| **loop** | ×`max_iterations`(默认 2) | — | **上面 search+extract+decide 再来一遍** | 翻倍 |
| write_report | 1 | — | ~8–20s | **一次性返回，无流式 → 体感"卡死"** |

\* 估算基于「单次 LLM ≈ 5s」的假设；miromind 是 deepresearch 模型，实测单次可能更慢，**串行项的放大效应也更严重**（本机因网关拦截暂无法实测，需在可联通环境用 `probe_miromind.py` 标定单次延迟）。

**结论**：最坏情况一次完整运行可达 **60–120s+**，其中 extract 串行 + 循环翻倍占大头，write_report 无流式拖垮体感。

## S2. 优化项（按 ROI 排序，均为**纯提速、不牺牲质量**）

| # | 优化 | 预期收益 | 工作量 | 风险 | 牺牲质量? |
|---|---|---|---|---|---|
| **P1** | **并行化 extract 的逐源 LLM 调用** | 🔴🔴🔴 墙钟从「8 次之和」→「最慢 1 次」 | 低 | 低 | 否 |
| **P2** | **market 按 `market_type` 路由 + 单源限时** | 🟠 market 15s→~3s | 低 | 低 | 否(更准) |
| **P3** | **缓存层**（搜索/正文/行情/LLM 按哈希，TTL） | 🔴 重复查询秒回（Demo 关键） | 中 | 低 | 否 |
| **P4** | **流式输出报告**（write_report 边生成边推 SSE） | 🔴🔴 体感"立即响应" | 中(动 UI) | 低 | 否 |
| **P5** | **plan→market/search 并行**（拓扑改造） | 🟠 省 min(market,search) | 中 | 中(LangGraph fan-in+loop 需谨慎) | 否 |
| **P6** | **HTTP 连接复用 + 超时自适应**（`requests.Session`、provider 早返回） | 🟡 削长尾 | 低 | 低 | 否 |

### P1 并行 extract（最高优先，先做）
- 把 `for source in batch` 里的 `_extract_source_notes(llm, ...)` 用 `ThreadPoolExecutor` 并发（`max_workers=min(8, len(batch))`）。
- **保持语义不变**：并发只用于"取 LLM 结果"，随后**仍按 batch 原顺序**串行做校验/去重/合并/`processed_source_ids` 标记 → 笔记顺序、跨源去重、失败隔离与"不重试失败源"全部与现状一致（现有 4 个 extract 测试可回归验证）。
- 线程安全：JSON 解析路径每次独立请求；真实 `ChatOpenAI` 并发 invoke 安全。

### P2 market 路由
- `us_equity`→仅 yfinance；`a_share`→akshare(+yfinance 兜底)；`macro`→跳过个股；`multi_market`→双源。
- 每个 fetch 单独超时；`datetime.utcnow()` 顺手改 timezone-aware。

### P3 缓存层
- key = `hash(provider + query/url/ticker + 关键参数)`；value 落磁盘（`diskcache`）带 TTL（搜索/正文 ~数小时，行情 ~分钟级）。
- 可选 LLM 响应缓存（key=prompt 哈希）——对**重复 Demo、回归评测**极有用。
- 提供"强制刷新"开关，避免演示时拿到过期数据。

### P4 流式报告（体感最大）
- `agent` 增加 `write_report` 的 token 级流式；新增 SSE 事件 `report_delta`；前端逐字渲染。
- 用户在 ~1s 内就看到报告开头在"打字"，而不是干等十几秒。

### P5 plan→market/search 并行（可选，收益依赖 P2）
- market 与 search 仅依赖 plan、写互不相交的 state（`market` vs `sources`），可并行扇出、扇入到 extract。
- 注意 LangGraph 在**循环**下的 fan-in 行为：decide 回边只应重跑 search，不应重跑 market。需小心设计，故列为中风险。
- 若 P2 已让 market 降到 ~3s，本项边际收益下降，**可后置**。

## S3. 质量↔速度 可调档（需团队定默认值）

这些**会影响覆盖深度**，建议做成预设档位让用户/评委现场切换：

| 参数 | 快档 | 标准档 | 深度档 |
|---|---|---|---|
| `max_iterations` | 1 | 2 | 3 |
| extract 来源数/轮 | 5 | 8 | 12 |
| 搜索超时 | 12s | 20s | 30s |
| 正文抓取(若上线) | 关 | Top-3 | Top-6 |

- 建议默认**标准档**，Demo 现场可一键切"快档"求响应速度，或"深度档"求报告质量。
- 智能化：`decide` 已能判断 `evidence_confidence`，可让"证据已充分"时**提前结束循环**（动态省一轮）。

## S4. 体感优化（UI 层，与「UI 呈现好」那条线协同）
- **流式报告**（P4）：最重要的体感项。
- **更细粒度进度**：来源逐条出现、笔记逐条出现（现为整步刷新）。
- **首字节优先**：plan 一生成就把"研究计划/子问题"亮出来，让用户立刻看到 Agent 在干活。
- **骨架屏 / 阶段预计耗时**：每步显示预估时间条，降低"卡住"焦虑。

## S5. 度量与验收
- 各节点已有 `_logger` 计时；补一个**端到端总延迟**指标，并在 SSE/快照里透出（UI 可显示"本次耗时 Xs"）。
- 评测集（Phase 5）记录 **P50/P95 延迟**，形成"优化前 vs 后"对比表 —— 答辩硬数据。
- 验收目标（建议，待实测标定后定稿）：标准档**端到端 P50 ≤ 现状的 40%**；首字节（plan 出现）≤ 5s；报告开始流式 ≤ 首个 token 可见。

## S6. 落地顺序建议
1. **P1 并行 extract**（最大赢面、最独立、可测）
2. **P2 market 路由**（顺带修 utcnow / akshare 浪费）
3. **P6 连接复用 + 超时自适应**（低成本削长尾）
4. **P3 缓存层**（Demo / 评测收益大）
5. **P4 流式报告**（体感飞跃，配合 UI 线）
6. **P5 plan→market/search 并行**（可选，视 P2 后收益）
7. **S3 档位预设 + S5 度量**（收尾，提供现场可调 + 答辩数据）
