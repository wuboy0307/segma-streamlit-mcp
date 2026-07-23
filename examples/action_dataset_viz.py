"""
Action Dataset 視覺化範例 / Action Dataset visualization example.

透過 Segma REST API(`GET /api/v1/action_datasets/{id}/data`)把一個 Action Dataset
的內容拉下來,用 Streamlit 畫成表格 + 圖表。這是「建好 CDP → 行動資料 → 拉出來做
視覺化」流程的最後一段:資料留在 Segma,前端只透過 API 取用。

跑法:
    export SEGMA_API_BASE=https://localhost:1443   # 或部署時的 access url
    export SEGMA_TOKEN=<bearer token>              # 部署在 Segma 內時由 ?token= 帶入
    export SEGMA_AD_ID=120                          # 要視覺化的 Action Dataset id
    streamlit run action_dataset_viz.py
"""

import os

import httpx
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Action Dataset 視覺化", page_icon="📊", layout="wide")

API_BASE = os.environ.get("SEGMA_API_BASE", "https://localhost:1443").rstrip("/")
TOKEN = os.environ.get("SEGMA_TOKEN", "") or st.query_params.get("token", "")
VERIFY = os.environ.get("SEGMA_VERIFY_TLS", "").lower() in ("1", "true", "yes")

st.title("📊 Action Dataset 視覺化")
st.caption(
    "資料留在 Segma;這支前端只透過 REST API "
    "`GET /api/v1/action_datasets/{id}/data` 把行動資料的內容拉出來畫圖。"
)

with st.sidebar:
    st.header("來源")
    ad_id = st.text_input("Action Dataset id", value=os.environ.get("SEGMA_AD_ID", "")).strip()
    if not TOKEN:
        TOKEN = st.text_input("Bearer token", type="password").strip()
    limit = st.number_input("最多幾筆", min_value=10, max_value=100000, value=1000, step=100)

if not (ad_id and TOKEN):
    st.info("請在左側填 Action Dataset id 與 token。", icon="🗝️")
    st.stop()


@st.cache_data(show_spinner="拉取行動資料中…")
def fetch_ad(base, token, ad_id, limit, verify):
    r = httpx.get(f"{base}/api/v1/action_datasets/{ad_id}/data",
                  params={"limit": limit},
                  headers={"Authorization": f"Bearer {token}"},
                  verify=verify, timeout=60)
    r.raise_for_status()
    return r.json()


try:
    payload = fetch_ad(API_BASE, TOKEN, ad_id, int(limit), VERIFY)
except Exception as exc:  # noqa: BLE001
    st.error(f"拉取失敗:{type(exc).__name__}: {exc}")
    st.stop()

cols = payload.get("columns", [])
df = pd.DataFrame(payload.get("data", []), columns=cols)
st.success(f"共 {payload.get('row_count', len(df))} 筆 · {len(cols)} 欄")

# numeric columns → coerce for charting
num_cols = []
for c in cols:
    coerced = pd.to_numeric(df[c], errors="coerce")
    if coerced.notna().any():
        df[c] = coerced
        num_cols.append(c)
text_cols = [c for c in cols if c not in num_cols]

c1, c2, c3 = st.columns(3)
if num_cols:
    total = df[num_cols[0]].sum(skipna=True)
    c1.metric(f"Σ {num_cols[0]}", f"{total:,.0f}")
    c2.metric(f"平均 {num_cols[0]}", f"{df[num_cols[0]].mean(skipna=True):,.1f}")
c3.metric("列數", f"{len(df):,}")

st.subheader("資料表")
st.dataframe(df, use_container_width=True, height=280)

if num_cols and text_cols:
    label = text_cols[0]
    metric = st.selectbox("挑一個數值欄畫長條圖", num_cols, index=0)
    top = (df[[label, metric]].dropna()
           .sort_values(metric, ascending=False).head(20).set_index(label))
    st.subheader(f"Top 20 · {metric}(依 {label})")
    st.bar_chart(top)

if len(num_cols) >= 2:
    st.subheader(f"分布 · {num_cols[1]}")
    st.bar_chart(df[num_cols[1]].dropna().value_counts().sort_index())
