# Example session — Full CDP build from templates via MCP

真實測試:對話**逐字取自 app 左側的常用範本**(`< >` 佔位符填入真實資料),驅動 app 同一套 Agent 全程跑一遍——所以這份記錄同時是**範本的驗證**。過程中遇到的 MCP bug / 缺漏(若有)見文末。

> 建構器 = segma-streamlit-mcp 的同一套 SYSTEM_PROMPT + Agent(gpt-4o → Pydantic AI → segma MCP)。目標 = 本機 stack。基底 = 空環境,從連接一個 PostgreSQL 資料倉開始。


## Turn 1 — 使用者(🔌 連接資料 · PostgreSQL)

> 接上我的 PostgreSQL 資料倉。類型是 postgres,連線:host <主機位址>, port <埠,預設 5432>, 帳號 postgres, 密碼 <密碼>, database postgres, schema public,資料來源名 b_信用卡倉。讀完整份資料結構後,主動提議可以建立哪些『分析主體』(要分析的對象,例如客戶)跟『事件』(發生的事,例如交易),先講給我聽,我確認你再建。


**工具呼叫**:

- ✅ `create_data_source`
- ✅ `refresh_data_source_schema`
- ✅ `list_data_source_columns`
- ✅ `list_data_source_columns`
- ✅ `list_data_source_columns`


**助手**:根據資料結構，我建議以下的『分析主體』與『事件』：

### 分析主體
1. **客戶 (Customer)**
   - 來自 `dimension.card_holder` 表
   - 重要欄位：`customer_id`, `name`, `email_address`, `mobile`, `date_of_birth`

2. **產品 (Product)**
   - 來自 `demo.DimProduct` 表
   - 重要欄位：`ProductKey`, `EnglishProductName`, `ListPrice`

3. **卡片持有人 (Card Holder)**
   - 來自 `dimension.card_holder` 表
   - 重要欄位：`customer_id`, `name`, `gender`, `age`, `card_issued_date`

### 事件
1. **交易 (Transaction)**
   - 來自 `credit_card.transaction_history` 表
   - 重要欄位：`transaction_id`, `amount`, `merchant_name`, `time`, `primary_holder_id`

2. **網路銷售 (Internet Sales)**
   - 來自 `demo.FactInternetSales` 表
   - 重要欄位：`OrderDate`, `SalesAmount`, `CustomerKey`, `ProductKey`

3. **行銷活動 (Campaign Event)**
   - 來自 `marketing.campaign_event` 表
   - 重要欄位：`campaign_id`, `campaign_name`, `event_type`, `customer_id`

這些主體與事件有清楚的資料欄位可以支援後續的指標計算和標籤產生。請確認是否要按照此設計進行建立，或者有其他想法。


## Turn 2 — 使用者(👤 分析主體(要分析的對象) · 客戶(單一主鍵))

> 把 dimension.card_holder 建成一個叫『b_客戶』的分析主體,用 customer_id 當識別。


**工具呼叫**:

- ✅ `create_dim`


**助手**:『b_客戶』這個分析主體已經建立完成，使用`customer_id`作為識別。您還想針對這個分析主體進行什麼操作，或是繼續建立其他『事件』或者配置其他資料設計?


## Turn 3 — 使用者(📅 事件(發生的事) · 交易(不同名欄位對應))

> 把 credit_card.transaction_history 建成『b_交易』事件,串到『b_客戶』:客戶端 customer_id 對交易端 primary_holder_id(名稱不同但代表同一個客戶)。


**工具呼叫**:

- ✅ `create_fact`


**助手**:『b_交易』這個事件已成功建立，並已串接到『b_客戶』分析主體，根據客戶端的 `customer_id` 與交易端的 `primary_holder_id` 進行關聯。您還需要進行什麼動作嗎？例如建立指標或標籤？


## Turn 4 — 使用者(📊 指標(可計算的數字) · 總消費金額 = SUM)

> 幫『b_客戶』算指標『b_總消費金額』= 把『b_交易』的 amount 加總(SUM)。


**工具呼叫**:

- ✅ `list_aggr_functions`
- ✅ `create_metric`


**助手**:已經成功建立指標『b_總消費金額』。這個指標是將所有『b_交易』中 `amount` 欄位的值進行加總，以提供『b_客戶』分析主體的總消費金額統計。還有其他的設置需要幫忙嗎？例如建立其他指標或是標籤？


## Turn 5 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔聚合〕消費次數 = COUNT)

> 幫『b_客戶』建聚合標籤『b_消費次數』= 數『b_交易』筆數(COUNT)。


**工具呼叫**:

- ✅ `create_trait`


**助手**:成功建立聚合標籤『b_消費次數』，用於計算每位客戶的交易次數。還有其他需要協助的部分嗎？例如更多的指標或標籤設定？


## Turn 6 — 使用者(🏷️ 標籤(對象的屬性 / 計算結果) · 〔計算〕距上次消費天數(近因))

> 幫『b_客戶』建計算標籤『b_距上次消費天數』= 距今最後一次『b_交易』的天數(days_since_last_event)。


**工具呼叫**:

- ✅ `list_compute_functions`
- ✅ `create_trait`


**助手**:已經成功建立計算標籤『b_距上次消費天數』，這能幫助計算每位客戶距離他們最後一次交易的天數。您是否還有其他方向需要協助的部分，或是想進一步操作這些資料？


## Turn 7 — 使用者(🎯 分群(篩出一群對象) · 高價值(指標超過門檻))

> 建分群『b_高價值客戶』:『b_總消費金額』指標 超過 10000。


**工具呼叫**:

- ✅ `create_segment`


**助手**:成功建立分群『b_高價值客戶』。此分群包含所有『b_總消費金額』指標超過 10,000 的客戶。您還需要其他幫助嗎？例如對這些分群進一步操作或設置資料同步？


## Turn 8 — 使用者(📦 行動資料(整理輸出欄位) · 客戶輪廓(trait))

> 建 trait 型行動資料『b_客戶輪廓』,輸出 b_消費次數、b_距上次消費天數 標籤欄位。


**工具呼叫**:

- ✅ `create_action_dataset`


**助手**:行動資料『b_客戶輪廓』已成功建立，輸出包含『b_消費次數』和『b_距上次消費天數』這兩個標籤欄位。還有其他需求或設定需要協助的嗎？例如資料同步或進一步的報表分析？


---

_Generated 2026-07-23 16:19 by tools/gen_transcript.py (headless, real run; turns 取自 PROMPT_TEMPLATES)._
