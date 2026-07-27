# Example session — E2E demo ADs via MCP

真實測試:對話**逐字取自 app 左側的常用範本**(`< >` 佔位符填入真實資料),驅動 app 同一套 Agent 全程跑一遍——所以這份記錄同時是**範本的驗證**。過程中遇到的 MCP bug / 缺漏(若有)見文末。

> 建構器 = segma-streamlit-mcp 的同一套 SYSTEM_PROMPT + Agent(gpt-4o → Pydantic AI → segma MCP)。目標 = 本機 stack。基底 = e2e CDP。


## Turn 1 — 使用者(📦 行動資料(整理輸出欄位) · 自訂 SQL 匯出(sql))

> 建 sql 型行動資料『d_每月消費總覽』,建在資料來源『d_信用卡資料倉』上,用這段我自己寫的 SQL 當輸出:SELECT DATE_TRUNC('month', t.time)::date AS "month", COUNT(*) AS "txn_count", SUM(t.amount) AS "total_amount", AVG(t.amount) AS "avg_amount" FROM credit_card.transaction_history t WHERE t.time >= '2022-01-01' AND t.time < '2023-01-01' GROUP BY 1 ORDER BY 1(FROM 用真實存在的表 / 欄位,大小寫混合的名稱加雙引號)。


**工具呼叫**:

- ✅ `list_data_sources`
- ✅ `create_action_dataset`


**助手**:已成功建立行動資料『d_每月消費總覽』，基於資料來源『d_信用卡資料倉』，並使用您提供的 SQL 查詢作為輸出。這個行動資料將每月的交易數量、總消費金額和平均消費金額進行彙總。若有其他需求或需要進一步的操作，請隨時告訴我！


## Turn 2 — 使用者(📦 行動資料(整理輸出欄位) · 〔進階〕trait 行動資料 + 自訂條件)

> 建 trait 型行動資料『d_VIP行銷名單』,輸出 d_VIP級別、d_持卡人姓名、d_2022年總消費 這些標籤,只包含符合這些條件的客戶:『d_2022年總消費』介於 30000 到 999999999。(條件規則同分群:群內 AND、可另設一組排除、事件用『發生次數在 X 到 Y 之間』。)


**工具呼叫**:

- ✅ `list_traits`
- ✅ `list_traits`
- ✅ `list_traits`
- ✅ `create_action_dataset`


**助手**:已成功建立行動資料『d_VIP行銷名單』，基於資料來源『d_信用卡資料倉』。此行動資料輸出以下標籤：

- d_VIP級別
- d_持卡人姓名
- d_2022年總消費

並且只包含『d_2022年總消費』介於 30,000 到 999,999,999 的客戶。若有其他需求或需要進一步的操作，請隨時告訴我！


## Turn 3 — 使用者(📦 行動資料(整理輸出欄位) · 每月銷售(metric + 時間分桶))

> 建 metric 型行動資料『d_月度消費KPI』,把『d_購買金額加總』依 年 + 月(year-month) 分桶,並用一個標籤『d_VIP級別』當分組維度(metric 型行動資料至少要選一個標籤當維度)。


**工具呼叫**:

- ✅ `list_metrics`
- ✅ `list_traits`
- ✅ `create_action_dataset`


**助手**:已成功建立行動資料『d_月度消費KPI』，基於資料來源『d_信用卡資料倉』。此行動資料將『d_購買金額加總』依年 + 月(year-month)進行分桶，並使用標籤『d_VIP級別』作為分組維度。若有其他需求或需要進一步的操作，請隨時告訴我！


## Turn 4 — 使用者(📦 行動資料(整理輸出欄位) · 〔進階〕trait 行動資料 + 自訂條件)

> 建 trait 型行動資料『d_網路活躍名單』,輸出 d_總點擊次數、d_持卡人姓名 這些標籤,只包含符合這些條件的客戶:『d_總點擊次數』介於 3 到 999999。(條件規則同分群:群內 AND、可另設一組排除、事件用『發生次數在 X 到 Y 之間』。)


**工具呼叫**:

- ✅ `list_traits`
- ✅ `list_traits`
- ✅ `create_action_dataset`


**助手**:已成功建立行動資料『d_網路活躍名單』，基於資料來源『d_信用卡資料倉』。此行動資料輸出以下標籤：

- d_總點擊次數
- d_持卡人姓名

並且只包含『d_總點擊次數』介於 3 到 999,999 的客戶。若有其他需求或需要進一步的操作，請隨時告訴我！


## Turn 5 — 使用者(📦 行動資料(整理輸出欄位) · 〔進階〕trait 行動資料 + 自訂條件)

> 建 trait 型行動資料『d_重點再行銷名單』,輸出 d_VIP級別、d_持卡人姓名、d_2022年總消費 這些標籤,只包含符合這些條件的客戶:『d_2022年總消費』介於 10000 到 999999999,且『d_信用卡交易』事件發生次數在 1 到 100 之間;另外排除『d_VIP級別』是『一般』的、以及『d_最後消費距今天數』介於 180 到 999999999 的客戶。(條件規則同分群:群內 AND、可另設一組排除、事件用『發生次數在 X 到 Y 之間』。)


**工具呼叫**:

- ✅ `list_traits`
- ✅ `list_traits`
- ✅ `list_traits`
- ✅ `list_traits`
- ✅ `create_action_dataset`


**助手**:已成功建立行動資料『d_重點再行銷名單』，基於資料來源『d_信用卡資料倉』。此行動資料輸出以下標籤：

- d_VIP級別
- d_持卡人姓名
- d_2022年總消費

並且只包含符合以下條件的客戶：

- 『d_2022年總消費』介於 10,000 到 999,999,999
- 『d_信用卡交易』事件發生次數在 1 到 100 之間

同時排除以下條件的客戶：

- 『d_VIP級別』是『一般』
- 『d_最後消費距今天數』介於 180 到 999,999,999

若有其他需求或需要進一步的操作，請隨時告訴我！


---

_Generated 2026-07-27 13:06 by tools/gen_transcript.py (headless, real run; turns 取自 PROMPT_TEMPLATES)._
