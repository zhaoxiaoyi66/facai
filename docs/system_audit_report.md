# facai 系统审计、瘦身与性能收口报告

生成日期：2026-06-20

## 1. 审计结论

本轮审计定位为系统收口，不新增业务功能，不调整买区核心算法、周末价差公式、持仓结构、交易日志或错题本数据结构。

当前结论：

- 启动语法检查通过。
- 周末价差、技术指标/回撤、新闻雷达相关目标测试通过。
- 工作区开始时无业务改动，未发现 `.env`、local json、cache、SQLite、CSV、日志被 Git 跟踪。
- 本轮只做低风险修复：导航重新归组、周末价差一处英文盘后来源文案中文化、补充本报告。
- 未做高风险删除；疑似历史包袱先记录，不直接移除。

## 2. 页面与模块地图

| 页面 | 主要文件 | 定位 | 默认行为 | 审计结论 |
| --- | --- | --- | --- | --- |
| 决策总览 | `ui/dashboard.py` | 主工作台，展示观察池评分、买区状态、宏观缓存 | 默认读缓存，刷新按钮触发重请求 | 保留主入口；全量刷新仍需低频使用 |
| 组合持仓 | `ui/portfolio.py` | 持仓查看和持仓属性维护 | 默认读本地持仓和缓存 | 保留；本轮不改持仓结构 |
| 交易错题本 | `ui/discipline_review.py` | 快速记录交易错误和下次防线 | 默认快速记录优先，高级统计折叠 | 保留一级入口 |
| 交易日志 | `ui/trade_journal.py` | 记录交易行为 | 表单和历史记录分离 | 保留一级入口 |
| 个股研究 | `ui/stock_detail.py` | 单股深度研究、回撤规律、指标口径 | 默认加载当前股票，刷新按钮触发更新 | 保留；继续避免全池批量计算 |
| 价格位置 | `ui/ai_stock_radar.py` | 买区/位置扫描与单股入口 | 默认读缓存，单股详情再算深层信息 | 保留一级入口；用户侧不显示旧英文名 |
| 数据复核 | `app.py`, `ui/manual_review.py` | 后验验证和数据观察工具集合 | 作为分组入口 | 本轮恢复为分组：周末价差、新闻雷达、信号表现 |
| 周末价差 | `ui/weekend_spread.py`, `data/weekend_spread_*`, `data/overnight_price_provider.py`, `data/binance_equity_scan.py` | 独立 Binance 美股映射价差观察工具 | 默认读缓存，扫描/刷新/回测由按钮触发 | 独立边界基本成立 |
| 新闻雷达 | `ui/news_radar.py`, `data/news_radar.py` | 持仓/观察池新闻事件雷达 | 默认读新闻缓存，刷新和翻译由按钮触发 | 保留在数据复核下 |
| 信号表现 | `ui/signal_performance.py`, `data/signal_performance.py` | 后验验证系统信号表现 | 默认读缓存 | 保留在数据复核下 |
| 观察池 | `ui/watchlist.py` | 候选清单维护 | 低频维护 | 保持最后一个一级菜单 |

## 3. 导航审计

本轮按最新收口要求调整为：

1. 决策总览
2. 组合持仓
3. 交易错题本
4. 交易日志
5. 个股研究
6. 价格位置
7. 数据复核
   - 周末价差
   - 新闻雷达
   - 信号表现
8. 观察池

内部 route key 保持兼容：

- `PAGE_WEEKEND_SPREAD`
- `PAGE_NEWS_RADAR`
- `PAGE_SIGNAL_PERFORMANCE`
- `PAGE_AI_RADAR`

`AI Stock Radar` 仅作为旧链接兼容值保留，不作为用户侧显示名。

## 4. 词条与内部字段审计

已有 `ui/display_labels.py` 作为显示层统一映射，内部 key 不强行改名，避免破坏缓存、历史记录和测试。

合理保留在代码/测试/缓存结构里的内部 key：

- `candidate`
- `confirmed`
- `auto usable`
- `anchor_source`
- `FINAL`
- `DATA_INSUFFICIENT`
- `DATA_MISSING`
- `event_type`
- `sentiment_label`
- `impact_level`
- `risk_note`
- `mapping_confidence`

本轮低风险修复：

- 周末价差盘后来源中的 `FMP aftermarket trade` 改为 `FMP 盘后成交`。
- 周末价差盘后来源中的 `FMP aftermarket quote mid` 改为 `FMP 盘后报价中间价`。

仍需后续人工确认的历史包袱：

- `ui/weekend_spread.py` 文件体量偏大，存在多代历史 UI 函数和兼容字段，建议后续拆分展示层文件，而不是直接删函数。
- `data/ai_stock_radar.py` 和买区相关数据层仍有英文 enum，这是内部计算口径，不建议在本轮改动。

## 5. 周末价差模块边界

周末价差定位：

> 独立的 Binance 美股映射价差观察工具。

允许依赖：

- 通用配置和缓存。
- Binance / Alpaca / FMP / TradingView 相关行情 provider。
- 观察池只读列表，用作筛选和标签。
- 持仓只读列表，用作筛选和标签。
- 模块自己的 mapping、ignore、snapshot、monitor 缓存。

禁止依赖和写入：

- 不写交易日志。
- 不写错题本。
- 不写持仓结构。
- 不改买区算法。
- 不改价格位置判断。
- 不写个股研报配置。
- 不自动写入信号表现。
- 不生成买卖建议或自动交易。

审计结果：

- `ui/weekend_spread.py` 使用 `load_watchlist()` 和 `PortfolioPositionStore().list_active_positions()` 做只读筛选。
- 写入范围集中在周末价差自己的 snapshot、mapping、ignore、monitor cache。
- 未发现直接写主交易系统数据的路径。

## 6. 本地文件、缓存与密钥审计

本轮检查未发现以下敏感或本地文件被 Git 跟踪：

- `.env`
- `*.local.json`
- `config/*local*`
- `data/cache/*`
- `.cache/*`
- `*.sqlite`
- `*.db`
- `*.csv`
- `*.log`
- `data/manual_import/*`

`.gitignore` 已覆盖：

- `.env` 和 `.env.*`，保留 `.env.example`
- `config/*.local.json`
- `config/binance_symbol_mapping.local.json`
- `config/binance_symbol_ignore.local.json`
- `data/cache/`
- `data/manual_import/`
- `.cache/`
- SQLite / DB / CSV / log 类文件

## 7. 性能审计

总体规则：

- 页面打开默认读缓存。
- 重 API 请求必须由按钮触发。
- 诊断、补数、历史细节默认折叠或按需展示。

已确认的方向：

- 周末价差：实时观察默认读 snapshot；扫描 Binance、刷新价格、更新盘后锚点、运行历史回测均由按钮触发。
- 新闻雷达：页面默认读新闻缓存；刷新新闻才请求 FMP；补全中文翻译才调用翻译能力。
- 价格位置：默认读本地缓存；单股详情和回撤/指标信息按当前股票展示，不应全池批量计算。
- 个股研究：单股刷新由按钮触发。
- 决策总览：存在多种刷新模式，已区分只更新价格、重算技术指标、财报后刷新、强制全量刷新；强制全量仍应保持低频。

潜在热点：

- `ui/weekend_spread.py` 文件过重，Streamlit rerun 时维护成本高。
- `ui/dashboard.py` 页面 HTML 和刷新流程复杂，后续可继续拆出更小的组件。
- 全量测试耗时可控但不应每个小任务都全跑；继续遵守 `docs/testing_policy.md` 的目标测试策略。

## 8. 数据质量口径审计

- 技术指标：RSI 使用本地 Wilder RSI 14，EMA 使用标准 EMA；close 数据充足时技术指标口径可视为正常。
- 历史回撤：默认近 3 年；优先 adjusted close，没有 adjusted close 时使用 close 并做拆股/异常跳变检查。
- 周末价差：P0 为本周最后交易日盘后锚点，P1 为 Binance 周末窗口 max(high)，P2 为夜盘开盘窗口内首个有效 1m bar，并区分首分钟样本与延迟成交样本。
- Binance 映射：Binance 合约价格读取成功即映射可用；用户忽略清单优先，忽略后不进入刷新、实时观察、历史回测和监控。
- 新闻雷达：新闻缓存保留原文 URL、中文标题/摘要、事件分类、情绪、影响等级和价格反应摘要；页面默认读缓存。

## 9. 建议瘦身清单

本轮不直接删除，建议后续分任务处理：

1. 拆分 `ui/weekend_spread.py`
   - `ui/weekend_spread_realtime.py`
   - `ui/weekend_spread_backtest.py`
   - `ui/weekend_spread_mapping.py`
   - `ui/weekend_spread_monitor.py`
   - `ui/weekend_spread_tools.py`

2. 拆分 `ui/dashboard.py`
   - 刷新控制
   - 表格渲染
   - 详情 Drawer
   - 数据健康面板

3. 建立更严格的 UI 文案测试
   - 对页面渲染结果做内部 key 泄漏检查。
   - 避免 `None`、`anchor_source`、`candidate`、`confirmed` 等进入用户界面。

4. 周末价差旧字段清理
   - 先梳理 active row schema。
   - 再逐步移除只为老缓存服务的字段。
   - 不直接删除兼容字段。

## 10. 本轮验证结果

已通过：

- `python -m py_compile app.py`
- `python -m py_compile` 覆盖 `data/*.py`、`ui/*.py`、`tools/*.py`、`tests/*.py`，共 185 个文件，0 个错误。
- `pytest tests/test_weekend_spread.py -q -p no:cacheprovider --basetemp .pytest_tmp_weekend`：292 passed。
- `pytest tests/test_indicator_validation.py tests/test_stock_detail_ui.py tests/test_ai_stock_radar.py -q -p no:cacheprovider --basetemp .pytest_tmp_indicators`：162 passed。
- `pytest tests/test_drawdown_profile.py -q -p no:cacheprovider --basetemp .pytest_tmp_drawdown`：12 passed。
- `pytest tests/test_news_radar.py -q -p no:cacheprovider --basetemp .pytest_tmp_news`：14 passed。

后续提交前还需重新运行：

- `python -m py_compile app.py ui/weekend_spread.py`
- `pytest tests/test_weekend_spread.py -q -p no:cacheprovider --basetemp .pytest_tmp_weekend`
- `git diff --check`

## 11. 本轮提交记录

本报告生成时尚未提交。提交后请在最终回复中记录 commit hash。
