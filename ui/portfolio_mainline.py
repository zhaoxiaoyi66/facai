from __future__ import annotations

from html import escape

import streamlit as st

from data.portfolio_narrative import DEFAULT_PORTFOLIO_NARRATIVE, PortfolioNarrativeStore


def render_current_mainline_module(store: PortfolioNarrativeStore | None = None) -> None:
    store = store or PortfolioNarrativeStore()
    _render_mainline_styles()
    narrative = store.get_narrative()
    edit_key = "portfolio_mainline_editing"

    if st.session_state.get(edit_key):
        _render_mainline_editor(store, narrative, edit_key)
        return

    header_cols = st.columns([1, 0.14])
    with header_cols[0]:
        st.markdown(_mainline_card_html(narrative), unsafe_allow_html=True)
    with header_cols[1]:
        st.write("")
        if st.button("编辑主线", key="portfolio-mainline-edit", width="stretch"):
            st.session_state[edit_key] = True
            st.rerun()


def _render_mainline_editor(store: PortfolioNarrativeStore, narrative: dict[str, str], edit_key: str) -> None:
    st.markdown(
        """
        <section class="portfolio-mainline-edit-head">
          <strong>当前主线</strong>
          <span>个人纪律备忘，不参与评分，也不阻止交易。</span>
        </section>
        """,
        unsafe_allow_html=True,
    )
    with st.form("portfolio-mainline-form"):
        main_thesis = st.text_area(
            "主判断一句话",
            value=narrative.get("main_thesis") or DEFAULT_PORTFOLIO_NARRATIVE["main_thesis"],
            height=72,
        )
        left, right = st.columns(2)
        with left:
            first_half_title = st.text_input(
                "上半场标题",
                value=narrative.get("first_half_title") or DEFAULT_PORTFOLIO_NARRATIVE["first_half_title"],
            )
            first_half_body = st.text_area(
                "上半场说明",
                value=narrative.get("first_half_body") or DEFAULT_PORTFOLIO_NARRATIVE["first_half_body"],
                height=96,
            )
        with right:
            second_half_title = st.text_input(
                "下半场标题",
                value=narrative.get("second_half_title") or DEFAULT_PORTFOLIO_NARRATIVE["second_half_title"],
            )
            second_half_body = st.text_area(
                "下半场说明",
                value=narrative.get("second_half_body") or DEFAULT_PORTFOLIO_NARRATIVE["second_half_body"],
                height=96,
            )
        portfolio_mapping = st.text_area(
            "组合映射说明",
            value=narrative.get("portfolio_mapping") or DEFAULT_PORTFOLIO_NARRATIVE["portfolio_mapping"],
            height=74,
        )
        save_col, reset_col, cancel_col = st.columns([1, 1, 1.2])
        save = save_col.form_submit_button("保存主线", width="stretch")
        reset = reset_col.form_submit_button("恢复默认", width="stretch")
        cancel = cancel_col.form_submit_button("取消编辑", width="stretch")

    if save:
        store.save_narrative(
            {
                "main_thesis": main_thesis,
                "first_half_title": first_half_title,
                "first_half_body": first_half_body,
                "second_half_title": second_half_title,
                "second_half_body": second_half_body,
                "portfolio_mapping": portfolio_mapping,
            }
        )
        st.session_state[edit_key] = False
        st.success("当前主线已保存。")
        st.rerun()
    if reset:
        store.reset_default()
        st.session_state[edit_key] = False
        st.success("当前主线已恢复默认。")
        st.rerun()
    if cancel:
        st.session_state[edit_key] = False
        st.rerun()


def _mainline_card_html(narrative: dict[str, str]) -> str:
    return (
        '<section class="portfolio-mainline-card">'
        '<div class="portfolio-mainline-head">'
        "<strong>当前主线</strong>"
        "<span>用于提醒当前投资主叙事，不参与评分，不直接影响交易动作。</span>"
        "</div>"
        f'<div class="portfolio-mainline-thesis">{escape(narrative.get("main_thesis") or "")}</div>'
        '<div class="portfolio-mainline-grid">'
        f'<article><b>{escape(narrative.get("first_half_title") or "")}</b><p>{escape(narrative.get("first_half_body") or "")}</p></article>'
        f'<article><b>{escape(narrative.get("second_half_title") or "")}</b><p>{escape(narrative.get("second_half_body") or "")}</p></article>'
        "</div>"
        '<div class="portfolio-mainline-mapping">'
        "<b>组合映射</b>"
        f'<span>{escape(narrative.get("portfolio_mapping") or "")}</span>'
        "</div>"
        "</section>"
    )


def _render_mainline_styles() -> None:
    st.markdown(
        """
        <style>
        .portfolio-mainline-card {
            margin: 0.35rem 0 0.75rem;
            padding: 0.82rem 0.92rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 9px;
            background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
            box-shadow: 0 8px 22px rgba(15, 23, 42, 0.035);
        }
        .portfolio-mainline-head,
        .portfolio-mainline-edit-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.8rem;
            margin-bottom: 0.56rem;
        }
        .portfolio-mainline-head strong,
        .portfolio-mainline-edit-head strong {
            color: #0f172a;
            font-size: 0.95rem;
            font-weight: 850;
        }
        .portfolio-mainline-head span,
        .portfolio-mainline-edit-head span {
            color: #64748b;
            font-size: 0.68rem;
        }
        .portfolio-mainline-thesis {
            margin-bottom: 0.72rem;
            color: #10233F;
            font-size: 1.02rem;
            font-weight: 820;
            line-height: 1.45;
        }
        .portfolio-mainline-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.55rem;
            margin-bottom: 0.62rem;
        }
        .portfolio-mainline-grid article {
            min-height: 86px;
            padding: 0.58rem 0.66rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.75);
        }
        .portfolio-mainline-grid b,
        .portfolio-mainline-mapping b {
            display: block;
            color: #0f172a;
            font-size: 0.8rem;
            font-weight: 820;
            margin-bottom: 0.22rem;
        }
        .portfolio-mainline-grid p,
        .portfolio-mainline-mapping span {
            margin: 0;
            color: #64748b;
            font-size: 0.72rem;
            line-height: 1.45;
        }
        .portfolio-mainline-mapping {
            display: flex;
            align-items: baseline;
            gap: 0.55rem;
            padding: 0.48rem 0.58rem;
            border: 1px solid rgba(37, 99, 235, 0.1);
            border-radius: 8px;
            background: rgba(37, 99, 235, 0.045);
        }
        .portfolio-mainline-mapping b {
            flex: 0 0 auto;
            margin-bottom: 0;
        }
        .portfolio-mainline-edit-head {
            margin: 0.35rem 0 0.55rem;
            padding: 0.58rem 0.7rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: #FFFFFF;
        }
        @media (max-width: 760px) {
            .portfolio-mainline-grid {
                grid-template-columns: 1fr;
            }
            .portfolio-mainline-head,
            .portfolio-mainline-edit-head,
            .portfolio-mainline-mapping {
                align-items: flex-start;
                flex-direction: column;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
