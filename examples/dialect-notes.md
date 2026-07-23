# 各方言建 CDP 的差異與已知問題 / Per-dialect notes

[session-01-build.md](session-01-build.md) 用 **MySQL** 當例子走完整條建置流程。同一套
範本在 SQL Server / Oracle / Postgres / Greenplum / BigQuery 上也各實跑過一遍;這頁把
「換方言時要注意什麼」和「過程中挖到的真問題」濃縮在一處,取代原本一個方言一個檔的做法。

> 這些是 headless 真跑(gpt-4o → segma MCP → 真實倉)濃縮出來的觀察,不是規格。每個
> 欄位怎麼填、join 怎麼判斷,仍以 MCP 工具自己的說明為準。連線資訊取自 gitignored 的
> `segma-backend/spec/e2e/config/data_sources.yml`。

## 共通觀察(所有方言)

- **`create_fact` 第一次常 ❌、retry 後 ✅**:agent 首次填 join 欄位常猜錯,靠工具回的
  實際錯誤自我修正(`retries=5`)。這是設計中的行為,不是 bug——記錄裡看到單一 ❌ 緊接
  ✅ 就是這個。
- **同名的 dimension 標籤跨維度容易抓錯**(最常見的真問題)。demo 裡每個 customer 維度
  都有 `name` / `email` / `tier` 這種同名欄位標籤;agent 在挑輸出 / 條件標籤時,可能抓到
  **別的維度**的 `trait_id` → 產 SQL 時 `relation/column does not exist`。
  - session(SQL Server)的 `null`/`in` 分群、session(Postgres)的行動資料都栽在這。
  - **緩解**:turn 明確要求「先 `list_traits`(限定這個維度)再挑」,或建立時把維度講清楚
    ——session(MySQL)就是加了這步才全過。屬 agent / 範本層,不是後端 crash。

## 各方言差異

| 方言 | 連線關鍵 | 識別字 | 備註 |
|---|---|---|---|
| **MySQL** | host / port 3306 / database | 反引號 `` ` `` | 最乾淨的一條;見 session-01。 |
| **SQL Server** | host / port 1433 / database | `[方括號]` | 見下方 findings。 |
| **Oracle** | host / port 1521 / **service name**(填在 database) | `"雙引號"`、**識別字大寫** | 表 / 欄 / schema 都要大寫(`SEGDEMO.CUSTOMERS`);compute「最近一次」agent 依 MCP 說明正確挑了 array 系的 `last_event_list`。 |
| **Postgres** | host / port 5432 / database / schema | `"雙引號"` | 一般乾淨;富一點的倉(materialization / 多 schema / 多 fact)可另跑。 |
| **Greenplum** | host / port 5432 / database | `"雙引號"` | 大倉 + 保留字 `time`,見下方 findings。 |
| **BigQuery** | **service account 金鑰**(project / dataset / keyfile);無 host/port/密碼 | 反引號 `` ` `` | 金鑰是機密,永遠只放 data_sources.yml,不進範例。 |

## 過程中挖到的真問題

### 已修

1. **eventCount 分群的相對時間值畸形 → 後端 500(不是驗證失敗)**〔SQL Server 那條首次踩到〕
   相對時間值 shape 不對時,`operator_value_validable.rb` 在 `r['from']['value']` 做了
   `Integer#[]`,丟「no implicit conversion of String into Integer」500。
   **修**:segma-backend `b275db8e` 加型別守衛——畸形值判為 invalid(false)而非 500。

2. **本機 MySQL(`db` 容器)撐不住 Greenplum 大倉的 schema refresh → 崩潰重啟**
   該倉 **2,101 張表 / 82,214 欄位**;backend 把整份 catalog 用**一條** `INSERT INTO
   db_columns` 寫回 MySQL,解析階段讓 `db`(上限 768M)mysqld malloc 失敗 abort、觸發 XA
   crash recovery(過程中的 `Can't connect to server on 'db' (115)` / `Lost connection
   (2013)` 都是這次崩潰的症狀)。**修(本機)**:`docker-compose.ernest.yml` 的 `db`
   768M → 1024M、recreate;重跑峰值 775MiB、restarts=0、82K 欄位全寫入。

### 未修(open — 追在 `worklog/OPEN_ISSUES.md`)

3. **保留字時間欄位被雙重加引號 → metric / eventCount SQL 掛掉**〔Greenplum,`time` 欄位〕
   agent 建 fact 時把 `time_column` 傳成已加引號的 `"time"`,backend **原樣存**,SQL 產生時
   方言 quoter **又加一次** → `COUNT(A."""time""")` → `column ... does not exist`。
   `fact_column` 本身存的是乾淨的 `time`,只有指定欄位 `time_column` 帶了引號 → 證明是
   **識別字參數原樣存 + 產生時再 quote 的雙重加引號**。
   **應修**:識別字類參數(`time_column`、`primary_key` 等)存原始欄名、只在產生時 quote;
   後端收到已加引號的值要正規化 / 擋(FE 擋得住、raw API/MCP 擋不住);swagger/MCP 說明
   寫明「傳原始欄名,別自己加引號」。→ **OPEN_ISSUES OI-68**。

4. **巨型單筆 INSERT 對大倉脆弱**〔上面第 2 項的 product 面〕
   把整份 catalog 用一條 INSERT 寫回,對真實客戶的大倉一樣會撐爆——本機只是把記憶體調大
   繞過,應改成分批寫入。→ **OPEN_ISSUES OI-69**。

5. **同名跨維度標籤抓錯**(見「共通觀察」)——屬 agent / 範本層,列為待改善方向。
