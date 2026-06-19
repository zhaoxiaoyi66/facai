from __future__ import annotations

import pandas as pd
import streamlit as st

from data.indicator_validation import indicator_validation_display_rows, validate_local_indicators


def render_indicator_validation_expander(symbol: str) -> None:
    with st.expander("指标口径", expanded=False):
        result = validate_local_indicators(symbol)
        rows = indicator_validation_display_rows(result)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        latest_date = result.get("latest_data_date") or "数据不足"
        daily_count = result.get("daily_count")
        daily_count_text = f"{int(daily_count)} 根日线" if isinstance(daily_count, (int, float)) else "数据不足"
        st.caption(f"最新数据日期：{latest_date}｜样本数量：{daily_count_text}")
