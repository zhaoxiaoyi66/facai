# facai 全系统审计、瘦身与性能收口报告

生成日期：2026-06-19

## 1. 基线状态

本轮开始时工作区已有未提交修改：

- `ui/weekend_spread.py`
- `tests/test_weekend_spread.py`

这些修改来自上一轮周末价差 V2 收口工作，本轮没有回滚。

基线验证：

- `python -m py_compile app.py ui/*.py data/*.py tests/*.py`：第一次因 PowerShell 未展开通配符失败；改用 PowerShell 展开文件列表后通过。
- `pytest tests/test_weekend_spread.py -q -p no:cacheprovider --basetemp .pytest_tmp`：通过。
- `pytest tests -q -p no:cacheprovider --basetemp .pytest_tmp`：运行 3 分钟后超时，未作为失败结论。建议后续拆分核心回归集。

## 2. 页面审计结果

| 页面名称 | route key / 内部 key | 左侧显示名 | 使用状态 | 重复/残留判断 | 建议 |
| --- | --- | --- | --- | --- | --- |
| 决策总览 | `dashboard` | 决策总览 | 高频使用 | 与右侧 Drawer/个股研究有展示重叠，但入口清晰 | 保留，继续压缩 Drawer 长文 |
| 组合持仓 | `portfolio` | 组合持仓 | 高频使用 | 账户净资产、持仓角色、主线叙事在同页，模块较多 | 保留，低频体检区继续折叠 |
| 交易复盘 / 投资笔记 | `discipline-review` | 交易复盘 | 高频使用 | 页面已收口为投资笔记和交易错题本，不再承担交易门禁 | 保留一级入口 |
| 交易日志 | `trade-journal` | 交易日志 | 高频使用 | 与错题本边界清晰：日志记录操作，错题本记录错误 | 保留 |
| 个股研究 | `detail` | 个股研究 | 高频使用 | 与研报中心共享买区/评分信息，但深度不同 | 保留 |
| 研报中心 | `ai-radar` / 内部仍兼容 `AI Stock Radar` | 研报中心 | 高频使用 | 用户侧旧 Radar/价格位置文案已收口，内部 key 暂不改 | 保留一级入口 |
| 周末价差 | `weekend-spread` | 周末价差 | 中频专项工具 | 历史包袱最多，但已作为一级专项工具 | 保留一级入口，继续清理旧备用函数 |
| 新闻雷达 | `news-radar` | 新闻雷达 | 中频信息雷达 | 默认读缓存，刷新/翻译由按钮触发 | 保留一级入口 |
| 数据复核 | `manual-review` | 数据复核 | 中低频使用 | 当前只保留信号表现等后验验证入口 | 保留分组，避免再塞专项模块 |
| 观察池 | `watchlist` | 观察池 | 低频维护入口 | 已放在最后，定位为候选池 | 保留最后 |

当前导航顺序符合目标：

1. 决策总览
2. 组合持仓
3. 交易日志
4. 交易复盘
5. 个股研究
6. 研报中心
7. 周末价差
8. 新闻雷达
9. 数据复核
   - 信号表现
10. 观察池

## 3. 词条混乱清单

### 3.1 合理内部 key，不需要改

这些字段仍会出现在数据层、测试和内部兼容逻辑里，不建议重命名：

- `AI Stock Radar`：仍作为 `ai-radar` 页面内部 key 的兼容值。
- `candidate` / `confirmed` / `mapping_confidence`：本地 mapping 文件兼容字段。
- `DATA_INSUFFICIENT` / `DATA_MISSING` / `BLOCK_CHASE` / `WAIT_CONFIRMATION`：买区和评分内部 enum。
- `anchor_source` / `FINAL` / `FALLBACK_REGULAR_CLOSE`：周末价差缓存和诊断内部字段。

### 3.2 UI 泄漏，已修复

- `AI Stock Radar` 页面标题最终显示为“研报中心”。
- “返回 Radar 列表”最终显示为“返回研报中心”。
- “AI 股票雷达研究”最终显示为“研报中心研究”。
- 观察池副标题中的旧 Radar / 价格位置叫法已改为“研报观察池”。
- 周末价差刷新状态中的英文 `Preparing Binance refresh` / `Refreshing Binance data` / `Refresh complete` 改为中文。
- 周末价差锚点状态中的 `FINAL` / `PROVISIONAL` 改为“已固定锚点 / 临时锚点”。
- 周末价差历史回放和映射审计中的 `confirmed mapping` / `candidate` / `trade-grade` / `estimated` 等用户可见词改为中文。
- 手动交易记录中的 `Paper Trade` / `Entry Plan` / `Hedge Plan` / `Exit Plan` 改为中文。

### 3.3 仍需后续确认的旧词条

- `ui/weekend_spread.py` 仍存在旧备用函数 `_render_backfill_audit_area`、早期 `_render_mapping_tab` 等重复定义。当前活跃入口未调用旧版本，但源码中仍有历史包袱。
- `data/ai_stock_radar.py` 中有英文 block reasons，例如 `current price is above the discipline buy zone`，目前由 UI 层翻译。建议暂不改数据层，避免破坏测试和历史快照。
- `backend/src/*` 有旧英文 buy zone 文案，似乎不在当前 Streamlit 主流程中。建议后续确认是否仍部署使用。

## 4. 已新增统一词条模块

新增：

- `ui/display_labels.py`

用途：

- 只做展示层映射。
- 内部 key 保持英文，避免破坏数据库、缓存、历史记录和测试。
- 可复用 `display_label()` 和 `replace_display_terms()` 逐步替换旧页面中的直写映射。

## 5. 瘦身审计

### 建议删除文件

本轮不建议直接删除文件。原因是当前系统测试覆盖较广，但旧模块和旧函数仍可能被测试、缓存或手动入口引用。直接删除风险高于收益。

### 建议合并模块

- 周末价差：`ui/weekend_spread.py` 已超过单页合理复杂度，建议后续拆成：
  - 实时观察展示模块
  - 历史回测展示模块
  - 映射管理展示模块
  - 数据源与补数工具展示模块
- 价格位置：`ui/ai_stock_radar.py` 同时承载列表、详情研报、性能探针和大量 HTML 拼接，建议后续拆出“报告视图渲染模块”和“列表视图渲染模块”。

### 建议保留但隐藏模块

- 周末价差的历史回放 / 数据质量 / 排除提醒：默认折叠，已符合低频诊断定位。
- TradingView Webhook / CSV / 手动补数工具：默认折叠，避免抢主屏。
- 手动交易记录：继续保留在高级设置里，不作为主流程。

### 暂不建议动的高风险模块

- `data/buy_zone_engine.py`：买区核心算法，测试和业务依赖多。
- `data/ai_stock_radar.py`：价格位置数据模型，内部 enum 多，不能简单改 key。
- `data/decision_log.py` / `data/portfolio_trade_entry.py`：交易记录与持仓入口，数据兼容要求高。
- SQLite repository 层：不做数据库迁移，不改历史字段。

## 6. 性能热点

### 发现

1. Streamlit tabs 会在一次 rerun 中执行所有 tab 的代码，周末价差页因此需要特别避免 tab 内自动请求外部数据。
2. 周末价差实时页当前默认先读 `weekend_spread_snapshot` 缓存；只有点击“刷新实时观察 / 更新盘后锚点”才请求 Binance / afterhours provider，方向正确。
3. 价格位置页默认读取本地 cache.sqlite 和运行期 cache，已有 `PerfProbe`，但 UI/报告 HTML 拼接仍较重。
4. 数据复核页和个股研究页存在大量 SQLite / JSON 读取与格式化，建议后续用页面级缓存和折叠区 lazy 计算继续优化。
5. 全量测试超过 3 分钟，说明回归集需要分层，否则每轮小修成本过高。

### 已做性能/体验优化

- 保持周末价差实时页默认缓存优先，不引入新的自动 API 请求。
- 将低频诊断和补数工具继续保持默认折叠。
- 将周末价差刷新进度提示改为中文，并明确“准备 / 进行中 / 完成”，减少用户误以为刷新卡住。
- 删除价格位置页一个不可达的旧重复分支，减少维护噪声。

### 建议后续优化

- 增加统一 `PERF_DEBUG=1` 开关，输出页面总耗时、缓存读取耗时、API 请求耗时、表格构建耗时。
- 将 `ui/weekend_spread.py` 的旧备用函数逐步删除或迁移到测试 fixture，减少源码扫描噪声。
- 周末价差 mapping 和 P0/P1/P2 诊断 DataFrame 只在展开 expander 后构建。
- 价格位置页报告 HTML 按 symbol 缓存并限制默认列表渲染条数。

## 7. 本轮实际代码修改

只做低风险展示层修改：

- 新增 `ui/display_labels.py`。
- `app.py`：
  - 侧边栏副标题改为中文。
  - 导航“研报中心”使用统一展示词条。
- `ui/ai_stock_radar.py`：
  - 页面标题改为“研报中心”。
  - 返回链接改为“返回研报中心”。
  - 研报壳标题改为“研报中心研究”。
  - 删除不可达旧重复分支。
- `ui/watchlist.py`：
  - 副标题从旧 Radar / 价格位置叫法改为“研报观察池”。
- `ui/weekend_spread.py`：
  - 周末价差刷新进度和完成提示中文化。
  - 历史回放、映射审计、手动交易记录、锚点状态词条中文化。
  - 保持缓存优先和按钮触发刷新。
- 测试同步：
  - `tests/test_ai_stock_radar.py`
  - `tests/test_weekend_spread.py`

## 8. 是否改了核心计算逻辑

没有。

本轮没有改：

- 买区算法
- Radar / 价格位置评分公式
- 周末价差 P0/P1/P2 公式
- 交易记录数据
- 持仓数据
- SQLite schema

## 9. 后续建议

1. 下一轮优先拆 `ui/weekend_spread.py`，这是当前最重的 UI 模块。
2. 为 `ui/display_labels.py` 增加测试，并逐步接入 dashboard、drawer、trade_journal 的状态展示。
3. 建立“核心测试集”：周末价差、价格位置、交易错题本、持仓入口，避免每次全量测试超时。
4. 对 `backend/src` 做一次是否仍使用的专项确认；如果 Streamlit 主系统不再依赖，应从主审计范围中降级为历史实验代码。
