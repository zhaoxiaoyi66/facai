# 周末价差模块边界

## 模块定位

周末价差观察台是独立的 Binance 美股映射价差观察工具。

它只负责：
- 扫描 Binance 美股映射合约。
- 获取 Binance 当前价和周末高点。
- 获取美股本周最后交易日盘后锚点。
- 获取下周第一个交易日美股夜盘首分钟价格。
- 计算 P0 / P1 / P2 传导关系。
- 展示实时价差、历史回测、映射管理和数据质量诊断。

它不负责：
- 生成买入建议。
- 修改买区或价格位置判断。
- 修改个股研报。
- 修改组合持仓。
- 修改交易日志或交易错题本。
- 写入信号表现。
- 自动触发任何交易行为。

## 允许依赖

周末价差模块可以依赖：
- 通用配置读取。
- 通用日志、缓存和 JSON 文件工具。
- 通用行情 provider，例如 Binance、FMP、Alpaca、TradingView 补数缓存。
- 观察池股票列表的只读读取。
- 当前持仓股票列表的只读读取。
- 核心仓标签的只读读取，如果项目已有统一 getter。
- 本模块自己的 Binance 映射配置。

观察池、持仓和核心仓信息只允许用于页面筛选和标记，例如：
- 全部 Binance 美股映射。
- 我的观察池。
- 我的持仓。
- 核心仓。
- 异常偏离。

## 禁止依赖

周末价差模块不应依赖：
- 买区算法模块。
- 价格位置判断模块。
- 个股研报构建模块。
- 主交易日志写入模块。
- 交易错题本模块。
- 组合持仓写入模块。
- 信号表现写入模块。
- 主页面业务状态。

如果未来需要把周末价差结果展示到其他页面，只能展示只读摘要，并且必须标注：

> Binance 价差观察，不构成交易建议。

## 数据写入范围

周末价差模块允许写入自己的独立缓存和配置：
- `data/cache/weekend_spread_snapshot.json`
- `data/cache/weekend_backtest_results.json`
- `data/cache/weekend_backtest_klines.json`
- `data/cache/binance_equity_scan.json`
- `data/manual_import/tradingview_cache.json`
- `data/manual_import/tradingview/`
- `config/binance_symbol_mapping.local.json`

TradingView Webhook、CSV 导入和手动补数只能写入周末价差自己的缓存。

禁止写入：
- 交易日志。
- 交易错题本。
- 价格位置。
- 个股研报。
- 主买区缓存。
- 组合持仓。
- `signal_performance`。

## 和观察池的关系

观察池不是周末价差的扫描边界。

正确关系：
1. 先扫描或读取 Binance 美股映射全市场缓存。
2. 再用观察池、持仓、核心仓作为只读筛选条件。

错误关系：
- 用观察池决定全市场扫描范围。
- 用观察池 mapping 决定全市场结果。
- 周末价差反向修改观察池。

## 和主系统的边界

周末价差不会修改：
- 买区。
- 价格位置。
- 个股研究。
- 组合持仓。
- 交易日志。
- 交易错题本。
- 信号表现。

映射管理只维护本模块自己的 Binance mapping，不修改主系统股票 universe。

## Session State 规则

所有 Streamlit key 必须使用独立前缀：
- `weekend_spread_*`

禁止使用容易和主系统冲突的 key：
- `selected_symbol`
- `current_symbol`
- `report_context`
- `buy_zone`
- `portfolio_state`
- `trade_signal`

## 性能规则

周末价差页面打开时默认只读缓存，不自动触发重 API 请求。

所有重操作必须由用户按钮触发：
- 扫描 Binance 美股映射。
- 刷新实时价格。
- 更新盘后锚点。
- 运行历史回测。
- 夜盘数据源自检。
- CSV 导入。
- 手动补数。

## 后续扩展原则

- 周末价差结果默认只是观察数据。
- 不自动写入信号表现。
- 不自动创建交易计划。
- 不自动修改主系统任何评分或动作。
- 如果未来需要导出，必须使用显式按钮，例如“导出为观察记录”，且默认关闭。
