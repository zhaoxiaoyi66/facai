# Facai 全系统 UI / 排版 / 信息层级审计

审计日期：2026-06-20  
范围：`app.py`、`ui/` 主要页面、周末价差各 tab、新闻雷达、交易复盘、数据复核、观察池。  
方式：本地 Streamlit 服务健康检查、`streamlit.testing.v1.AppTest` 页面执行抽样、源码静态扫描、内部字段/乱码关键字扫描。

## 执行摘要

本轮没有发现页面级 traceback。左侧导航结构已经符合当前工作流：周末价差和新闻雷达是一级模块，数据复核下只保留信号表现，观察池在最后。

本轮已修复的低风险问题：

- 数据复核：自动处理日志默认展开，改为默认收起，减少后台日志抢占第一屏。
- 数据复核：修复工作台 tab radio 的 Streamlit session_state 警告，避免运行日志污染。
- 新闻雷达：替换旧 `use_container_width` 参数为 `width="stretch"`，减少新版 Streamlit 兼容警告。
- 观察池：副标题去掉旧“价格位置观察池”叫法，改为“研报观察池”。
- 周末价差：删除一个无引用的旧前瞻记录 helper，保留单一折叠入口。

仍建议后续处理的较大问题：

- 固定侧栏仍依赖 `st.components.v1.html` 注入。Streamlit 已提示该 API 将移除，需要单独规划替代方案。
- 周末价差页面功能密度最高，虽然各区块多已折叠，但按钮和数据表数量仍偏多，后续可继续拆分“实时雷达 / 研究工具 / 数据维护”。
- 个股研究详情页表格和折叠区较多，适合后续做“摘要优先、证据折叠”的第二轮梳理。
- 观察池页面按钮数量多，后续可把单行操作收进更多菜单或批量操作区。

## 页面巡检

| 页面 | 当前定位 | 第一屏清晰度 | 主要风险 | 本轮处理 |
| --- | --- | --- | --- | --- |
| 决策总览 | 总览和当日动作入口 | 清晰 | 固定侧栏依赖旧组件 API | 记录为后续项 |
| 组合持仓 | 真实持仓、仓位和操作 | 较清晰 | 表单和展开区多，适合持续收敛 | 记录为后续项 |
| 交易日志 | 真实交易记录 | 清晰 | 卖出/复盘信息量大 | 记录为后续项 |
| 交易复盘 | 复盘和原则提醒 | 清晰 | 顶部原则区已突出；展开区数量可接受 | 无需改动 |
| 个股研究 | 单票研究详情 | 信息很全但密度高 | 17 个 dataframe、13 个 expander，首屏后内容重 | 记录为 P2 |
| 研报中心 | 单票研报和价格区间 | 清晰 | 旧内部名只保留在兼容映射 | 记录为合法内部兼容 |
| 周末价差 | 独立价差观察台 | 功能完整但最重 | 14 个 dataframe、40 个按钮、12 个 expander；需持续压缩低频工具 | 删除无引用 helper |
| 新闻雷达 | 持仓/观察池新闻辅助复核 | 清晰 | 普通新闻/详情 expander 多，但默认折叠 | 修复旧 Streamlit 参数 |
| 数据复核 | 后验验证和数据质量复核 | 第一屏较重 | 处理日志默认展开；radio session warning | 已修复 |
| 信号表现 | 信号后验表现 | 清晰 | 数据为空时文案可继续增强 | 记录为 P2 |
| 观察池 | 候选清单 | 基本清晰 | 单行按钮多；副标题旧称谓 | 已修复副标题 |

## 关键发现

### P0 / P1 已修复

1. 数据复核日志默认展开

- 问题：`处理日志`属于低频排障信息，默认展开会把复核工作台变成后台日志页。
- 修复：改为 `expanded=False`。
- 文件：`ui/manual_review.py`

2. 数据复核 radio session_state 警告

- 问题：默认 tab 逻辑同时写 widget key 并传入 index，Streamlit 会发出 warning。
- 修复：默认选择只写逻辑状态，不预写 radio widget key。
- 文件：`ui/manual_review.py`

3. 新闻雷达旧宽度参数

- 问题：`use_container_width` 在当前 Streamlit 会提示迁移到 `width`。
- 修复：按钮和 dataframe 改为 `width="stretch"`。
- 文件：`ui/news_radar.py`

4. 观察池副标题旧称谓

- 问题：“价格位置观察池”与当前“研报中心”命名不一致。
- 修复：改为“研报观察池”。
- 文件：`ui/watchlist.py`

5. 周末价差重复旧 helper

- 问题：`_render_backfill_audit_area_v2` 和旧 `_render_backfill_audit_area` 重复，旧函数无引用。
- 修复：合并为单一 `_render_backfill_audit_area`。
- 文件：`ui/weekend_spread.py`

### P1 / P2 报告项

1. 固定侧栏 API 技术债

- 现状：`app.py` 使用 `st.components.v1.html` 注入固定侧栏。
- 风险：Streamlit 运行时提示该 API 将移除。
- 建议：单独做一次侧栏实现迁移，避免和业务 UI 改动混在一起。

2. 周末价差仍是最高密度页面

- 现状：功能强，但按钮、表格、折叠区总量最高。
- 建议：继续保持“实时观察只放雷达判断；历史/研究/映射/数据维护分 tab”的原则。后续可把高级补数、前瞻记录和诊断工具进一步集中到维护抽屉。

3. 个股研究详情页证据层偏重

- 现状：AppTest 抽样显示多个 dataframe 和 expander。
- 建议：后续把核心结论、价格位置、技术口径、回撤档案分成摘要卡；原始证据表默认折叠。

4. 观察池单行操作过多

- 现状：AppTest 统计按钮数量较多。
- 建议：后续把编辑/星标/删除等低频操作收为“更多”或批量处理，默认列表只显示候选状态和下一步。

## 内部字段与编码扫描

扫描关键词包括：

- `None`
- `anchor_source`
- `auto usable`
- `confirmed`
- `candidate`
- `risk_note`
- `mapping_confidence`
- `DATA_INSUFFICIENT`
- `DATA_MISSING`
- `event_type`
- `sentiment_label`
- `impact_level`
- `AI Stock Radar`
- 常见中文乱码字符

结论：

- 未发现源码级真实乱码字符。PowerShell `Get-Content` 会把中文显示成乱码，但 UTF-8 读取正常。
- `AI Stock Radar` 只出现在旧路由兼容 alias 和显示标签翻译映射里，不是导航可见文案。
- `candidate / confirmed / anchor_source / event_type / sentiment_label / impact_level` 多数是内部字段或中文映射输入；当前应继续通过 `ui/display_labels.py`、页面 frame 构造函数和详情卡转成中文。
- `DATA_INSUFFICIENT / DATA_MISSING` 是数据状态内部值；可见 UI 应使用“数据不足 / 数据过期 / 休市中，价格有效”等中文状态。

## 页面打开与性能边界

抽样确认：

- 页面打开主要读本地缓存，没有发现页面加载即批量刷新 FMP/Binance 的明显行为。
- 周末价差、新闻雷达、监控复盘等高成本动作仍通过按钮触发。
- 本轮未做全量网络截图；浏览器插件在当前会话返回工具元数据错误，Chrome headless 未成功生成截图文件。已用 Streamlit AppTest 执行页面作为替代验证。

## 后续建议

1. 单独迁移固定侧栏实现，替代 `st.components.v1.html`。
2. 对个股研究做第二轮信息层级收敛：摘要优先、证据折叠。
3. 对观察池做批量操作和行内操作瘦身。
4. 对周末价差继续保持“实时雷达轻量、研究工具后置”的页面边界。
5. 为 UI 文案扫描补一个轻量测试，确保导航和主表不出现旧英文名、内部字段或乱码。
