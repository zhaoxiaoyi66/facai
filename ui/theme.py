from __future__ import annotations

from html import escape

import streamlit as st
import streamlit.components.v1 as components


def render_global_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --zhx-bg: #F7F8FA;
            --zhx-surface: #ffffff;
            --zhx-surface-soft: #F3F4F6;
            --zhx-line: #E5E7EB;
            --zhx-line-strong: #D1D5DB;
            --zhx-text: #111827;
            --zhx-muted: #6B7280;
            --zhx-faint: #9CA3AF;
            --zhx-blue: #2563eb;
            --zhx-green: #138a5e;
            --zhx-yellow: #a16207;
            --zhx-orange: #c2410c;
            --zhx-red: #b42318;
            --zhx-shadow: 0 14px 34px rgba(15, 23, 42, 0.06);
            --zhx-action-bg: #F6F8FB;
            --zhx-action-border: rgba(15, 23, 42, 0.07);
            --zhx-action-text: #52657F;
            --zhx-action-text-strong: #0F172A;
            --zhx-table-head: #F8FAFC;
            --zhx-table-hover: #FBFCFE;
            --zhx-subtle-line: rgba(15, 23, 42, 0.055);
        }

        .stApp {
            background: var(--zhx-bg);
            color: var(--zhx-text);
            font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }

        [data-testid="stMainBlockContainer"] {
            max-width: 1440px;
            padding: 2rem 2rem 2.5rem;
        }

        [data-testid="stHeader"] {
            height: 0;
            min-height: 0;
            background: transparent;
            overflow: visible;
            pointer-events: auto;
        }

        [data-testid="stToolbar"],
        [data-testid="stStatusWidget"],
        [data-testid="stAppDeployButton"],
        [data-testid="stMainMenu"],
        [data-testid="stDecoration"] {
            visibility: hidden;
            height: 0;
            min-height: 0;
            opacity: 0;
            pointer-events: none;
        }

        [data-testid="collapsedControl"] {
            visibility: visible !important;
            opacity: 1 !important;
            pointer-events: auto !important;
            position: fixed !important;
            top: 0.75rem !important;
            left: 0.75rem !important;
            z-index: 999999 !important;
        }

        [data-testid="collapsedControl"] button {
            width: 2.55rem !important;
            height: 2.55rem !important;
            min-height: 2.55rem !important;
            border-radius: 10px !important;
            border: 1px solid rgba(148, 163, 184, 0.22) !important;
            background: #0B1220 !important;
            color: #F8FAFC !important;
            box-shadow: 0 12px 24px rgba(15, 23, 42, 0.16) !important;
            pointer-events: auto !important;
        }

        [data-testid="collapsedControl"] button::after {
            content: "导航";
            margin-left: 0.25rem;
            color: #F8FAFC;
            font-size: 0.72rem;
            font-weight: 760;
        }

        [data-testid="collapsedControl"] svg {
            color: #F8FAFC !important;
            fill: #F8FAFC !important;
        }

        [data-testid="stSidebarCollapseButton"] {
            display: none !important;
            pointer-events: none !important;
        }

        [data-testid="stSidebar"] {
            width: 240px !important;
            min-width: 240px !important;
            background: #0B1220;
            border-right: 1px solid rgba(255, 255, 255, 0.06);
        }

        [data-testid="stSidebarContent"] {
            width: 240px !important;
            padding: 1.5rem 1rem;
        }

        [data-testid="stSidebar"] * {
            color: #F8FAFC;
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
            color: #94A3B8;
            font-size: 0.78rem;
        }

        [data-testid="stSidebar"] [data-testid="stRadio"] > label {
            display: none;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label {
            position: relative;
            min-height: 40px;
            border-radius: 10px;
            padding: 0.48rem 0.7rem 0.48rem 0.9rem;
            margin-bottom: 0.22rem;
            border: 1px solid transparent;
            color: #94A3B8;
            transition: background 140ms ease, border-color 140ms ease, color 140ms ease;
        }

        [data-testid="stSidebar"] [role="radiogroup"] input,
        [data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {
            display: none !important;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label p {
            color: #94A3B8 !important;
            font-size: 0.88rem !important;
            font-weight: 650;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: rgba(59, 130, 246, 0.12);
            border-color: rgba(96, 165, 250, 0.18);
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked)::before {
            content: "";
            position: absolute;
            left: 0;
            top: 9px;
            bottom: 9px;
            width: 3px;
            border-radius: 999px;
            background: #60A5FA;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {
            color: #F8FAFC !important;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: rgba(148, 163, 184, 0.08);
        }

        .sidebar-brand {
            color: rgba(255,255,255,0.94);
            font-size: 1.18rem;
            font-weight: 800;
            letter-spacing: 0;
            margin: 0 0 0.65rem;
        }

        .sidebar-brand-block {
            margin: 0 0 1.35rem;
        }

        .sidebar-brand-title {
            color: #F8FAFC;
            font-size: 1.08rem;
            font-weight: 820;
            letter-spacing: 0.01em;
            line-height: 1.15;
        }

        .sidebar-brand-subtitle {
            margin-top: 0.28rem;
            color: #94A3B8;
            font-size: 0.72rem;
            font-weight: 650;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }

        .sidebar-section-label {
            margin: 1.05rem 0 0.42rem;
            color: #64748B;
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .sidebar-workspace-label {
            margin-top: 1.25rem;
        }

        .sidebar-data-card {
            padding: 0.55rem 0.65rem;
            border: 1px solid rgba(148, 163, 184, 0.12);
            border-radius: 10px;
            background: rgba(15, 23, 42, 0.48);
            line-height: 1.35;
        }

        .sidebar-data-card div {
            color: #CBD5E1;
            font-size: 0.78rem;
            font-weight: 680;
        }

        .sidebar-data-card span {
            display: block;
            color: #64748B;
            font-size: 0.72rem;
        }

        [data-testid="stSidebar"] div[style*="border-top"] {
            margin-top: 1.15rem !important;
            padding-top: 0.9rem !important;
            border-top-color: rgba(255, 255, 255, 0.08) !important;
        }

        [data-testid="stSidebar"] div[style*="border-top"] span {
            color: #94A3B8 !important;
            font-size: 0.76rem !important;
        }

        [data-testid="stSidebar"] div[style*="border-top"] span:first-child {
            color: #F8FAFC !important;
            font-weight: 760 !important;
        }

        [data-testid="stSidebar"] div[style*="border-top"] span:nth-child(2) {
            color: #475569 !important;
        }

        h1, h2, h3 {
            letter-spacing: 0;
            color: var(--zhx-text);
        }

        h1 {
            font-size: 1.8rem;
            line-height: 1.12;
        }

        h2 {
            font-size: 1.18rem;
        }

        h3 {
            font-size: 1rem;
        }

        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.78);
            border: 1px solid var(--zhx-line);
            border-radius: 0.6rem;
            padding: 0.85rem 0.9rem;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
        }

        div[data-testid="stMetricLabel"] p {
            color: var(--zhx-muted);
            font-size: 0.8rem;
        }

        div[data-testid="stMetricValue"] {
            color: var(--zhx-text);
            font-weight: 720;
        }

        .stButton > button {
            min-height: 2rem;
            border-radius: 7px;
            border: 1px solid rgba(15, 23, 42, 0.12);
            background: rgba(255, 255, 255, 0.92);
            color: #334155;
            box-shadow: none;
            font-size: 0.8rem;
            font-weight: 680;
        }

        .stButton > button:hover {
            border-color: rgba(15, 23, 42, 0.20);
            color: #0F172A;
            background: #ffffff;
        }

        .zhx-action-group,
        .buy-zone-row-actions,
        .portfolio-row-actions {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            width: max-content;
            padding: 0;
            border: 0;
            border-radius: 0;
            background: transparent;
            box-shadow: none;
            white-space: nowrap;
        }

        .zhx-action-link,
        .buy-zone-detail-link,
        .buy-zone-record-link,
        .dashboard-view-action,
        .drawer-signal-actions a,
        .drawer-menu-link,
        .portfolio-view-link,
        .trade-entry-delete-link,
        .trade-entry-delete-link:visited {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 38px;
            height: 26px;
            padding: 0 0.56rem;
            border-radius: 4px;
            border: 1px solid transparent;
            background: transparent;
            color: var(--zhx-action-text) !important;
            font-size: 12px;
            font-weight: 700;
            line-height: 1;
            text-decoration: none !important;
            white-space: nowrap;
            box-shadow: none;
        }

        .zhx-action-link:hover,
        .buy-zone-detail-link:hover,
        .buy-zone-record-link:hover,
        .dashboard-view-action:hover,
        .drawer-signal-actions a:hover,
        .drawer-menu-link:hover,
        .portfolio-view-link:hover,
        .trade-entry-delete-link:hover {
            color: var(--zhx-action-text-strong) !important;
            border-color: rgba(15, 23, 42, 0.08);
            background: #FFFFFF;
            text-decoration: none !important;
        }

        .portfolio-archive-link,
        .trade-entry-delete-link,
        .trade-entry-delete-link:visited {
            padding-left: 0.2rem;
            padding-right: 0.2rem;
            color: #64748B !important;
            font-weight: 650;
        }

        [class*="st-key-stock-detail-record-signal"] button,
        [class*="st-key-dashboard-detail-"] button,
        [class*="st-key-dashboard-position-"] button,
        [class*="st-key-dashboard-plan-"] button,
        [class*="st-key-dashboard-refresh-"] button,
        [class*="st-key-trade-error-select-"] button,
        [class*="st-key-trade-snapshot-delete-"] button,
        [class*="st-key-trade-error-edit-"] button,
        [class*="st-key-trade-error-delete-"] button {
            min-height: 26px !important;
            height: 26px !important;
            padding: 0 0.56rem !important;
            border-radius: 4px !important;
            border-color: transparent !important;
            background: transparent !important;
            color: var(--zhx-action-text) !important;
            box-shadow: none !important;
            font-size: 12px !important;
            font-weight: 700 !important;
        }

        [class*="st-key-stock-detail-record-signal"] button:hover,
        [class*="st-key-dashboard-detail-"] button:hover,
        [class*="st-key-dashboard-position-"] button:hover,
        [class*="st-key-dashboard-plan-"] button:hover,
        [class*="st-key-dashboard-refresh-"] button:hover,
        [class*="st-key-trade-error-select-"] button:hover,
        [class*="st-key-trade-snapshot-delete-"] button:hover,
        [class*="st-key-trade-error-edit-"] button:hover,
        [class*="st-key-trade-error-delete-"] button:hover {
            color: var(--zhx-action-text-strong) !important;
            border-color: rgba(15, 23, 42, 0.08) !important;
            background: #FFFFFF !important;
        }

        [class*="st-key-trade-snapshot-delete-"] button,
        [class*="st-key-trade-error-delete-"] button {
            padding-left: 0.2rem !important;
            padding-right: 0.2rem !important;
            color: #64748B !important;
            font-weight: 650 !important;
        }

        .zhx-badge,
        .decision-badge,
        .detail-pill,
        .review-badge,
        .trade-action-badge {
            display: inline-flex;
            align-items: center;
            max-width: 100%;
            min-height: 18px;
            height: auto;
            padding: 0.05rem 0.42rem;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 650;
            line-height: 1.35;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            box-sizing: border-box;
        }

        .buy-zone-table,
        .portfolio-table-wrap.terminal,
        .trade-journal-table-wrap,
        .decision-table {
            border-color: rgba(15, 23, 42, 0.08) !important;
            border-radius: 8px !important;
            background: #FFFFFF !important;
            box-shadow: none !important;
        }

        .portfolio-table.terminal th,
        .trade-journal-table th,
        .decision-grid-head,
        .buy-zone-grid-head {
            min-height: 28px;
            background: var(--zhx-table-head) !important;
            color: #64748B !important;
            font-size: 11px !important;
            font-weight: 650 !important;
            letter-spacing: 0 !important;
        }

        .portfolio-table.terminal td,
        .trade-journal-table td,
        .decision-row,
        .buy-zone-row {
            border-bottom-color: var(--zhx-subtle-line) !important;
        }

        .portfolio-table.terminal tr:hover td,
        .trade-journal-table tr:hover td,
        .decision-row:hover,
        .buy-zone-row:hover {
            background: var(--zhx-table-hover) !important;
        }

        .stAlert {
            border-radius: 0.65rem;
            border: 1px solid var(--zhx-line);
        }

        [data-testid="stDataFrame"] {
            border-radius: 0.6rem;
            overflow: hidden;
            border: 1px solid var(--zhx-line);
        }

        .zhx-page-head {
            display: flex;
            justify-content: space-between;
            gap: 1.25rem;
            align-items: flex-end;
            padding: 0.95rem 0 1.15rem;
            border-bottom: 1px solid var(--zhx-line);
            margin-bottom: 1rem;
        }

        .zhx-kicker {
            color: var(--zhx-blue);
            font-size: 0.76rem;
            font-weight: 760;
            text-transform: uppercase;
        }

        .zhx-title {
            margin-top: 0.15rem;
            font-size: 2.05rem;
            line-height: 1.1;
            font-weight: 780;
            color: var(--zhx-text);
        }

        .zhx-subtitle {
            margin-top: 0.45rem;
            color: var(--zhx-muted);
            max-width: 54rem;
            line-height: 1.55;
            font-size: 0.94rem;
        }

        .zhx-head-meta {
            color: var(--zhx-muted);
            font-size: 0.82rem;
            text-align: right;
            white-space: nowrap;
        }

        .zhx-section-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin: 1.25rem 0 0.65rem;
        }

        .zhx-section-title strong {
            font-size: 1.02rem;
            color: var(--zhx-text);
        }

        .zhx-section-title span {
            font-size: 0.82rem;
            color: var(--zhx-muted);
        }

        [data-testid="stSidebar"],
        [data-testid="stSidebarContent"],
        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapseButton"] {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
        }

        [data-testid="stMainBlockContainer"] {
            margin-left: 272px !important;
            margin-right: 2rem !important;
            max-width: 1440px;
        }

        .zhx-fixed-sidebar {
            position: fixed;
            z-index: 99999;
            top: 0;
            left: 0;
            bottom: 0;
            width: 240px;
            padding: 24px;
            box-sizing: border-box;
            background: #0B1220;
            border-right: 1px solid rgba(255, 255, 255, 0.06);
            color: #F8FAFC;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            box-shadow: 18px 0 36px rgba(15, 23, 42, 0.10);
        }

        .zhx-side-brand {
            margin-bottom: 1.35rem;
        }

        .zhx-side-title {
            color: #F8FAFC;
            font-size: 1.08rem;
            font-weight: 820;
            letter-spacing: 0.01em;
            line-height: 1.15;
        }

        .zhx-side-subtitle {
            margin-top: 0.28rem;
            color: #94A3B8;
            font-size: 0.72rem;
            font-weight: 650;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }

        .zhx-side-section {
            margin: 1.05rem 0 0.42rem;
            color: #64748B;
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .zhx-side-section.workspace {
            margin-top: 1.25rem;
        }

        .zhx-side-data-card {
            padding: 0.58rem 0.65rem;
            border: 1px solid rgba(148, 163, 184, 0.12);
            border-radius: 10px;
            background: rgba(15, 23, 42, 0.48);
            line-height: 1.38;
        }

        .zhx-side-data-card strong {
            display: block;
            color: #CBD5E1;
            font-size: 0.78rem;
            font-weight: 720;
        }

        .zhx-side-data-card span,
        .zhx-side-queue {
            display: block;
            color: #64748B;
            font-size: 0.72rem;
        }

        .zhx-side-queue {
            margin-top: 0.45rem;
        }

        .zhx-side-nav {
            display: flex;
            flex-direction: column;
            gap: 0.22rem;
        }

        .zhx-side-nav-item {
            position: relative;
            min-height: 40px;
            border-radius: 10px;
            padding: 0.48rem 0.7rem 0.48rem 0.78rem;
            border: 1px solid transparent;
            color: #94A3B8 !important;
            text-decoration: none !important;
            display: flex;
            align-items: center;
            gap: 0.62rem;
            font-size: 0.88rem;
            font-weight: 650;
            transition: background 140ms ease, border-color 140ms ease, color 140ms ease;
        }

        .zhx-side-nav-item:hover {
            background: rgba(148, 163, 184, 0.08);
            color: #E2E8F0 !important;
        }

        .zhx-side-nav-item.active {
            background: rgba(59, 130, 246, 0.12);
            border-color: rgba(96, 165, 250, 0.18);
            color: #F8FAFC !important;
        }

        .zhx-side-nav-item.active::before {
            content: "";
            position: absolute;
            left: 0;
            top: 9px;
            bottom: 9px;
            width: 3px;
            border-radius: 999px;
            background: #60A5FA;
        }

        .zhx-side-nav-icon {
            width: 1.05rem;
            color: inherit;
            text-align: center;
            font-size: 0.95rem;
        }

        .zhx-side-footer {
            margin-top: auto;
            padding-top: 0.9rem;
            border-top: 1px solid rgba(255, 255, 255, 0.10);
            color: #94A3B8;
            font-size: 0.76rem;
            font-weight: 650;
            display: flex;
            gap: 0.4rem;
            align-items: center;
        }

        .zhx-side-mark {
            color: #F8FAFC;
            font-weight: 780;
        }

        .zhx-side-dot {
            color: rgba(255, 255, 255, 0.28);
        }

        @media (max-width: 980px) {
            .zhx-fixed-sidebar {
                position: relative;
                width: auto;
                min-height: auto;
                bottom: auto;
                margin: 0 0 1rem;
                padding: 18px;
            }
            [data-testid="stMainBlockContainer"] {
                margin-left: 0 !important;
                margin-right: 0 !important;
                padding: 1rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_restore_button() -> None:
    components.html(
        """
        <script>
        (() => {
          const doc = window.parent.document;
          const existing = doc.getElementById("zhx-sidebar-restore");
          if (existing) existing.remove();

          const button = doc.createElement("button");
          button.id = "zhx-sidebar-restore";
          button.type = "button";
          button.textContent = "☰ 导航";
          button.setAttribute("aria-label", "打开左侧导航");
          button.style.cssText = [
            "position:fixed",
            "top:12px",
            "left:12px",
            "z-index:2147483647",
            "height:40px",
            "padding:0 12px",
            "border-radius:10px",
            "border:1px solid rgba(148,163,184,.28)",
            "background:#0B1220",
            "color:#F8FAFC",
            "font:700 13px system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
            "box-shadow:0 14px 30px rgba(15,23,42,.18)",
            "cursor:pointer",
            "display:none",
            "align-items:center",
            "gap:6px"
          ].join(";");

          const openSidebar = () => {
            const selectors = [
              '[data-testid="collapsedControl"] button',
              'button[aria-label*="Open sidebar"]',
              'button[title*="Open sidebar"]',
              'button[aria-label*="open sidebar"]',
              'button[title*="open sidebar"]'
            ];
            for (const selector of selectors) {
              const target = doc.querySelector(selector);
              if (target) {
                target.click();
                return;
              }
            }
          };

          const isSidebarVisible = () => {
            const sidebar = doc.querySelector('[data-testid="stSidebar"]');
            if (!sidebar) return false;
            const rect = sidebar.getBoundingClientRect();
            const style = doc.defaultView.getComputedStyle(sidebar);
            return rect.width > 80 && style.visibility !== "hidden" && style.display !== "none";
          };

          const refresh = () => {
            button.style.display = isSidebarVisible() ? "none" : "inline-flex";
          };

          button.addEventListener("click", () => {
            openSidebar();
            window.setTimeout(refresh, 120);
            window.setTimeout(refresh, 420);
          });

          doc.body.appendChild(button);
          refresh();
          window.setTimeout(refresh, 300);
          window.setTimeout(refresh, 1000);

          const observer = new MutationObserver(refresh);
          observer.observe(doc.body, { attributes: true, childList: true, subtree: true });
          window.addEventListener("beforeunload", () => {
            observer.disconnect();
            button.remove();
          });
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def render_page_header(title: str, subtitle: str, meta: str = "") -> None:
    st.markdown(
        f"""
        <div class="zhx-page-head">
            <div>
                <div class="zhx-kicker">ZHX Research</div>
                <div class="zhx-title">{escape(title)}</div>
                <div class="zhx-subtitle">{escape(subtitle)}</div>
            </div>
            <div class="zhx-head-meta">{escape(meta)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_title(title: str, meta: str = "") -> None:
    st.markdown(
        f"""
        <div class="zhx-section-title">
            <strong>{escape(title)}</strong>
            <span>{escape(meta)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
