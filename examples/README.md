# 對話記錄範例 / Example sessions

用 Segma MCP Chat 建立 / 啟用 CDP 的**真實對話記錄**,給想了解「一段完整流程長怎樣」
的使用者參考。想直接開始的話,用 app 左側的**常用範本**(說明見
[../PROMPTS.md](../PROMPTS.md))。

每份記錄的對話都**取自那些常用範本**(`< >` 佔位符填入真實資料),再驅動 app 同一套
Agent 真跑一遍——所以這些記錄同時是**範本的驗證**:範本有缺漏、或撞到 MCP / 後端 bug,
會直接在記錄裡現形(見各檔文末的 findings)。

| 檔案 | 內容 |
|---|---|
| [session-01-build.md](session-01-build.md) | **從零建一整條 CDP**(連接 → 分析主體 → 事件 → 指標 → 標籤含 derive → 分群 → 行動資料)。以 MySQL 當例子;其他方言差異見 dialect-notes。 |
| [session-02-activation.md](session-02-activation.md) | **啟用流程**:同步目的地 → 同步 → 特徵商店 → 即時查詢。文末記錄過程中發現的 4 個缺漏 + 修法。 |
| [session-03-traits.md](session-03-traits.md) | **標籤(Trait)四種算法**:aggregation / compute / derive / sql,逐一在真實資料上建成。 |
| [session-04-segments.md](session-04-segments.md) | **分群(Segment)**:指標門檻、eventCount 相對時間、eventAggr,再列出各群人數 / 拉名單。 |
| [dialect-notes.md](dialect-notes.md) | **各方言差異與已知問題**:SQL Server / Oracle / Postgres / Greenplum / BigQuery 換方言要注意什麼,+ 過程中挖到的真 bug(已修 / open)。 |

> session-02 / 03 建在既有的 `mcpdemo_` 信用卡 demo 上(分析主體『mcpdemo_持卡人』+
> 事件『mcpdemo_信用卡交易』);session-01 從空環境連一個新倉開始;session-04 建在 demo
> 的指標之上。

## 這些記錄怎麼產生的

用 [`../tools/gen_transcript.py`](../tools/gen_transcript.py) 產:一個 headless driver,
**重用 app 同一套 `SYSTEM_PROMPT` + Agent**(gpt-4o → Pydantic AI → segma MCP)。新版的
turns 檔**直接指名要用哪個範本 + 填哪些值**,driver 再從 `streamlit_app.py` 的
`PROMPT_TEMPLATES` 把範本原文抓出來、填好、真跑——所以記錄永遠不會跟範本脫節,而且**少
一個範本就會報錯**,逼你先把它補進 `PROMPT_TEMPLATES`。

> **格式**:`session-02/03/04` 都已用這種「指名範本」的新格式(見下)並重跑驗證過。
> 只剩 `session-01-build` 的 turns 還是舊的 `["標籤", "整段 prompt"]`(當初照範本手填、
> 已驗證)——它要連一個真實資料倉,改成新格式得再跑一次 live warehouse,列為後續。
> driver 兩種格式都吃。

新版 turns 檔(`../tools/turns/*.json`)每一輪長這樣:

```json
{ "template": ["🎯 分群(篩出一群對象)", "高價值(指標超過門檻)"],
  "fill": { "『高價值客戶』": "『s_高價值客戶』",
            "『總消費金額』": "『mcpdemo_購買金額加總』",
            "<10000>": "10000" } }
```

要自己跑(連線 / 敏感值從 gitignored 的 `segma-backend/spec/e2e/config/data_sources.yml`
拼回真實資訊):

```bash
cp .env.example .env      # 填 LLM_API_KEY
export SEGMA_MCP_URL=https://localhost:1443/mcp
export SEGMA_MCP_TOKEN=<POST /api/auth/login 拿到的 JWT>   # 或留給 ~/.claude.json 帶入
.venv/bin/python tools/gen_transcript.py \
    --turns tools/turns/session-04-segments.json \
    --title "Segments from templates (分群)" \
    --base "既有的 mcpdemo_ 信用卡 CDP" \
    --out examples/session-04-segments.md
```

> 敏感值(密碼 / 內網 IP / service-account 金鑰)只存在 `data_sources.yml`,**永遠不進
> 這些記錄**——記錄裡一律是 `<佔位符>`。
