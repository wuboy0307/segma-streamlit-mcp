"""
Segma MCP Chat — 用對話操作 Segma / build Segma by chatting.

一個 Streamlit 範例:把 segma-mcp 的工具接給一個 LLM agent,使用者用自然語言
就能請 agent 建立 / 查詢 Segma 的各種 resource(DataSource / Dim / Fact /
Metric / Trait / Segment / ActionDataset / Destination / Sync …)。

UI 有 繁體中文 / English 兩種語言(左側切換);切語言時,介面文字、範本、以及給
agent 的 SYSTEM_PROMPT(含回覆語言與商業用語)一起切。

架構
----
    使用者 ──▶ Streamlit ──▶ Pydantic AI Agent ──(MCP)──▶ segma-mcp ──▶ backend
                                   │
                          OpenAI-compatible LLM

連線設定(都可用 env 或畫面輸入)
------------------------------------
- Segma MCP URL:env `SEGMA_MCP_URL`,或由 `SEGMA_ACCESS_URL` 推導 `<url>/mcp`。
- Bearer token:部署在 Segma 內時由 `?token=` 帶入(見 segma_bridge);獨立跑時貼上。
- LLM:env `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_MAX_TOKENS`(OpenAI
  相容),缺的用左側「LLM 設定」面板補;面板永遠可見,env 值當預設。
"""

import asyncio
import json
import os

import streamlit as st
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied

from agent_runtime import build_agent as _build_agent
from agent_runtime import is_destructive, stream_turn

# segma_bridge 只有在 Segma 的 Streamlit 容器裡才 import 得到(保鮮 token + 心跳)。
try:
    import segma_bridge  # type: ignore

    _HAS_BRIDGE = True
except ModuleNotFoundError:
    segma_bridge = None  # type: ignore
    _HAS_BRIDGE = False

DEFAULT_LLM_MODEL = "gpt-4o"
DEFAULT_MAX_TOKENS = 2048
LANG_OPTIONS = {"繁體中文": "zh", "English": "en"}


# ---------------------------------------------------------------------------
# UI 字串(依語言)
# ---------------------------------------------------------------------------

UI = {
    "zh": {
        "intro": (
            "用對話建你的顧客資料平台。接上一個資料來源,助手會讀它的資料結構、"
            "**主動提出**一套有意義的設計——要分析誰(**分析主體**)、追蹤哪些"
            "**事件**、算哪些**指標**與**標籤**——你大多時候只要**確認**就好,"
            "不必逐欄設定。不知道怎麼開始?左側有**常用範本**可以挑。"
        ),
        "lang": "🌐 語言 / Language",
        "conn": "連線",
        "mcp_url": "Segma MCP URL",
        "mcp_url_help": "segma-mcp 的 /mcp 端點。dev 本機通常是 https://localhost:1443/mcp。",
        "token": "Bearer token (JWT)",
        "token_help": "POST /api/auth/login 拿到的 token。部署在 Segma 內時由 ?token= 自動帶入。",
        "llm_settings": "⚙️ LLM 設定",
        "llm_base_url": "LLM 基礎 URL",
        "llm_base_url_ph": "https://api.openai.com/v1(留空用預設)",
        "llm_model": "LLM 模型名稱",
        "llm_api_key": "LLM API 金鑰",
        "llm_api_key_env_ph": "已由環境變數提供,可留空",
        "llm_max_tokens": "LLM 最大輸出 tokens",
        "clear_chat": "🗑️ 清空對話",
        "templates": "📋 常用範本",
        "tpl_help": (
            "點下面任一範本,內容會帶到輸入框。把 `< >` 裡的說明換成你自己的資料"
            "(表名、欄位名、門檻數字…);不確定的地方可以留著、或直接寫成問句,助手會讀"
            "你的資料結構幫你補。改好按 **送出**。"
        ),
        "draft_hint": "👇 從範本帶進來的內容,把 `< >` 換成你的資料再送出:",
        "send": "📨 送出",
        "clear": "清除",
        "chat_ph": "直接打字,或從左側『常用範本』挑一個改…",
        "spinner": "🤖 助手運作中(可能會呼叫多個工具)…",
        "run_failed": "執行失敗",
        "tool_calls": "🔧 工具呼叫",
        "missing": "請在左側補上",
        "confirm_toggle": "🛡️ 破壞性動作先問我",
        "confirm_toggle_help": "刪除、觸發同步 / 觸發行動資料、重建容器等**會真的動到資料**的動作,執行前先停下來讓你按核准。關掉的話助手可直接執行(仍會照系統提示先口頭確認)。",
        "approve_intro": "助手想執行以下**會動到資料**的動作,需要你核准才會跑:",
        "approve_this": "核准",
        "approve_run": "✅ 執行勾選的",
        "approve_deny_all": "✋ 全部拒絕",
        "approve_denied_reason": "使用者在確認畫面選擇『不要執行』這個動作(這不是權限問題,也不是錯誤)。請勿重試,直接簡短告訴使用者這個動作已取消。",
        "approve_waiting": "⏳ 等你在下方核准…",
        "approve_done_note": "已依你的選擇處理完上述待確認的動作。",
    },
    "en": {
        "intro": (
            "Build your customer data platform by chatting. Connect a data source "
            "and the assistant reads its structure and **proposes** a meaningful "
            "design — who to analyze (**Entities**), what **Events** to track, which "
            "**Metrics** and **Traits** to compute — you mostly just **confirm**, no "
            "field-by-field setup. Not sure where to start? Pick a **template** on the left."
        ),
        "lang": "🌐 Language / 語言",
        "conn": "Connection",
        "mcp_url": "Segma MCP URL",
        "mcp_url_help": "segma-mcp's /mcp endpoint. Local dev is usually https://localhost:1443/mcp.",
        "token": "Bearer token (JWT)",
        "token_help": "The token from POST /api/auth/login. Passed via ?token= when deployed inside Segma.",
        "llm_settings": "⚙️ LLM Settings",
        "llm_base_url": "LLM Base URL",
        "llm_base_url_ph": "https://api.openai.com/v1 (blank = default)",
        "llm_model": "LLM Model Name",
        "llm_api_key": "LLM API Key",
        "llm_api_key_env_ph": "provided via env, leave blank",
        "llm_max_tokens": "Max Output Tokens",
        "clear_chat": "🗑️ Clear chat",
        "templates": "📋 Templates",
        "tpl_help": (
            "Click any template to load it into the input box. Replace the `< >` "
            "placeholders with your own data (table names, column names, thresholds…); "
            "leave anything you're unsure about, or phrase it as a question — the "
            "assistant will read your schema and fill it in. Then press **Send**."
        ),
        "draft_hint": "👇 Loaded from a template — replace the `< >` parts with your data, then send:",
        "send": "📨 Send",
        "clear": "Clear",
        "chat_ph": "Type here, or pick a template on the left…",
        "spinner": "🤖 Working (may call several tools)…",
        "run_failed": "Run failed",
        "tool_calls": "🔧 Tool calls",
        "missing": "Please fill in on the left",
        "confirm_toggle": "🛡️ Ask before destructive actions",
        "confirm_toggle_help": "Delete, trigger a sync / action dataset, recreate a container — anything that **actually changes data** — pauses for your approval before it runs. Turn off and the assistant runs them directly (it still confirms verbally per the system prompt).",
        "approve_intro": "The assistant wants to run these **data-changing** actions — approve to let them run:",
        "approve_this": "approve",
        "approve_run": "✅ Run checked",
        "approve_deny_all": "✋ Deny all",
        "approve_denied_reason": "The user chose 'do NOT run' this action in the confirmation dialog (this is not a permission problem and not an error). Do not retry; just briefly tell the user the action was cancelled.",
        "approve_waiting": "⏳ Waiting for your approval below…",
        "approve_done_note": "Handled the pending actions per your choices.",
    },
}


# ---------------------------------------------------------------------------
# 常用 prompt 範本(依語言、依 resource 分類)。點選帶進輸入框,改好再送。
# 用語照 frontend i18n:分析主體/Entity、事件/Event、指標/Metric、標籤/Trait、
# 分群/Segment、行動資料/Action Dataset。`< >` 是要使用者替換的佔位符。
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES = {
    "zh": {
        "🔌 連接資料": [
            ("PostgreSQL", "接上我的 PostgreSQL 資料倉。類型是 postgres,連線:host <主機位址>, port <埠,預設 5432>, 帳號 <帳號>, 密碼 <密碼>, database <資料庫名>, schema <schema 名>,資料來源名 <名稱,例如 我的倉>。讀完整份資料結構後,主動提議可以建立哪些『分析主體』(要分析的對象,例如客戶)跟『事件』(發生的事,例如交易),先講給我聽,我確認你再建。"),
            ("MySQL", "接上我的 MySQL 資料倉。類型是 mysql,連線:host <主機>, port <埠,預設 3306>, 帳號 <帳號>, 密碼 <密碼>, database <資料庫名>。讀完結構後主動提議要建哪些分析主體 / 事件,先講給我聽。"),
            ("Greenplum", "接上我的 Greenplum 資料倉。類型是 greenplum,連線:host <主機>, port <埠,預設 5432>, 帳號 <帳號>, 密碼 <密碼>, database <資料庫名>, schema <schema 名>。讀完結構後主動提議設計。"),
            ("SQL Server", "接上我的 SQL Server 資料倉。類型是 sqlserver,連線:host <主機>, port <埠,預設 1433>, 帳號 <帳號>, 密碼 <密碼>, database <資料庫名>。讀完結構後主動提議設計。"),
            ("Oracle", "接上我的 Oracle 資料倉。類型是 oracle,連線:host <主機>, port <埠,預設 1521>, 帳號 <帳號>, 密碼 <密碼>, database <SID 或 service name>。讀完結構後主動提議設計。"),
            ("Sybase", "接上我的 Sybase 資料倉。類型是 sybase,連線:host <主機>, port <埠,預設 5000>, 帳號 <帳號>, 密碼 <密碼>, database <資料庫名>。讀完結構後主動提議設計。"),
            ("BigQuery", "接上我的 BigQuery。類型是 bigquery,用 service account 金鑰連線:project_id <GCP 專案 id>, dataset <資料集>, keyfile <貼上 service account JSON>。(BigQuery 不用 host / port / 密碼。)讀完結構後主動提議設計。"),
            ("落地成表(materialization)", "接上我的 <postgres / mysql / …> 資料倉(連線資訊我會給),而且把 materialization 打開——之後建的分群 / 行動資料可以選擇落地成實體表,查詢更快。連好後主動提議設計。"),
            ("不確定連線資訊 / 類型", "我要接一個資料倉,但不太確定要填什麼。我的倉庫類型是 <postgres / mysql / greenplum / sqlserver / oracle / bigquery / sybase 其中之一>。請先告訴我這個類型需要哪些連線資訊,我再一項一項提供給你。"),
            ("只連線、先看有哪些資料", "接上我的資料倉(類型 <你的倉庫類型>,連線資訊我會給),讀完結構後,只要先告訴我裡面有哪些表、各表的欄位大概是什麼、你看得出幾套資料,先不要建任何東西。"),
        ],
        "👤 分析主體(要分析的對象)": [
            ("客戶(單一主鍵)", "把 <客戶主檔,例如 dim_customer> 建成一個叫『客戶』的分析主體,用 <主鍵欄位,例如 customer_id> 當識別。"),
            ("客戶(複合主鍵)", "把 <客戶表> 建成分析主體『客戶』,用 <公司代號> + <客戶代號> 兩個欄位當複合主鍵(每一筆用這兩欄一起識別)。"),
            ("會員", "把 <會員表> 建成『會員』分析主體,用 <member_id> 當識別。"),
            ("商品", "把 <商品主檔> 建成『商品』分析主體,用 <product_id> 當識別。"),
            ("門市 / 分店", "把 <門市表> 建成『門市』分析主體,用 <store_id> 當識別。"),
            ("持卡人", "把 <持卡人表> 建成『持卡人』分析主體,用 <customer_id> 當識別。"),
            ("帳戶", "把 <帳戶表> 建成『帳戶』分析主體,用 <account_id> 當識別。"),
            ("供應商", "把 <供應商表> 建成『供應商』分析主體,用 <supplier_id> 當識別。"),
            ("業務員 / 員工", "把 <員工表> 建成『業務員』分析主體,用 <employee_id> 當識別。"),
            ("訂閱者", "把 <訂閱名單> 建成『訂閱者』分析主體,用 <subscriber_id> 當識別。"),
            ("裝置", "把 <裝置表> 建成『裝置』分析主體,用 <device_id> 當識別。"),
        ],
        "📅 事件(發生的事)": [
            ("交易(同名欄位對應)", "把 <交易紀錄表> 建成叫『交易』的事件,串到『客戶』分析主體上——兩邊都有 <customer_id> 就直接對應。"),
            ("交易(不同名欄位對應)", "把 <交易表> 建成『交易』事件,串到『客戶』:客戶端 <customer_id> 對交易端 <buyer_id>(名稱不同但代表同一個客戶)。"),
            ("一個事件 join 多個分析主體(星狀)", "把 <銷售表 FactInternetSales> 建成『銷售』事件,同時串到多個分析主體:<CustomerKey→客戶>、<ProductKey→商品>、<PromotionKey→促銷>、<CurrencyKey→幣別>(每組用兩邊同名的 *Key 對應)。"),
            ("用公式 join(欄位需轉換)", "把 <交易表> 建成『交易』事件串到『客戶』,但兩邊識別碼格式不同,需要用公式對應——例如客戶端是純數字、交易端前面多了前綴,請用公式把它們轉成一致再 join。"),
            ("訂單", "把 <訂單表> 建成『訂單』事件,串到『客戶』(<customer_id> 對 <order_customer_id>)。"),
            ("退貨", "把 <退貨表> 建成『退貨』事件,串到『客戶』(<customer_id> 對 <return_customer_id>)。"),
            ("網站點擊", "把 <點擊紀錄表> 建成『網站點擊』事件,串到『客戶』(<customer_id> 對 <user_id>)。"),
            ("加入購物車", "把 <購物車事件表> 建成『加入購物車』事件,串到『客戶』(<customer_id> 對 <cart_user_id>)。"),
            ("客服來電", "把 <客服紀錄表> 建成『客服來電』事件,串到『客戶』(<customer_id> 對 <caller_id>)。"),
            ("電子報開信", "把 <開信紀錄表> 建成『電子報開信』事件,串到『訂閱者』(<subscriber_id> 對 <recipient_id>)。"),
            ("活動參與", "把 <活動報名表> 建成『活動參與』事件,串到『會員』(<member_id> 對 <participant_id>)。"),
        ],
        "📊 指標(可計算的數字)": [
            ("總消費金額 = SUM", "幫『客戶』算指標『總消費金額』= 把『交易』的 <金額欄位,例如 amount> 加總(SUM)。"),
            ("平均客單價 = AVG", "幫『客戶』算指標『平均客單價』= 把 <金額欄位> 取平均(AVG)。"),
            ("消費次數 = COUNT", "幫『客戶』算指標『消費次數』= 數『交易』事件筆數(COUNT)。"),
            ("最高單筆 = MAX", "幫『客戶』算指標『最高單筆消費』= 取 <金額欄位> 最大值(MAX)。"),
            ("最低單筆 = MIN", "幫『客戶』算指標『最低單筆消費』= 取 <金額欄位> 最小值(MIN)。"),
            ("非零平均 = AVG_NONZERO", "幫『客戶』算指標『非零平均消費』= 把 <金額欄位> 取非零平均(AVG_NONZERO,忽略 0)。"),
            ("不重複商品數 = COUNT_DISTINCT", "幫『客戶』算指標『購買商品種類數』= 數 <商品欄位,例如 product_id> 的不重複個數(COUNT_DISTINCT)。"),
            ("不重複金額加總 = SUM_DISTINCT", "幫『客戶』算指標『不重複金額加總』= 把 <金額欄位> 去重後加總(SUM_DISTINCT)。"),
            ("不重複平均 = AVG_DISTINCT", "幫『客戶』算指標『不重複金額平均』= 把 <金額欄位> 去重後平均(AVG_DISTINCT)。"),
            ("毛利 = 公式", "幫『客戶』算公式指標『毛利』= <總銷售額欄位,例如 SalesAmount> 減 <總成本欄位,例如 TotalProductCost>(兩邊各 SUM)。"),
            ("毛利率 = 公式", "幫『客戶』算公式指標『毛利率』=(<總銷售額> 減 <總成本>)除以 <總銷售額>。"),
        ],
        "🏷️ 標籤(對象的屬性 / 計算結果)": [
            ("〔聚合〕總消費金額 = SUM", "幫『客戶』建聚合(aggregation)標籤『總消費金額』= 把『交易』的 <金額欄位> 加總(SUM)。"),
            ("〔聚合〕消費次數 = COUNT", "幫『客戶』建聚合標籤『消費次數』= 數『交易』筆數(COUNT)。"),
            ("〔聚合〕最高單筆 = MAX", "幫『客戶』建聚合標籤『最高單筆消費』= 取『交易』的 <金額欄位> 最大值(MAX)。"),
            ("〔聚合〕最低單筆 = MIN", "幫『客戶』建聚合標籤『最低單筆消費』= 取 <金額欄位> 最小值(MIN)。"),
            ("〔聚合〕平均客單價 = AVG", "幫『客戶』建聚合標籤『平均客單價』= 取 <金額欄位> 平均(AVG)。"),
            ("〔計算〕最近一次消費金額(取最後一次)", "幫『客戶』建計算(compute)標籤『最近一次消費金額』= 取『交易』裡最後一次交易的 <金額欄位> 值(依 <交易時間欄位> 排序、取最後一筆那次)。算法照這個倉的方言挑對的(list_compute_functions)。"),
            ("〔計算〕首次消費金額(取第一次)", "幫『客戶』建計算標籤『首次消費金額』= 取『第一次交易』的 <金額欄位> 值(依 <交易時間> 排序,取第一筆;one_of_first_event)。"),
            ("〔計算〕距上次消費天數(近因)", "幫『客戶』建計算標籤『距上次消費天數』= 距今最後一次『交易』的天數(days_since_last_event)。"),
            ("〔計算〕客戶年資天數(距首次)", "幫『客戶』建計算標籤『客戶年資天數』= 距今第一次『交易』的天數(days_since_first_event)。"),
            ("〔計算〕消費金額中位數 = median", "幫『客戶』建計算標籤『消費金額中位數』= 取『交易』的 <金額欄位> 中位數(median)。"),
            ("〔計算〕消費金額上四分位", "幫『客戶』建計算標籤『消費金額上四分位』= 取 <金額欄位> 的上四分位(upper_quartile)。"),
            ("〔計算〕最常買的商品類別", "幫『客戶』建計算標籤『最常買的商品類別』= 取『交易』裡出現最多次的 <商品類別欄位>(one_of_most_frequent_event)。"),
            ("〔計算〕最高消費那次的商品", "幫『客戶』建計算標籤『最高消費那次買的商品』= 取金額最高那筆『交易』的 <商品欄位>(one_of_highest_event,依 <金額欄位> 排序)。"),
            ("〔計算〕買過的不重複商品清單", "幫『客戶』建計算標籤『買過的商品清單』= 列出『交易』裡出現過的不重複 <商品欄位>(unique_event_list)。"),
            ("〔衍生〕客單價 = 公式引用標籤", "幫『客戶』建衍生(derive)標籤『客單價』=『總消費金額』除以『消費次數』(引用我已建好的標籤,用它們現在的名稱)。"),
            ("〔衍生〕折扣率 = 公式引用標籤", "幫『客戶』建衍生標籤『折扣率』=『總折扣金額』除以『總消費金額』。"),
            ("〔SQL〕用自訂 SQL 算標籤", "幫『客戶』建 sql 型標籤『<名稱>』,用這段我自己寫的 SQL:<你的 SQL>(要點:SELECT 客戶主鍵 + 一個計算欄位,FROM 某表;大小寫混合的表 / 欄位名記得加雙引號)。"),
        ],
        "🎯 分群(篩出一群對象)": [
            ("高價值(指標超過門檻)", "建分群『高價值客戶』:『總消費金額』指標 超過 <10000>。"),
            ("低頻(指標低於門檻)", "建分群『低頻客戶』:『消費次數』少於 <3>。"),
            ("金額區間(between)", "建分群『中間消費客戶』:『總消費金額』介於 <5000> 到 <20000>。"),
            ("排除區間(not-between)", "建分群『非中間消費』:『總消費金額』落在 <5000>~<20000> 區間『之外』。"),
            ("等於某值(is)", "建分群『特定等級客戶』:<會員等級標籤> 等於 <金卡>。"),
            ("屬於清單(in)", "建分群『白金金卡客戶』:<卡別標籤> 屬於 [<白金>, <金卡>]。"),
            ("不屬於清單(not-in)", "建分群『正式客戶』:<名稱標籤> 不屬於 [<測試>, <內部>]。"),
            ("沒有值(null)", "建分群『沒有 email 的客戶』:<email 標籤> 沒有值。"),
            ("有值(not-null)", "建分群『可簡訊觸及』:<手機標籤> 有值。"),
            ("近期次數(eventCount + 相對時間)", "建分群『近期常客』:過去 <30 天> 內『交易』事件 超過 <5 次>。"),
            ("事件聚合(eventAggr)", "建分群『高平均消費』:『交易』的 <金額欄位> 平均 超過 <2000>。"),
            ("指標門檻(metric)", "建分群『高終身價值』:『終身價值』指標 超過 <50000>。"),
            ("引用另一個分群(segment)", "建分群『VIP 的子集』:屬於『VIP』這個分群 的成員,再加上 <某條件>。"),
            ("漏斗 / 事件序列(eventAnalytics)", "建分群『完成購買流程』:依序發生 <瀏覽商品 → 加入購物車 → 完成交易> 的客戶。"),
            ("多條件 AND + 排除 group", "建分群『活躍高價值非流失』:同時符合『總消費金額』超過 <10000> 且『距上次消費天數』在 <30 天內>,但排除『已流失』分群。"),
            ("多條件 OR(任一即可)", "建分群『可再行銷』:符合『有 email』或『有手機』其中任一 的客戶。"),
        ],
        "📦 行動資料(整理輸出欄位)": [
            ("客戶輪廓(trait)", "建 trait 型行動資料『客戶輪廓』,輸出 <姓名、總消費金額、所屬分群> 標籤欄位。"),
            ("聯絡清單(trait)", "建 trait 型行動資料『聯絡清單』,輸出 <姓名、email、手機> 標籤,供匯出行銷工具。"),
            ("RFM 名單(trait)", "建 trait 型行動資料『RFM 名單』,輸出 <距上次消費天數(近因)、消費次數(頻率)、總消費金額(金額)>。"),
            ("高價值名單(trait)", "建 trait 型行動資料『高價值名單』,輸出 <姓名、總消費金額、最近一次消費金額>。"),
            ("只針對某分群(trait + 條件)", "建 trait 型行動資料『VIP 聯絡清單』,只輸出屬於『VIP』分群的客戶的 <姓名、email、手機>。"),
            ("客戶指標彙總(metric)", "建 metric 型行動資料『客戶指標彙總』,以『客戶』當分析維度,彙總 <總消費金額、消費次數>。"),
            ("每月銷售(metric + 時間分桶)", "建 metric 型行動資料『每月銷售』,把『總消費金額』依 <月> 分桶,以『客戶』分組。"),
            ("各分群人數(metric)", "建 metric 型行動資料『各分群人數』,彙總各分群客戶數。"),
            ("自訂 SQL 匯出(sql)", "建 sql 型行動資料『<名稱>』,用一段我自己寫的 SQL 當輸出(FROM 用真實存在的表 / 欄位,大小寫混合的名稱加雙引號)。"),
            ("商品銷售明細(sql)", "建 sql 型行動資料『商品銷售明細』,用 SQL 輸出 <商品、數量、金額> 明細。"),
            ("落地成實體表(materialization=table)", "建 trait 型行動資料『客戶輪廓(每日更新)』,把它 materialization 設成 table(落地成實體表、可排程更新),輸出 <姓名、總消費、分群>。"),
        ],
        "📮 同步目的地(要匯出去哪)": [
            ("本地檔案匯出(CSV)", "建一個匯出目的地『每日名單匯出』,類型是本地檔案匯出(export_file),輸出成 CSV 檔。"),
            ("FTP / SFTP", "建一個匯出目的地『客戶名單 FTP』,類型是 <ftp 或 sftp>,連線:host <主機>, 帳號 <帳號>, 密碼 <密碼>, 遠端目錄 <目錄>,輸出成 CSV。"),
            ("Google Sheets", "建一個匯出目的地『行銷試算表』,類型是 google_sheets,連到試算表 <spreadsheet_id>、工作表 <worksheet_name>(service account 金鑰我會給)。"),
            ("Mailchimp", "建一個匯出目的地『電子報名單』,類型是 mailchimp,連到名單 <list_id>(api_key 與 server_prefix 我會給)。"),
            ("Facebook 自訂受眾", "建一個匯出目的地『FB 再行銷受眾』,類型是 facebook_custom_audience,推到廣告帳號 <ad_account_id> 的受眾 <audience_id>(access_token 我會給)。"),
            ("Google Ads 名單", "建一個匯出目的地『Google Ads 名單』,類型是 google_ads,客戶 <customer_id>(developer_token 等憑證我會給)。"),
            ("Tableau", "建一個匯出目的地『Tableau 發布』,類型是 tableau,server <位址>、site <站台>、project <專案>(帳密或 token 我會給)。"),
            ("電子豹(Email 服務)", "建一個匯出目的地『電子豹名單』,類型是 news_leopard,account <帳號>(api_key 我會給)。"),
            ("不確定要填什麼", "我要建一個匯出目的地,類型是 <上面其中一種>。先告訴我這個類型需要哪些連線 / 設定欄位,我再一項一項提供給你。"),
            ("列出所有可用類型", "我可以把名單匯出到哪些地方?列出所有支援的匯出目的地類型,各自大概要填什麼。"),
        ],
        "🔄 同步(把名單送出去)": [
            ("把行動資料同步到目的地", "把『客戶輪廓』行動資料同步到『每日名單匯出』目的地,送出它全部的欄位。"),
            ("把分群同步到目的地(挑欄位)", "把『高價值客戶』分群同步到『FB 再行銷受眾』目的地,送出 <姓名、email、手機> 這幾個欄位。"),
            ("送出前先篩選(sync filter)", "把『客戶輪廓』同步到『電子報名單』,但只送 <email 有值> 的客戶——沒有 email 的先濾掉再送。"),
            ("排程每天自動送", "把『客戶輪廓』同步到『每日名單匯出』,排程設成 <每天早上 6 點> 自動執行。"),
            ("跟著來源自動更新(follow_refresh)", "把『客戶輪廓』同步到『每日名單匯出』,設成來源資料一更新就跟著送(follow_refresh)。"),
            ("先不排程、只手動觸發(none)", "把『客戶輪廓』同步到『每日名單匯出』,先不要排程(none),我要送的時候再手動觸發。"),
            ("加一個計算欄位再送", "把『客戶輪廓』同步到『每日名單匯出』,另外加一個輸出欄位『全名』=用公式把 <名欄位> 和 <姓欄位> 串起來。"),
            ("現在就手動送一次(會真的送出)", "現在就把『每日名單匯出』這個同步手動執行一次——注意這會真的把資料送到目的地。"),
            ("看同步執行結果", "『每日名單匯出』這個同步最近幾次執行的結果如何?成功還是失敗、各送了多少列?"),
        ],
        "🗃️ 特徵商店(即時查特徵)": [
            ("把行動資料做成特徵商店", "把『客戶輪廓』行動資料做成特徵商店『客戶特徵』,用它輸出欄位裡代表客戶 id 的那一個當查詢索引(索引必須是行動資料的輸出欄位),之後可即時查單一客戶的特徵。"),
            ("複合索引", "把『商品特徵』行動資料做成特徵商店,用 <門市 id> + <商品 id> 兩個欄位當複合查詢索引。"),
            ("排程更新", "把『客戶輪廓』做成特徵商店『客戶特徵』,索引用 <customer_id>,排程設成 <每天> 更新一次。"),
            ("跟著來源更新(follow_refresh)", "把『客戶輪廓』做成特徵商店,索引 <customer_id>,設成來源一更新就跟著更新(follow_refresh)。"),
            ("即時查某個對象的特徵", "查特徵商店『客戶特徵』裡 <某個 customer_id> 的特徵值。"),
        ],
        "🔍 查詢 / 探索(看看建了什麼)": [
            ("盤點我建了哪些東西", "幫我盤點目前建了哪些分析主體、事件、指標、標籤、分群、行動資料、同步目的地、同步、特徵商店,各用對應的清單列出來。"),
            ("列出所有分群 + 人數", "列出我建好的所有分群,各有多少人。"),
            ("預覽行動資料前幾筆", "預覽『客戶輪廓』行動資料的前 <10> 筆資料。"),
            ("看某分群的名單", "把『高價值客戶』分群的成員名單拉出來給我看。"),
            ("看某指標 / 標籤的值", "看『客戶』的『總消費金額』<指標 / 標籤> 算出來長怎樣,拉幾筆看看。"),
            ("查單一對象的完整輪廓", "把 <某個 customer_id> 這個客戶的完整輪廓(各標籤值)拉出來。"),
            ("某資料來源有哪些表 / 欄位", "『<資料來源名>』這個資料來源裡有哪些表?列出各表的欄位跟型別。"),
            ("找某個資源", "幫我找名字含『<關鍵字>』的 <分群 / 標籤 / 行動資料>。"),
        ],
        "🚀 一鍵完整流程": [
            ("從連線到分群一次做完",
             "接上我的資料倉(類型 <postgres / …>,連線資訊我會給),讀完資料結構後,"
             "主動提議一整套以『客戶』為中心的分析(客戶分析主體、交易事件、幾個常用指標如"
             "總消費 / 消費次數、幾個標籤含近因 / 頻率 / 金額、再分出高價值客戶群),列給我看;"
             "我說『好』你就依序建立。"),
            ("建好再串一個行動資料 + 準備匯出",
             "在你已幫我建好的 CDP 上,再建一個『客戶輪廓』行動資料(姓名 + 總消費 + 所屬分群),"
             "並告訴我之後要怎麼把它同步 / 匯出到外部工具(先不要實際觸發)。"),
        ],
    },
    "en": {
        "🔌 Connect Data": [
            ("PostgreSQL", "Connect my PostgreSQL warehouse. Type is postgres — host <host>, port <port, default 5432>, user <user>, password <password>, database <db>, schema <schema>, data source name <name, e.g. My Warehouse>. After reading the full schema, propose which Entities (things to analyze, e.g. customers) and Events (things that happen, e.g. transactions) to build — tell me first, I'll confirm."),
            ("MySQL", "Connect my MySQL warehouse. Type is mysql — host <host>, port <port, default 3306>, user <user>, password <password>, database <db>. After reading the schema, propose the Entities/Events to build."),
            ("Greenplum", "Connect my Greenplum warehouse. Type is greenplum — host <host>, port <port, default 5432>, user <user>, password <password>, database <db>, schema <schema>. After reading the schema, propose a design."),
            ("SQL Server", "Connect my SQL Server warehouse. Type is sqlserver — host <host>, port <port, default 1433>, user <user>, password <password>, database <db>. After reading the schema, propose a design."),
            ("Oracle", "Connect my Oracle warehouse. Type is oracle — host <host>, port <port, default 1521>, user <user>, password <password>, database <SID or service name>. After reading the schema, propose a design."),
            ("Sybase", "Connect my Sybase warehouse. Type is sybase — host <host>, port <port, default 5000>, user <user>, password <password>, database <db>. After reading the schema, propose a design."),
            ("BigQuery", "Connect my BigQuery. Type is bigquery — authenticate with a service account: project_id <GCP project id>, dataset <dataset>, keyfile <paste the service account JSON>. (BigQuery has no host/port/password.) After reading the schema, propose a design."),
            ("With materialization on", "Connect my <postgres / mysql / …> warehouse (I'll give the connection info) with materialization ON — so segments / action datasets I build later can be materialized into physical tables for faster queries. After connecting, propose a design."),
            ("Not sure of the details / type", "I want to connect a warehouse but I'm not sure what to fill in. My warehouse type is <one of postgres / mysql / greenplum / sqlserver / oracle / bigquery / sybase>. First tell me which connection fields that type needs, then I'll provide them one by one."),
            ("Just connect and show what's there", "Connect my warehouse (type <your type>, I'll give the connection info), read the structure, and just tell me what tables exist, roughly what columns each has, and how many datasets you see — don't build anything yet."),
        ],
        "👤 Entity (what you analyze)": [
            ("Customer (single key)", "Turn <customer master, e.g. dim_customer> into an Entity called 'Customer', keyed by <primary key, e.g. customer_id>."),
            ("Customer (composite key)", "Turn <customer table> into an Entity 'Customer' keyed by <company_code> + <customer_code> as a composite primary key (each row identified by both columns)."),
            ("Member", "Turn <members table> into a 'Member' Entity, keyed by <member_id>."),
            ("Product", "Turn <product master> into a 'Product' Entity, keyed by <product_id>."),
            ("Store", "Turn <stores table> into a 'Store' Entity, keyed by <store_id>."),
            ("Card holder", "Turn <card_holder table> into a 'Card Holder' Entity, keyed by <customer_id>."),
            ("Account", "Turn <accounts table> into an 'Account' Entity, keyed by <account_id>."),
            ("Supplier", "Turn <suppliers table> into a 'Supplier' Entity, keyed by <supplier_id>."),
            ("Sales rep", "Turn <employees table> into a 'Sales Rep' Entity, keyed by <employee_id>."),
            ("Subscriber", "Turn <subscribers list> into a 'Subscriber' Entity, keyed by <subscriber_id>."),
            ("Device", "Turn <devices table> into a 'Device' Entity, keyed by <device_id>."),
        ],
        "📅 Event (what happens)": [
            ("Transaction (same-name key)", "Turn <transactions table> into an Event 'Transaction' linked to 'Customer' — both sides have <customer_id>, so match directly."),
            ("Transaction (different-name key)", "Turn <transactions table> into an Event 'Transaction' linked to 'Customer': customer side <customer_id> to transaction side <buyer_id> (different names, same customer)."),
            ("One event to many entities (star)", "Turn <FactInternetSales> into a 'Sales' Event linked to several entities at once: <CustomerKey→Customer>, <ProductKey→Product>, <PromotionKey→Promotion>, <CurrencyKey→Currency> (match the same-name *Key on each)."),
            ("Formula join (needs a transform)", "Turn <transactions table> into a 'Transaction' Event linked to 'Customer', but the ids are formatted differently on each side — use a formula to normalize them before joining (e.g. one side is plain digits, the other has a prefix)."),
            ("Order", "Turn <orders table> into an 'Order' Event, linked to 'Customer' (<customer_id> to <order_customer_id>)."),
            ("Return", "Turn <returns table> into a 'Return' Event, linked to 'Customer' (<customer_id> to <return_customer_id>)."),
            ("Page click", "Turn <clickstream table> into a 'Page Click' Event, linked to 'Customer' (<customer_id> to <user_id>)."),
            ("Add to cart", "Turn <cart events table> into an 'Add to Cart' Event, linked to 'Customer' (<customer_id> to <cart_user_id>)."),
            ("Support call", "Turn <support log table> into a 'Support Call' Event, linked to 'Customer' (<customer_id> to <caller_id>)."),
            ("Email open", "Turn <email opens table> into an 'Email Open' Event, linked to 'Subscriber' (<subscriber_id> to <recipient_id>)."),
            ("Campaign participation", "Turn <campaign signups table> into a 'Campaign Participation' Event, linked to 'Member' (<member_id> to <participant_id>)."),
        ],
        "📊 Metric (a computed number)": [
            ("Total Spend = SUM", "Give 'Customer' a Metric 'Total Spend' = SUM of the Transaction <amount column, e.g. amount>."),
            ("Avg Order Value = AVG", "Give 'Customer' a Metric 'Avg Order Value' = AVG of the <amount column>."),
            ("Order Count = COUNT", "Give 'Customer' a Metric 'Order Count' = COUNT of Transaction events."),
            ("Max Single = MAX", "Give 'Customer' a Metric 'Max Single Purchase' = MAX of the <amount column>."),
            ("Min Single = MIN", "Give 'Customer' a Metric 'Min Single Purchase' = MIN of the <amount column>."),
            ("Nonzero Avg = AVG_NONZERO", "Give 'Customer' a Metric 'Nonzero Avg Spend' = AVG_NONZERO of the <amount column> (ignores 0s)."),
            ("Distinct Products = COUNT_DISTINCT", "Give 'Customer' a Metric 'Distinct Products' = COUNT_DISTINCT of <product column, e.g. product_id>."),
            ("Distinct Sum = SUM_DISTINCT", "Give 'Customer' a Metric 'Distinct Amount Sum' = SUM_DISTINCT of the <amount column> (dedupe then sum)."),
            ("Distinct Avg = AVG_DISTINCT", "Give 'Customer' a Metric 'Distinct Amount Avg' = AVG_DISTINCT of the <amount column>."),
            ("Gross Profit = formula", "Give 'Customer' a formula Metric 'Gross Profit' = <total sales column, e.g. SalesAmount> minus <total cost column, e.g. TotalProductCost> (SUM each)."),
            ("Margin % = formula", "Give 'Customer' a formula Metric 'Margin %' = (<total sales> minus <total cost>) divided by <total sales>."),
        ],
        "🏷️ Trait (attributes / computed values)": [
            ("[aggregation] Total Spend = SUM", "Give 'Customer' an aggregation Trait 'Total Spend' = SUM of the Transaction <amount column>."),
            ("[aggregation] Purchase Count = COUNT", "Give 'Customer' an aggregation Trait 'Purchase Count' = COUNT of Transactions."),
            ("[aggregation] Max Single = MAX", "Give 'Customer' an aggregation Trait 'Max Single Purchase' = MAX of the <amount column>."),
            ("[aggregation] Min Single = MIN", "Give 'Customer' an aggregation Trait 'Min Single Purchase' = MIN of the <amount column>."),
            ("[aggregation] Avg Order Value = AVG", "Give 'Customer' an aggregation Trait 'Avg Order Value' = AVG of the <amount column>."),
            ("[compute] Last Purchase Amount (latest)", "Give 'Customer' a compute Trait 'Last Purchase Amount' = the <amount column> of the MOST RECENT Transaction (ordered by <time column>, the last one). Pick the compute function that fits this warehouse's dialect (list_compute_functions)."),
            ("[compute] First Purchase Amount (earliest)", "Give 'Customer' a compute Trait 'First Purchase Amount' = the <amount column> of the FIRST Transaction (ordered by <time column>, first one; one_of_first_event)."),
            ("[compute] Days Since Last Purchase (recency)", "Give 'Customer' a compute Trait 'Days Since Last Purchase' = days_since_last_event on Transactions."),
            ("[compute] Tenure Days (since first)", "Give 'Customer' a compute Trait 'Tenure Days' = days_since_first_event on Transactions."),
            ("[compute] Median Spend = median", "Give 'Customer' a compute Trait 'Median Spend' = median of the <amount column>."),
            ("[compute] Upper Quartile Spend", "Give 'Customer' a compute Trait 'Upper-Quartile Spend' = upper_quartile of the <amount column>."),
            ("[compute] Most Frequent Category", "Give 'Customer' a compute Trait 'Most Frequent Category' = the most frequent <category column> across Transactions (one_of_most_frequent_event)."),
            ("[compute] Product of Biggest Purchase", "Give 'Customer' a compute Trait 'Product of Biggest Purchase' = the <product column> of the highest-amount Transaction (one_of_highest_event, ordered by <amount column>)."),
            ("[compute] Distinct Products List", "Give 'Customer' a compute Trait 'Products Bought' = the distinct list of <product column> across Transactions (unique_event_list)."),
            ("[derive] AOV = formula over traits", "Give 'Customer' a derive Trait 'Avg Order Value' = 'Total Spend' divided by 'Purchase Count' (reference existing traits by their current names)."),
            ("[derive] Discount Rate = formula over traits", "Give 'Customer' a derive Trait 'Discount Rate' = 'Total Discount' divided by 'Total Spend'."),
            ("[sql] Custom-SQL Trait", "Give 'Customer' a sql Trait '<name>' using this SQL I write: <your SQL> (the point: SELECT the customer primary key + one computed column, FROM some table; double-quote mixed-case names)."),
        ],
        "🎯 Segment (a group)": [
            ("High-value (metric > threshold)", "Build a Segment 'High-Value': 'Total Spend' over <10000>."),
            ("Low-frequency (metric < threshold)", "Build a Segment 'Low-Frequency': 'Purchase Count' under <3>."),
            ("Amount range (between)", "Build a Segment 'Mid-Range': 'Total Spend' between <5000> and <20000>."),
            ("Outside a range (not-between)", "Build a Segment 'Not Mid-Range': 'Total Spend' outside <5000>–<20000>."),
            ("Equals a value (is)", "Build a Segment 'Specific Tier': <tier trait> is <Gold>."),
            ("In a list (in)", "Build a Segment 'Platinum/Gold': <card tier trait> in [<Platinum>, <Gold>]."),
            ("Not in a list (not-in)", "Build a Segment 'Real Customers': <name trait> not in [<test>, <internal>]."),
            ("Is null", "Build a Segment 'No Email': <email trait> has no value."),
            ("Is not null", "Build a Segment 'SMS-Reachable': <mobile trait> has a value."),
            ("Recent count (eventCount + relative time)", "Build a Segment 'Frequent Buyers': more than <5> Transaction events in the last <30 days>."),
            ("Event aggregate (eventAggr)", "Build a Segment 'High Avg Spend': average Transaction <amount> over <2000>."),
            ("Metric threshold (metric)", "Build a Segment 'High LTV': 'Lifetime Value' metric over <50000>."),
            ("Reference another segment (segment)", "Build a Segment 'Subset of VIP': members of the 'VIP' segment, plus <some condition>."),
            ("Funnel / sequence (eventAnalytics)", "Build a Segment 'Completed Purchase Flow': customers who did <view product → add to cart → complete transaction> in order."),
            ("Multi-condition AND + exclude group", "Build a Segment 'Active High-Value Non-Churned': 'Total Spend' over <10000> AND 'Days Since Last Purchase' within <30 days>, excluding the 'Churned' segment."),
            ("Multi-condition OR", "Build a Segment 'Reachable': customers with 'Has Email' OR 'Has Mobile'."),
        ],
        "📦 Action Dataset (output columns)": [
            ("Customer Profile (trait)", "Build a trait Action Dataset 'Customer Profile' outputting <name, total spend, segment> trait columns."),
            ("Contact List (trait)", "Build a trait Action Dataset 'Contact List' outputting <name, email, mobile> for export."),
            ("RFM List (trait)", "Build a trait Action Dataset 'RFM List' outputting <days-since-last-purchase (recency), purchase count (frequency), total spend (monetary)>."),
            ("High-Value List (trait)", "Build a trait Action Dataset 'High-Value List' outputting <name, total spend, last purchase amount>."),
            ("Scoped to a segment (trait + condition)", "Build a trait Action Dataset 'VIP Contact List' outputting <name, email, mobile> for only the customers in the 'VIP' segment."),
            ("Customer Metric Rollup (metric)", "Build a metric Action Dataset 'Customer Metric Rollup' with 'Customer' as the dimension, rolling up <total spend, purchase count>."),
            ("Monthly Sales (metric + bucket)", "Build a metric Action Dataset 'Monthly Sales' bucketing 'Total Spend' by <month>, grouped by 'Customer'."),
            ("Segment Sizes (metric)", "Build a metric Action Dataset 'Segment Sizes' rolling up the customer count per segment."),
            ("Custom-SQL export (sql)", "Build a sql Action Dataset '<name>' using SQL I write (FROM real tables/columns; double-quote mixed-case names)."),
            ("Product Sales Detail (sql)", "Build a sql Action Dataset 'Product Sales Detail' outputting <product, quantity, amount> via SQL."),
            ("Materialized as a table (materialization=table)", "Build a trait Action Dataset 'Customer Profile (daily)' with materialization = table (physically materialized, schedulable), outputting <name, total spend, segment>."),
        ],
        "📮 Destination (where to export)": [
            ("Local file export (CSV)", "Create an export destination 'Daily List Export', type local file export (export_file), output as a CSV file."),
            ("FTP / SFTP", "Create an export destination 'Customer List FTP', type <ftp or sftp> — host <host>, user <user>, password <password>, remote dir <dir>, output as CSV."),
            ("Google Sheets", "Create an export destination 'Marketing Sheet', type google_sheets, into spreadsheet <spreadsheet_id> / worksheet <worksheet_name> (I'll give the service account key)."),
            ("Mailchimp", "Create an export destination 'Newsletter List', type mailchimp, into list <list_id> (I'll give api_key and server_prefix)."),
            ("Facebook Custom Audience", "Create an export destination 'FB Retargeting Audience', type facebook_custom_audience, pushing to ad account <ad_account_id> audience <audience_id> (I'll give the access_token)."),
            ("Google Ads list", "Create an export destination 'Google Ads List', type google_ads, customer <customer_id> (I'll give the developer_token etc.)."),
            ("Tableau", "Create an export destination 'Tableau Publish', type tableau — server <host>, site <site>, project <project> (I'll give the credentials or token)."),
            ("News Leopard (email)", "Create an export destination 'News Leopard List', type news_leopard, account <account> (I'll give the api_key)."),
            ("Not sure what to fill in", "I want to create an export destination, type <one of the above>. First tell me which connection / config fields that type needs, then I'll provide them one by one."),
            ("List all available types", "Where can I export my lists to? List every supported destination type and roughly what each needs."),
        ],
        "🔄 Sync (send it out)": [
            ("Sync an action dataset to a destination", "Sync the 'Customer Profile' action dataset to the 'Daily List Export' destination, sending all of its columns."),
            ("Sync a segment to a destination (pick columns)", "Sync the 'High-Value Customers' segment to the 'FB Retargeting Audience' destination, sending <name, email, mobile>."),
            ("Filter rows before sending (sync filter)", "Sync 'Customer Profile' to the 'Newsletter List', but only send customers whose <email is not null> — drop the ones with no email before sending."),
            ("Schedule daily", "Sync 'Customer Profile' to 'Daily List Export', scheduled to run automatically <every day at 6am>."),
            ("Follow the source (follow_refresh)", "Sync 'Customer Profile' to 'Daily List Export', set to send whenever the source data refreshes (follow_refresh)."),
            ("No schedule, manual only (none)", "Sync 'Customer Profile' to 'Daily List Export' with no schedule (none) — I'll trigger it manually when I want to send."),
            ("Add a computed column then send", "Sync 'Customer Profile' to 'Daily List Export', plus one extra output column 'Full Name' = a formula concatenating <first name column> and <last name column>."),
            ("Send it now, manually (this really sends)", "Trigger the 'Daily List Export' sync once, right now — note this actually sends the data to the destination."),
            ("Check sync run results", "How did the last few runs of the 'Daily List Export' sync go — succeeded or failed, and how many rows each?"),
        ],
        "🗃️ Feature Store (real-time lookup)": [
            ("Turn an action dataset into a feature store", "Turn the 'Customer Profile' action dataset into a feature store 'Customer Features', indexed by whichever of its OUTPUT columns holds the customer id (the index must be an output column of the action dataset), so I can look up one customer's features in real time."),
            ("Composite index", "Turn the 'Product Features' action dataset into a feature store, using <store id> + <product id> as a composite lookup index."),
            ("Scheduled refresh", "Turn 'Customer Profile' into a feature store 'Customer Features', indexed by <customer_id>, refreshing <daily>."),
            ("Follow the source (follow_refresh)", "Turn 'Customer Profile' into a feature store, indexed by <customer_id>, set to refresh whenever the source refreshes (follow_refresh)."),
            ("Look up one entity's features", "Look up the features for <some customer_id> in the 'Customer Features' feature store."),
        ],
        "🔍 Query / Explore (see what's built)": [
            ("Inventory what I've built", "Give me an inventory of everything I've built — entities, events, metrics, traits, segments, action datasets, destinations, syncs, feature stores — each via its own list."),
            ("List all segments + sizes", "List all the segments I've built and how many members each has."),
            ("Preview an action dataset", "Preview the first <10> rows of the 'Customer Profile' action dataset."),
            ("Show a segment's members", "Pull up the member list of the 'High-Value Customers' segment."),
            ("See a metric / trait's values", "Show me what 'Customer' 'Total Spend' <metric / trait> comes out to — pull a few rows."),
            ("One entity's full profile", "Pull the full profile (all trait values) for <some customer_id>."),
            ("What tables / columns a data source has", "What tables are in the '<data source name>' data source? List each table's columns and types."),
            ("Find a resource", "Find the <segment / trait / action dataset> whose name contains '<keyword>'."),
        ],
        "🚀 One-shot full flow": [
            ("From connect to segment in one go",
             "Connect my warehouse (type <postgres / …>, I'll give the connection info), "
             "read the schema, then propose a full customer-centric analysis (Customer "
             "entity, Transaction event, common metrics like total spend / order count, a "
             "few traits covering recency / frequency / monetary, and a high-value segment) "
             "— list it for me; when I say OK, build it in order."),
            ("Then add an action dataset + prep export",
             "On the CDP you built for me, add a 'Customer Profile' action dataset (name + "
             "total spend + segment), and tell me how I'd later sync / export it to an "
             "external tool (don't actually trigger it yet)."),
        ],
    },
}


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT(依語言)。核心規則兩版一致,差在回覆語言 + 商業用語對照。
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = {
    "zh": """\
你是 Segma CDP 的建構助手,透過連線的 segma MCP 工具操作。風格是「主動提案、
使用者確認」——不要用瑣碎的逐欄提問折磨使用者。**一律用繁體中文回覆。**

# 鐵則(最優先):對使用者一律用商業語彙,絕不吐資料庫術語

你的對象是行銷 / 營運同仁,看不懂資料庫術語。**任何給使用者看的文字——提案、確認、
進度回報、錯誤說明——都絕對不能出現這些字:`Dim`、`Fact`、`dimension`、`維度`、
`事實`、`事實表`。** 一律換成商業講法:

| 內部工具概念 | 對使用者要講 |
|---|---|
| Dim / dimension | **分析主體**(你要分析的對象,如客戶、會員、商品) |
| Fact | **事件**(發生的事,如交易、點擊、活動) |
| Metric | **指標** |
| Trait | **標籤 / 屬性** |
| Segment | **分群 / 受眾** |
| ActionDataset | **行動資料** |

工具名(`create_dim` / `create_fact` …)是內部 API,呼叫工具時照原樣用;但只要是
輸出給使用者的句子,就翻成上表右欄。連括號註解也不要放 `Dim` / `Fact`。
✗ 不要:「我建立了維度 DimCustomer,把 transaction_history 當事實表 join 上去」
✓ 要:「我建立了『客戶』這個分析主體,把『交易』事件串了上去」

# 核心工作流

資料來源是入口;分析主體 / 事件是它的結構化視角,指標 / 標籤 建在其上。步驟:
1. 取得連線資訊(host / port / 帳密 / database / schema),缺什麼直接問,別叫他排格式。
2. 建好資料來源後**立刻** refresh_data_source_schema,再用 list_data_source_columns
   **讀完整份 schema**(分頁讀完,別只看第一頁)。若 refresh 的 statistic 顯示有欄位、
   但 list 回 `metadata.count` 0,那是剛載入還沒可見——**重試 list 幾次**再下判斷,
   別因單一次矛盾的空讀取就說這個倉是空的。
3. **分析 schema,主動提出一套有意義的設計**(先提案不要建):一個真實倉常同時有好幾套
   不相干的資料,先分群、只在同群內把分析主體跟事件配起來,跨群不硬湊;指標 / 標籤依
   事件的數值欄位與對象屬性給有語意的內容。**具體列出**請使用者一次確認。
4. 確認後依序呼叫:create_dim → create_fact → create_metric → create_trait →
   (視需要)create_segment → create_action_dataset → …。引用資源先用 list_* / search_*
   換 id,別猜。

# 原則
- **商業語彙是鐵則(見開頭)。**
- 欄位值一律從 metadata 取,每個欄位怎麼填、值的形狀、join 怎麼判斷都看該工具的說明。
- **工具失敗時照實引用它回的『實際錯誤訊息』**自我修正或告訴使用者,絕不腦補理由;
  先照錯誤調整重試幾次,真的解不了才把原始錯誤原封不動交給使用者。
- 破壞性或會實際跑資料的動作(delete_* / trigger_* / 觸發同步)先確認。
- 提案用清單、簡潔。
- 建 export_file 匯出目的地時,使用者只說「匯出 CSV」沒指定細節,就直接用這組合理預設把它建起來,別回頭一項項問:default_filename="data_%Y%m%d_%H%M%S.csv"、default_file_format="csv"、default_compression="zip"、default_sep=","、default_line_terminator="windows"、default_header=true。使用者有講的才覆蓋。
- 「盤點 / 列出我建了什麼」用對應的 list_*(list_destinations / list_syncs / list_feature_stores / list_segments / list_action_datasets …),別用 get_profile 之類的工具亂湊;查不到就說沒有,絕不腦補名稱或 id。
- 特徵商店(feature store)的索引欄位必須是「來源行動資料的輸出欄位」之一(用 list_action_dataset_columns 查),不是 dim 的主鍵欄名;要用客戶 id 當索引,該行動資料就得先把 id 當成一個輸出欄位輸出。
- 建 feature store 或 sync 時,使用者沒指定更新排程就用 cron="none"(手動、不自動跑),直接建起來別回頭問;要自動更新才用 "follow_refresh"(跟著來源)或 cron 表達式。
- **引用資源時自己查、直接建,別回頭問使用者 id。** 使用者用『名稱』提到某個指標 / 標籤 / 分群 / 分析主體(例如建分群時說「『總消費金額』指標超過 X」),你就自己用 list_*/search_* 把名稱換成 id、並判斷它掛在哪個分析主體上,然後**直接把分群 / 行動資料建起來**——不要反問使用者「那個指標的 id 是多少」「它屬於哪個分析主體」「要不要物化」。這些你都查得到,問了就違反「主動、少問」。真的查無此名稱,才回報找不到並列出相近的幾個讓他選。
- **使用者講的『指標』不一定是 Metric 資源。** 標籤(Trait)本身也有 trait_type=metric 這種型別,而且使用者常把聚合標籤(如「總消費金額」)口語講成「指標」。所以要解析一個叫某名字的「指標」時,**list_metrics 跟 list_traits 兩邊都查**,找到後用對應的 condition_type(metric 或 trait)去建條件——不要只 list_metrics 查不到就停下來問或放棄。
- 建分群 / 行動資料使用者沒特別要求時,用非物化(查詢時計算)的預設直接建,別回頭問要不要落地成表;要落地(materialization=table)是使用者明講才做。
""",
    "en": """\
You are Segma CDP's building assistant, operating through the connected segma MCP
tools. Your style is "propose, then confirm" — don't pester the user field by field.
**Always reply in English.**

# TOP RULE: always speak to the user in plain business language, never DB jargon

Your audience is marketing / ops people who don't know database terms. **Any text the
user sees — proposals, confirmations, progress, error explanations — must NEVER contain
the words `Dim`, `Fact`, `dimension`, or `fact table`.** Translate to business terms:

| internal tool concept | say to the user |
|---|---|
| Dim / dimension | **Entity** (the thing you analyze: customer, member, product) |
| Fact | **Event** (something that happened: transaction, click, campaign) |
| Metric | **Metric** |
| Trait | **Trait** (an attribute of the entity) |
| Segment | **Segment** / audience |
| ActionDataset | **Action Dataset** |

Tool names (`create_dim` / `create_fact` …) are the internal API — use them verbatim
when calling tools; but every sentence shown to the user uses the right-hand column,
including parenthetical glosses (say "Entity", not "(Dim)").
✗ Don't: "I created the DimCustomer dimension and joined transaction_history as a fact table"
✓ Do: "I created the 'Customer' Entity and linked the 'Transaction' Event to it"

# Core workflow

The data source is the entry point; Entities / Events are its structured view; Metrics /
Traits build on top. Steps:
1. Get connection info (host / port / credentials / database / schema); ask directly for
   whatever's missing — don't make the user format it.
2. Right after creating the data source, call refresh_data_source_schema, then
   list_data_source_columns to **read the whole schema** (page through it; don't judge
   from the first page). If refresh's statistic shows columns exist but list returns
   `metadata.count` 0, the freshly-loaded rows aren't visible yet — **retry list a few
   times** before concluding; never tell the user the warehouse is empty on a single
   zero read that contradicts the statistic.
3. **Analyze the schema and proactively propose a meaningful design** (propose first,
   don't build): a real warehouse often holds several unrelated datasets — group the
   tables first, pair an Entity with an Event only within one group, don't force
   cross-group joins; base Metrics / Traits on the events' numeric columns and the
   entity's attributes. **List it concretely** and get one confirmation.
4. After confirmation, call in order: create_dim → create_fact → create_metric →
   create_trait → (as needed) create_segment → create_action_dataset → …. Resolve ids
   via list_* / search_* first — don't guess.

# Principles
- **Business language is the top rule (see above).**
- Take field values from metadata; how to fill each field, value shapes, and how to pick
  join columns all come from each tool's own description.
- **When a tool fails, quote its ACTUAL error message** to self-correct or tell the user;
  never invent a plausible-sounding reason. Retry a few times per the real error; only
  hand the raw error to the user if you truly can't resolve it.
- Confirm before destructive or data-running actions (delete_* / trigger_* / syncs).
- Keep proposals short and in lists.
- When creating an export_file destination and the user just says "export CSV" without details, build it directly with these sensible defaults instead of asking field by field: default_filename="data_%Y%m%d_%H%M%S.csv", default_file_format="csv", default_compression="zip", default_sep=",", default_line_terminator="windows", default_header=true. Override only what the user specified.
- For "inventory / list what I've built", use the matching list_* (list_destinations / list_syncs / list_feature_stores / list_segments / list_action_datasets …); don't cobble it together from get_profile or similar. If there's nothing, say so — never invent names or ids.
- A feature store's index column must be one of the SOURCE action dataset's OUTPUT columns (check with list_action_dataset_columns), not the dim's primary-key column name. To key by customer id, the action dataset must first output that id as one of its columns.
- When creating a feature store or sync and the user didn't specify a schedule, use cron="none" (manual, no auto-run) and build it directly instead of asking; use "follow_refresh" (chain to the source) or a cron expression only when the user wants auto-refresh.
- **Resolve references yourself and build directly — never ask the user for an id.** When the user refers to a metric / trait / segment / entity by NAME (e.g. "build a segment where the 'Total Spend' metric is over X"), use list_*/search_* to turn that name into an id and to figure out which entity it hangs off, then **build the segment / action dataset directly** — do NOT ask the user "what's that metric's id", "which entity does it belong to", or "do you want it materialized". You can look all of that up; asking violates "propose, don't pester". Only if the name genuinely isn't found do you report that and list a few close matches to pick from.
- **What the user calls a "metric" isn't always a Metric resource.** A Trait can have trait_type=metric, and users often call an aggregation trait (e.g. "Total Spend") a "metric" loosely. So to resolve a "metric" by name, check **both list_metrics AND list_traits**, then build the condition with the matching condition_type (metric or trait) — don't stop or give up just because list_metrics alone came up empty.
- For a segment / action dataset with no explicit request, build it non-materialized (computed at query time) by default instead of asking about materialization; only materialize (materialization=table) when the user explicitly says so.
""",
}

st.set_page_config(page_title="Segma MCP Chat", page_icon="🧩", layout="wide")


# ---------------------------------------------------------------------------
# 連線設定 helpers
# ---------------------------------------------------------------------------

def resolve_mcp_url() -> str:
    explicit = os.environ.get("SEGMA_MCP_URL", "").strip()
    if explicit:
        return explicit
    access = os.environ.get("SEGMA_ACCESS_URL", "").strip()
    if access and not access.startswith("{"):
        return access.rstrip("/") + "/mcp"
    return ""


def resolve_token() -> str:
    if _HAS_BRIDGE:
        segma_bridge.init()  # 每次 rerun 無條件呼叫
        token = segma_bridge.get_token()
        if token:
            return token
    if "token" not in st.session_state:
        url_token = st.query_params.get("token", None)
        if url_token:
            st.session_state.token = url_token
    return st.session_state.get("token", "")


def verify_tls() -> bool:
    return os.environ.get("SEGMA_MCP_VERIFY_TLS", "").strip().lower() in ("1", "true", "yes")


# Language selector first (top of sidebar) so everything below can use it.
# Default language comes from env SEGMA_UI_LANG (zh / en); user can still switch.
_env_lang = (os.environ.get("SEGMA_UI_LANG") or "").strip().lower()
_default_lang_idx = 1 if _env_lang in ("en", "english") else 0
with st.sidebar:
    lang_label = st.radio(UI["zh"]["lang"], list(LANG_OPTIONS), horizontal=True,
                          index=_default_lang_idx, key="lang_sel")
lang = LANG_OPTIONS[lang_label]
U = UI[lang]

st.title("🧩 Segma MCP Chat")
st.write(U["intro"])

# Every field below takes its env / resolved value as an EDITABLE default — the
# user can always override in the UI. MCP URL / Base URL / Model pre-fill the
# value; the two live credentials (token, API key) fall back to their env source
# when the field is left blank, so a segma_bridge-refreshed token still flows.
env_token = resolve_token()

with st.sidebar:
    st.header(U["conn"])
    mcp_url = st.text_input(U["mcp_url"], value=resolve_mcp_url(),
                            placeholder="https://localhost:1443/mcp",
                            help=U["mcp_url_help"]).strip()
    token_in = st.text_input(
        U["token"], type="password", help=U["token_help"],
        placeholder=(f"…{env_token[-6:]} (auto)" if env_token else ""),
    ).strip()
    token = token_in or env_token  # typed value wins; else the resolved/refreshed token

    # LLM 設定 — always-visible panel; env values are the defaults.
    with st.expander(U["llm_settings"], expanded=False):
        env_key = os.environ.get("LLM_API_KEY") or os.environ.get("llm_api_key", "")
        env_model = os.environ.get("LLM_MODEL") or os.environ.get("llm_model", "")
        env_base = os.environ.get("LLM_BASE_URL") or os.environ.get("llm_base_url", "")
        try:
            env_max = int(os.environ.get("LLM_MAX_TOKENS", "") or DEFAULT_MAX_TOKENS)
        except ValueError:
            env_max = DEFAULT_MAX_TOKENS

        llm_base_url = st.text_input(U["llm_base_url"], value=env_base,
                                     placeholder=U["llm_base_url_ph"]).strip()
        llm_model = st.text_input(U["llm_model"], value=env_model or DEFAULT_LLM_MODEL).strip()
        key_in = st.text_input(
            U["llm_api_key"], type="password",
            placeholder=(U["llm_api_key_env_ph"] if env_key else ""),
        ).strip()
        llm_api_key = key_in or env_key  # typed value wins; else fall back to env
        max_tokens = int(st.number_input(U["llm_max_tokens"], min_value=256, max_value=32768,
                                         value=env_max, step=256))

    require_confirm = st.toggle(U["confirm_toggle"], value=True, help=U["confirm_toggle_help"])

    if st.button(U["clear_chat"]):
        for k in ("messages", "pa_history", "pending_approval"):
            st.session_state.pop(k, None)
        st.rerun()

    st.header(U["templates"])
    st.caption(U["tpl_help"])
    for category, items in PROMPT_TEMPLATES[lang].items():
        with st.expander(category, expanded=False):
            for label, text in items:
                if st.button(label, key=f"tpl_{lang}_{category}_{label}", use_container_width=True):
                    st.session_state["draft_text"] = text
                    st.rerun()

missing = [
    name for name, value in {
        U["mcp_url"]: mcp_url,
        U["token"]: token,
        U["llm_api_key"]: llm_api_key,
        U["llm_model"]: llm_model,
    }.items() if not value
]
if missing:
    st.info(f"{U['missing']}:{', '.join(missing)}。", icon="🗝️")
    st.stop()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def build_agent(mcp_url: str, token: str, model_name: str, api_key: str, base_url: str,
                verify: bool, max_tokens: int, lang: str, require_confirm: bool):
    """以連線 / LLM / 語言 / 確認開關為 key 快取 agent;任一變動就重建。真正的組裝在
    agent_runtime.build_agent(純邏輯、可單元測試)。"""
    return _build_agent(
        mcp_url=mcp_url, token=token, model_name=model_name, api_key=api_key,
        base_url=base_url, verify=verify, max_tokens=max_tokens,
        instructions=SYSTEM_PROMPT[lang], require_confirm=require_confirm,
    )


agent = build_agent(mcp_url, token, llm_model, llm_api_key, llm_base_url,
                    verify_tls(), max_tokens, lang, require_confirm)


# ---------------------------------------------------------------------------
# 對話狀態 + 串流工具進度 + 破壞性動作確認
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pa_history" not in st.session_state:
    st.session_state.pa_history = []


def _norm_args(args):
    """工具參數可能是 JSON 字串或 dict;統一成可展開的物件。"""
    if isinstance(args, str):
        try:
            return json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return args
    return args


def render_tool_calls(calls: list[dict]) -> None:
    """把一輪的工具呼叫收在一個可展開區塊裡(完成後的靜態呈現)。"""
    if not calls:
        return
    with st.expander(f"{U['tool_calls']} ({len(calls)})", expanded=False):
        for c in calls:
            warn = " ⚠️" if is_destructive(c["name"]) else ""
            st.markdown(f"**`{c['name']}`**{warn}")
            st.json(_norm_args(c["args"]), expanded=False)
            result = c.get("result")
            if result is not None:
                text = result if isinstance(result, str) else json.dumps(
                    result, ensure_ascii=False, default=str)
                st.caption(f"↳ {text[:400]}")


def _live_progress(placeholder, calls: list[dict]) -> None:
    """串流中即時更新的工具進度清單:⏳ 執行中 / ✅ 完成,破壞性標 ⚠️。"""
    lines = [U["spinner"]]
    for c in calls:
        icon = "✅" if c["done"] else "⏳"
        warn = " ⚠️" if is_destructive(c["name"]) else ""
        lines.append(f"- {icon} `{c['name']}`{warn}")
    placeholder.markdown("\n".join(lines))


def _msg_calls(calls: list[dict]) -> list[dict]:
    """收斂成存進對話紀錄的精簡格式(給 render_tool_calls 重播用)。"""
    return [{"name": c["name"], "args": c["args"], "result": c["result"]} for c in calls]


def run_turn(prompt, deferred_results=None, history=None) -> None:
    """跑一輪並**邊跑邊**顯示工具進度與文字。

    - prompt=有值:一般對話(chat_input / 範本送出)。
    - prompt=None + deferred_results:使用者按完核准後的續跑。
    - 若破壞性工具被攔下(agent 回 DeferredToolRequests),把待確認狀態存進
      session_state.pending_approval 並 rerun 到核准 UI(見 render_approval_ui)。
    """
    if history is None:
        history = st.session_state.pa_history
    if prompt is not None:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    text_buf = {"s": ""}

    with st.chat_message("assistant"):
        tools_ph = st.empty()
        text_ph = st.empty()

        def on_call(part):
            entry = {"name": part.tool_name, "args": part.args, "result": None, "done": False}
            by_id[part.tool_call_id] = entry
            calls.append(entry)
            _live_progress(tools_ph, calls)

        def on_result(part):
            entry = by_id.get(part.tool_call_id)
            if entry is not None:
                entry["result"] = part.content
                entry["done"] = True
            _live_progress(tools_ph, calls)

        def on_text(chunk, replace):
            text_buf["s"] = chunk if replace else text_buf["s"] + chunk
            text_ph.markdown(text_buf["s"])

        try:
            result = asyncio.run(stream_turn(
                agent, prompt=prompt, message_history=history,
                deferred_results=deferred_results,
                on_tool_call=on_call, on_tool_result=on_result, on_text=on_text))
        except Exception as exc:  # noqa: BLE001
            st.error(f"{U['run_failed']}:{type(exc).__name__}: {exc}")
            st.stop()

        # 收起串流清單,換成正式的可展開工具紀錄(順序:工具在上、文字在下)。
        with tools_ph.container():
            render_tool_calls(_msg_calls(calls))

        out = result.output
        if isinstance(out, DeferredToolRequests):
            # 破壞性工具被攔:已跑的工具留在紀錄,待確認的丟給 render_approval_ui。
            text_ph.info(U["approve_waiting"])
            st.session_state.pa_history = result.all_messages()
            st.session_state.messages.append(
                {"role": "assistant", "content": "", "tool_calls": _msg_calls(calls)})
            st.session_state.pending_approval = {
                "approvals": [
                    {"id": c.tool_call_id, "name": c.tool_name, "args": c.args}
                    for c in out.approvals
                ],
            }
        else:
            answer = (out if isinstance(out, str) else "") or text_buf["s"] or ""
            text_ph.markdown(answer)
            st.session_state.pa_history = result.all_messages()
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "tool_calls": _msg_calls(calls)})

    if "pending_approval" in st.session_state:
        st.rerun()


def _resume_after_approval(decisions: dict[str, bool]) -> None:
    """把使用者對每個待確認動作的核准 / 拒絕轉成 DeferredToolResults,續跑那一輪。"""
    pend = st.session_state.pop("pending_approval")
    results = DeferredToolResults()
    for a in pend["approvals"]:
        results.approvals[a["id"]] = (
            True if decisions.get(a["id"]) else ToolDenied(U["approve_denied_reason"]))
    run_turn(prompt=None, deferred_results=results, history=st.session_state.pa_history)
    st.rerun()


def render_approval_ui() -> None:
    """待確認狀態下顯示核准 UI:每個破壞性動作一個勾選框 + 執行 / 全部拒絕。"""
    pend = st.session_state.pending_approval
    with st.chat_message("assistant"):
        st.warning(U["approve_intro"])
        with st.form("approval_form"):
            for a in pend["approvals"]:
                st.markdown(f"⚠️ **`{a['name']}`**")
                st.json(_norm_args(a["args"]), expanded=False)
                st.checkbox(U["approve_this"], value=True, key=f"appr_{a['id']}")
            run_col, deny_col = st.columns(2)
            run = run_col.form_submit_button(U["approve_run"], type="primary", use_container_width=True)
            deny = deny_col.form_submit_button(U["approve_deny_all"], use_container_width=True)
    if run or deny:
        decisions = {
            a["id"]: (False if deny else bool(st.session_state.get(f"appr_{a['id']}", False)))
            for a in pend["approvals"]
        }
        _resume_after_approval(decisions)


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_tool_calls(message.get("tool_calls", []))
        if message["content"]:
            st.markdown(message["content"])


# 有待確認的破壞性動作時,只顯示核准 UI,先不接受新輸入。
if "pending_approval" in st.session_state:
    render_approval_ui()
    st.stop()


# A template was just sent — clear the draft BEFORE the text_area is created this
# run (mutating a widget-keyed value after instantiation errors).
if st.session_state.pop("_clear_draft", False):
    st.session_state["draft_text"] = ""

if st.session_state.get("draft_text"):
    st.caption(U["draft_hint"])
    st.text_area("draft", key="draft_text", height=160, label_visibility="collapsed")
    send_col, clear_col, _ = st.columns([1, 1, 4])
    if send_col.button(U["send"], type="primary", use_container_width=True):
        text = st.session_state["draft_text"].strip()
        if text:
            st.session_state["_clear_draft"] = True
            run_turn(text)
            st.rerun()
    if clear_col.button(U["clear"], use_container_width=True):
        st.session_state["_clear_draft"] = True
        st.rerun()

if prompt := st.chat_input(U["chat_ph"]):
    run_turn(prompt)
