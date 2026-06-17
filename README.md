# ZHX Research

个人美股研究、买区决策、交易复盘和组合纪律工具。

这个仓库只保存代码、UI、算法、页面、测试和配置模板。真实数据库、缓存、交易记录、持仓记录、API Key 和本地私密配置不进入 GitHub。

## 本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

如果使用启动脚本：

```powershell
.\start_zhx_research.bat
```

## 环境变量

在项目根目录复制 `.env.example` 为 `.env`，然后填写自己的密钥。

```powershell
Copy-Item .env.example .env
```

常用变量：

```text
FMP_API_KEY=你的_FMP_API_KEY
QWEN_API_KEY=你的_QWEN_API_KEY
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-flash
QWEN_SECOND_MODEL=qwen-plus
OPENAI_API_KEY=可选
DASHSCOPE_API_KEY=可选
POLYGON_API_KEY=可选
ALPHAVANTAGE_API_KEY=可选
BINANCE_USDM_BASE_URL=可选
BINANCE_SPOT_DATA_BASE_URL=可选
```

最小可运行配置通常只需要 `FMP_API_KEY`。AI 复核、盘后参考价、周末价差等功能如果缺少对应 key，会显示缺失提示，不会写入假数据。

## 需要自己创建的本地文件

这些文件不提交到 GitHub，需要在本地自己创建或保留：

```text
.env
.streamlit/secrets.toml
config/watchlist.yaml
config/portfolio_targets.yaml
config/trading_discipline.yaml
config/binance_symbol_mapping.local.json
data/cache.sqlite
data/research.sqlite
.cache/
backups/
logs/
```

`config/binance_symbol_mapping.example.json` 是模板，可以复制成：

```powershell
Copy-Item config\watchlist.example.yaml config\watchlist.yaml
Copy-Item config\portfolio_targets.example.yaml config\portfolio_targets.yaml
Copy-Item config\trading_discipline.example.yaml config\trading_discipline.yaml
Copy-Item config\binance_symbol_mapping.example.json config\binance_symbol_mapping.local.json
```

## GitHub 私有仓库备份范围

应该提交：

- `app.py`
- `data/`
- `ui/`
- `indicators/`
- `scoring/`
- `ai/`
- `backend/src/`
- `tests/`
- `docs/`
- `scripts/`
- `config/*.example.yaml`
- `config/*.example.json`
- `.env.example`
- `requirements.txt`
- `backend/package.json`
- `backend/package-lock.json`
- `README.md`

不应该提交：

- `.env`
- `.streamlit/secrets.toml`
- SQLite / DB 文件
- `.cache/`
- `data/cache/`
- `backups/`
- `logs/`
- `config/watchlist.yaml`
- `config/portfolio_targets.yaml`
- `config/trading_discipline.yaml`
- `config/*.local.*`
- 交易记录、持仓记录、缓存快照、API Key

## 数据库和代码恢复关系

数据库丢失不影响代码恢复。

从 GitHub 克隆后，应用会在本地重新创建需要的 SQLite 文件和缓存文件。恢复代码后需要重新配置 `.env`、本地 mapping 和必要的 watchlist/组合配置。

重要提醒：

- GitHub 私有仓库保存的是系统能力，不是个人交易数据云迁移。
- `data/cache.sqlite` 包含本地缓存、交易、持仓和复盘数据，不进入 GitHub。
- 如果需要保存真实数据库，应单独做加密离线备份，不和代码仓库混在一起。

## 敏感文件检查

提交前建议运行：

```powershell
git status --short
git ls-files | Select-String -Pattern '(^|/)(\.env|.*secret.*|.*secrets.*|.*\.sqlite|.*\.sqlite3|.*\.db|.*\.pem|.*\.key|.*\.p12|.*\.local\.|binance_symbol_mapping\.local\.json|cache\.sqlite|research\.sqlite)$'
git diff --check
```

如果第二条命令有输出，说明可能有敏感文件已经被 Git 跟踪。只从 Git tracking 移除、不删除本地文件：

```powershell
git rm --cached 路径
```

## 测试

优先运行与改动相关的 targeted tests：

```powershell
pytest tests/test_buy_zone_engine.py tests/test_buy_zone_display.py tests/test_ai_stock_radar.py -q -p no:cacheprovider
```

如果改动涉及页面入口、交易记录或周末价差，再按 `docs/testing_policy.md` 和 `scripts/select_tests.py` 选择更广的测试集。
