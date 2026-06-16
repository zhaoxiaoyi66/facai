# 美股筛选与买区系统

这是一个个人投资流程用的美股筛选 MVP。它帮助你查看观察名单、判断估值区间、识别风险旗标，并用分批买入价格规划仓位。

## 功能

- 总览仪表盘：显示股票代码、现价、市值、52 周高低点、回撤、RSI14、EMA20/50/200、20 日涨幅、估值指标、评级和机会/防追高提醒。TypeScript 后端方向会把评分拆成公司质量分、入场分、风险分三条线。
- 单股详情：显示价格图、EMA、RSI、评分拆分、风险旗标，以及基本面字段。缺失财务数据统一显示为 `N/A`，不会编造。
- 买区展示：Radar、Drawer 和个股研究统一读取 `data/buy_zone_engine.py` 与 `data/buy_zone_display.py` 的 canonical 买区结论。
- 防追高提醒：当动量过强但风险回报变差时提示不要追高。
- 左侧机会信号：当股票在可控回撤中估值改善时提示分批观察。
- 观察名单编辑器：配置文件为 `config/watchlist.yaml`。
- 本地 SQLite 缓存：默认缓存路径为 `data/cache.sqlite`。
- 数据源抽象层：当前正式数据源为 `FMPProvider`，默认按 FMP 付费基础版使用；Polygon、SEC Edgar 先保留接口。

## 安装与启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

## FMP API Key

项目根目录需要 `.env` 文件：

```text
FMP_API_KEY=你的_API_KEY
```

如果没有配置 key，系统会明确提示缺少 `FMP_API_KEY`，不会返回假数据。

## 项目结构

```text
app.py
buy_zone.py
config/watchlist.yaml
data/
  fmp_cache.py
  fmp_queue.py
  providers.py
  prices.py
  fundamentals.py
backend/
  src/
    fmpClient.ts
    rateLimiter.ts
    cacheService.ts
    refreshQueue.ts
    stockRepository.ts
    scoringEngine.ts
    technicalIndicators.ts
    valuationEngine.ts
    buyZoneEngine.ts
    riskEngine.ts
    apiCallLogger.ts
  schema.sql
indicators/
  technicals.py
scoring/
  valuation.py
  quality.py
  growth.py
  risk_flags.py
  signals.py
  total_score.py
ui/
  dashboard.py
  stock_detail.py
  buy_zone.py
  watchlist.py
tests/
  test_core_logic.py
```

## 测试

核心计算逻辑与 Streamlit、FMP API 请求分离。安装依赖后运行：

```powershell
python -m unittest discover -s tests
```

当前测试覆盖 RSI14、EMA20/50/200、20 日涨幅、52 周高点回撤、估值分、技术分、总分、防追高/左侧机会信号，以及三种估值方法的买区价格梯。

## 数据原则

核心原则是：不编财务数据，不用模型补数字。缺失财务值一律显示 `N/A`。任何受缺失字段影响的评分都只能视为临时参考，不等同于真实财务分析。

当前按 FMP 付费基础版优化：总览仪表盘使用摘要基本面，单股详情使用更完整的基本面快照。系统会读取 TTM 比率、关键指标、利润表、资产负债表、现金流、增长率和分析师预期等端点；如果某个端点暂时不可用，对应字段显示 `N/A`，不会让系统编造数字。

FMP 请求不会由前端刷新直接打出去。所有 FMP HTTP 请求都会先进入 `data/fmp_queue.py` 里的后端队列，按 Starter 计划保守限速：

```text
max_per_minute = 300
safe_per_second = 4
burst_per_minute = 240
```

页面刷新会优先读取 Streamlit 缓存和本地 SQLite 缓存；只有后端判断缓存过期或缺少对应深度的数据时，才会进入 FMP 请求队列。

FMP 端点级缓存 TTL：

```text
quote: 5 minutes
profile: 7 days
financials: 7 days
ratios: 7 days
keyMetrics: 7 days
historicalPrice: 1 day
news: 30 minutes
analystEstimates: 1 day
scores: 1 day
```

这些 TTL 在后端执行，不依赖前端刷新行为。

## TypeScript 后端架构

项目现在增加了一层 TypeScript 后端服务骨架，目标是把系统从“页面刷新就打 API”改成“后端统一调度、缓存、评分、落库”。前端或 UI 层以后只读取后端整理好的研究结果，不直接请求 FMP。

核心模块：

```text
backend/src/fmpClient.ts            FMP 客户端，只能在服务端初始化
backend/src/rateLimiter.ts          Starter 计划限速：4 次/秒，240 次/分钟
backend/src/cacheService.ts         本地文件缓存，按端点 TTL 过期
backend/src/refreshQueue.ts         刷新任务队列，避免页面加载触发全量刷新
backend/src/stockRepository.ts      股票、指标、评分、买区、刷新任务的 SQLite 仓储
backend/src/scoringEngine.ts        公司质量分、入场分、风险分，三条线分开
backend/src/technicalIndicators.ts  RSI、EMA、20 日涨幅、距 52 周高点
backend/src/valuationEngine.ts      PE、PS、EV/FCF、FCF 收益率等估值逻辑
backend/src/buyZoneEngine.ts        EPS / FCF / 收入三种估值法和分批买区价格梯
backend/src/riskEngine.ts           风险旗标和防追高提醒
backend/src/apiCallLogger.ts        API 调用日志
backend/schema.sql                  本地数据库表结构
```

后端已按 FMP Starter 计划预留这些刷新节奏：

```text
核心观察名单报价：交易时段每 5 分钟
扩展观察名单报价：每 30 分钟
历史价格：收盘后每天一次
基本面：每周一次，财报后刷新
Ratios / Key Metrics：每周一次，财报后刷新
新闻：观察名单每 30 分钟
评分：收盘后每天一次
买区：收盘后每天一次
```

TypeScript 后端检查：

```powershell
npm install --prefix backend
npm run --prefix backend typecheck
npm run --prefix backend build
```

TypeScript 后端默认本地库路径是 `data/research.sqlite`。刷新队列会把报价、历史价格、年报基础字段、ratios、key metrics、新闻、分析师预期、评分和买区结果写入仓储层。当前阶段它还没有完全替换现有 Streamlit UI；这样做是为了不把 MVP 一下子改成复杂生产系统，先把 FMP 调用边界、限速、缓存、队列和评分引擎打稳。

后续模块 TODO：
- 接入更完整的付费基本面和预期数据 API。
- 加入季度收入、利润率、自由现金流和资产负债趋势。
- 加入手动催化剂/叙事评分。
- 将仓位规则与当前持仓和风险预算联动。
- 当股票进入买区时增加提醒。
