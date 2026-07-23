# Example session — Trait algorithms from templates (標籤:aggregation / compute / derive / sql) via MCP

真實測試:對話**逐字取自 app 左側的常用範本**(`< >` 佔位符填入真實資料),驅動 app 同一套 Agent 全程跑一遍——所以這份記錄同時是**範本的驗證**。過程中遇到的 MCP bug / 缺漏(若有)見文末。

> 建構器 = segma-streamlit-mcp 的同一套 SYSTEM_PROMPT + Agent(gpt-4o → Pydantic AI → segma MCP)。目標 = 本機 stack。基底 = 既有的 mcpdemo_ 信用卡 CDP(分析主體『mcpdemo_持卡人』+ 事件『mcpdemo_信用卡交易』)。


## Turn 1 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔聚合〕最高單筆 = MAX)

> 幫『mcpdemo_持卡人』建聚合標籤『t_最高單筆消費』= 取『mcpdemo_信用卡交易』的 amount 最大值(MAX)。


**工具呼叫**:

- ✅ `list_dims`
- ✅ `list_facts`
- ✅ `list_aggr_functions`
- ✅ `create_trait`


**助手**:已成功建立聚合標籤『t_最高單筆消費』。這個標籤將計算『mcpdemo_信用卡交易』中的 amount 欄位的最大值。


## Turn 2 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔聚合〕總消費金額 = SUM)

> 幫『mcpdemo_持卡人』建聚合(aggregation)標籤『t_總消費金額』= 把『mcpdemo_信用卡交易』的 amount 加總(SUM)。


**工具呼叫**:

- ✅ `create_trait`


**助手**:已成功建立聚合標籤『t_總消費金額』。這個標籤將計算『mcpdemo_信用卡交易』中的 amount 欄位的總和(SUM)。


## Turn 3 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔聚合〕消費次數 = COUNT)

> 幫『mcpdemo_持卡人』建聚合標籤『t_消費次數』= 數『mcpdemo_信用卡交易』筆數(COUNT)。


**工具呼叫**:

- ✅ `create_trait`


**助手**:已成功建立聚合標籤『t_消費次數』。這個標籤將計算『mcpdemo_信用卡交易』中的交易筆數(COUNT)。


## Turn 4 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔計算〕最近一次消費金額(取最後一次))

> 幫『mcpdemo_持卡人』建計算(compute)標籤『t_最近一次消費金額』= 取『mcpdemo_信用卡交易』裡最後一次交易的 amount 值(依 time 排序、取最後一筆那次)。算法照這個倉的方言挑對的(list_compute_functions)。


**工具呼叫**:

- ✅ `list_compute_functions`
- ✅ `create_trait`


**助手**:已成功建立計算標籤『t_最近一次消費金額』。這個標籤將取得『mcpdemo_信用卡交易』中根據時間排序的最後一次交易的 amount 值。


## Turn 5 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔計算〕消費金額中位數 = median)

> 幫『mcpdemo_持卡人』建計算標籤『t_消費金額中位數』= 取『mcpdemo_信用卡交易』的 amount 中位數(median)。


**工具呼叫**:

- ✅ `create_trait`


**助手**:已成功建立計算標籤『t_消費金額中位數』。這個標籤將計算『mcpdemo_信用卡交易』中 amount 欄位的中位數值。


## Turn 6 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔計算〕消費金額上四分位)

> 幫『mcpdemo_持卡人』建計算標籤『t_消費上四分位』= 取 amount 的上四分位(upper_quartile)。


**工具呼叫**:

- ✅ `create_trait`


**助手**:已成功建立計算標籤『t_消費上四分位』。這個標籤將計算『mcpdemo_信用卡交易』中 amount 欄位的上四分位值。


## Turn 7 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔衍生〕客單價 = 公式引用標籤)

> 幫『mcpdemo_持卡人』建衍生(derive)標籤『t_客單價』=『t_總消費金額』除以『t_消費次數』(引用我已建好的標籤,用它們現在的名稱)。


**工具呼叫**:

- ✅ `create_trait`


**助手**:已成功建立衍生標籤『t_客單價』。這個標籤將計算『t_總消費金額』除以『t_消費次數』來得到客單價。


## Turn 8 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔SQL〕用自訂 SQL 算標籤)

> 幫『mcpdemo_持卡人』建 sql 型標籤『t_持卡人城市』,用這段我自己寫的 SQL:SELECT customer_id AS "PK_customer_id", city AS "t_持卡人城市" FROM dimension.card_holder(要點:SELECT 客戶主鍵 + 一個計算欄位,FROM 某表;大小寫混合的表 / 欄位名記得加雙引號)。


**工具呼叫**:

- ✅ `create_trait`


**助手**:已成功建立 SQL 型標籤『t_持卡人城市』。此標籤利用您提供的 SQL 查詢來獲取持卡人城市的信息。


## Turn 9 — 使用者(🔍 查詢 / 探索(看看建了什麼) · 看某指標 / 標籤的值)

> 看『mcpdemo_持卡人』的『t_消費金額中位數』標籤 算出來長怎樣,拉幾筆看看。


**工具呼叫**:

- ✅ `list_traits`
- ✅ `get_trait_data`


**助手**:這是『mcpdemo_持卡人』的『t_消費金額中位數』標籤算出來的部分資料：

1. 消費金額中位數: 719.0
2. 消費金額中位數: 729.0
3. 消費金額中位數: 833.5
4. 消費金額中位數: 876.5
5. 消費金額中位數: 881.0

這些數值代表不同持卡人的消費金額中位數。


---

_Generated 2026-07-23 16:45 by tools/gen_transcript.py (headless, real run; turns 取自 PROMPT_TEMPLATES)._
