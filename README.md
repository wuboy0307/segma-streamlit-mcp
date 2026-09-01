# segma-streamlit-mcp

用**對話**操作 Segma。一個 Streamlit 範例:把 [segma-mcp](https://gitlab.com/wavein/segma-mcp) 的
工具接給一個 LLM agent,使用者用自然語言就能請 agent 建立 / 查詢 Segma 的各種
resource——等同於你在 Claude 裡接 segma MCP 做的事,搬進 Streamlit。

```
使用者 ──▶ Streamlit ──▶ Pydantic AI Agent ──(MCP)──▶ segma-mcp ──▶ backend
                              │
                     OpenAI-compatible LLM
```

agent 拿到的工具,就是 segma-mcp 由 backend swagger 自動生成的那 100+ 個
operationId 工具(`create_dim` / `create_trait` / `create_segment` /
`list_*` / `search_*` …)。**每個工具的參數規則由工具自己的說明定義**,這支 app
不重抄——跟 `segma/segma-unified-composer/.../templates/streamlit` 的 chatbot
範本同樣的分工原則。

## 設計理念:主動提案,而非逐欄設定

DataSource 是入口;Dim / Fact 只是它的結構化視角,Metric / Trait 又是建在其上的
語意層。所以 agent 的工作流是**從 metadata 反推整個 CDP**,而不是要你一格一格填:

1. 你給資料來源的連線資訊(agent 缺什麼會問)。
2. agent 建好 DataSource → `refresh_data_source_schema` → `list_data_source_columns`
   讀出 table / column / 型別。
3. agent **分析 schema、主動提出**一套有意義的設計:哪些表當 Dim(實體)、哪些當
   Fact(事件)、怎麼 join,以及合理的 Metric(SUM/AVG/COUNT…)與 Trait(近因、
   分級…)。
4. 你**確認或微調**(大多時候一句「好」就開建);除非你堅持某個東西一定要怎樣,
   自己詳述,否則不用碰瑣碎欄位。
5. 確認後 agent 依依賴順序把整組建起來。

`SYSTEM_PROMPT`(在 `streamlit_app.py`)就是這套工作流的來源——想調 agent 的行為
改那裡即可。

## 常用 prompt 範本

不知道怎麼開口?app 左側的 **📋 常用範本** 面板依「連接資料 / 分析主體 / 事件 /
指標 / 標籤 / 分群 / 行動資料 / 同步目的地 / 同步 / 特徵商店 / 查詢探索 / 一鍵完整
流程」分類——從接資料一路到把名單匯出、即時查詢,涵蓋整個 CDP 流程。點一下就把範本帶進輸入框,把
`< >` 佔位符換成你的資料再送出。完整清單 + 怎麼改的說明見 **[PROMPTS.md](PROMPTS.md)**;
範本本身定義在 `streamlit_app.py` 的 `PROMPT_TEMPLATES`(要增修範本改那裡,
會自動出現在面板上)。想看完整一段流程長怎樣,見 **[examples/](examples/)** 的對話記錄。

## 跑起來(本機)

```bash
cd segma-streamlit-mcp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 三組設定:MCP 端點 / Segma token / LLM。最簡單:複製範例填好即可
# (.env 已被 gitignore,不會進 git);缺的欄位一樣會在畫面上問。
cp .env.example .env      # 然後編輯 .env 填入 LLM_API_KEY…

# 或改用 export(等價):
# export SEGMA_MCP_URL=https://localhost:1443/mcp   # dev 本機 stack
# export LLM_API_KEY=sk-...                          # OpenAI 相容
# export LLM_MODEL=gpt-4o                            # 預設 gpt-4o

.venv/bin/streamlit run streamlit_app.py
```

### 要加或改相依套件

`requirements.txt` 是**產物**,不要手改。改 `requirements.in`,然後重新產生:

```bash
uv pip compile $(sed -n 's/^# uv-compile-args: *//p' requirements.in) \
    --output-file requirements.txt requirements.in
```

在 Claude Code 裡不用自己跑 —— 有 hook 會在你存檔 `requirements.in` 的當下自動重跑
(`scripts/relock-python-requirements.sh`,裝在 workspace 的 `.claude/settings.json`)。
用別的編輯器就要自己跑那行。

兩個檔案一起 commit。CI 會自己重跑一次並比對,不一致就紅 —— 所以忘了重跑會在
build 上看到,而不是變成「開發機和線上跑不同的程式碼」。

uv 的版本和 compile 參數都只寫在 `requirements.in` 檔頭的 `# uv-version:` /
`# uv-compile-args:` 兩行,hook 和 CI 都讀那裡,所以只有一個地方有值。比對是逐位元組
的,uv 版本或參數不同都可能重排一行或改寫檔頭,會讓一個正確的改動變紅。

在 repo 目錄下重跑時,現有的 `requirements.txt` 會被當成既有的釘選沿用 —— 這正是
要的行為:只重解你改動到的部分,其他 42 個套件留在原地。要整批升級是另一件事
(`-U`),獨立一個 commit、自己跑一次測試。

為什麼是 lock 而不是手動釘:`requirements.in` 只寫了 5 個套件,乾淨安裝會裝進
47 個。2026-09-01 那次線上全掛,壞的就是另外 42 個裡的一個 —— 它在某天早上跳了
一個 major,而沒有任何檔案說得上話。

lock 帶 `--generate-hashes`,所以每次安裝都會驗檔案內容;任何一個套件被換掉,
安裝會直接失敗而不是安靜地裝進去。這也表示**手改 lock 裡的版號一定會壞** ——
hash 不會跟著改。一律走 `.in` + 重新產生。

開啟後在左側貼上 **Bearer token**(見下方),就能開始對話。例如:

> 接上我的 postgres 倉庫(host … / database … / schema …),看一下 schema
> 提議要建哪些 dim、fact、metric、trait。

agent 會讀 metadata 後列出一套提案,你回「好」它就整組建起來。也可以只做單一件事:

> 幫我建一個匯出 CSV 的 destination,名稱「行銷名單」,含表頭、逗號分隔。

## 拿 Segma token

同一顆 JWT 就是 `/api/v1` 與 `/mcp` 共用的:

```bash
curl -sk -X POST https://localhost:1443/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"account":"root","password":"<你的密碼>"}'
# → {"token":"eyJ..."}
```

把 `token` 貼進左側欄位。TTL 到期(會出現「簽名已過期」)就重新 login 換一顆。

## 設定一覽

| 來源 | 變數 | 說明 |
|---|---|---|
| env | `SEGMA_MCP_URL` | segma-mcp 的 `/mcp` 端點。沒設會嘗試由 `SEGMA_ACCESS_URL` 推導 `<url>/mcp`,再不行就畫面輸入 |
| env | `SEGMA_MCP_VERIFY_TLS` | 預設 `false`(dev/test stack 是 self-signed)。正式憑證設 `true` |
| env | `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` | OpenAI 相容 LLM;缺的畫面補。model 預設 `gpt-4o` |
| env | `SEGMA_EAGER_TOOLS` | 哪些工具一開始就送完整 schema(逗號分隔)。預設用 `agent_runtime.EAGER_TOOLS`;設 `none` 表示全部延後。見[工具定義的送出成本](#工具定義的送出成本) |
| URL | `?token=<JWT>` | 部署在 Segma frontend 內時自動帶入(見下) |

## 部署進 Segma(iframe 模式)

放進 Segma 的 Streamlit 容器跑時,token 由外層 frontend 以 `?token=` 帶入,並靠
`segma_bridge` 在每次 rerun 保鮮(避免原本嵌入的 token 過期後 401)。這支 app 會
自動偵測:`segma_bridge` import 得到就用它,否則退回純 `?token=` 讀取。
`SEGMA_ACCESS_URL` 這個 placeholder 也會在部署時被替換成實際 host。詳見
`segma-unified-composer/docker/streamlit/segma_bridge/`。

## 即時工具進度(串流)

agent 建整套 CDP 常一口氣呼叫十幾個工具;為了不讓你對著 spinner 乾等,這支 app 用
pydantic-ai 的 `agent.iter` **邊跑邊顯示**:每個工具 ⏳ 開始 → ✅ 完成即時更新,
助手的文字也逐字串流出來。跑完那串進度會收合成一個可展開的「🔧 工具呼叫」紀錄。
串流邏輯在 `agent_runtime.stream_turn`(純邏輯、可單元測試)。

## 破壞性動作要你確認

刪除 / 觸發同步 / 觸發行動資料 / 重建容器這類**會真的動到資料**的動作,有兩層保護:

1. **軟性**:system prompt 要 agent 先口頭問你。
2. **硬性**:就算 agent 真的呼叫了 `delete_*` / `trigger_*` / `batch_destroy_*` /
   `*_recreate_*`,也會被 `ApprovalRequiredToolset` 攔在**執行之前**,畫面跳出核准
   UI(每個動作一個勾選框 + 執行 / 全部拒絕);你不按,它就不會跑。左側「🛡️ 破壞性
   動作先問我」開關可關掉這層(仍保留第 1 層)。哪些工具算破壞性見
   `agent_runtime.DESTRUCTIVE_PREFIXES`。

## 工具定義的送出成本

segma-mcp 有 152 個工具。工具定義是**每一輪都重送**的,而這裡走 OpenAI API,是真的
計費的那條路(本機 Claude Code 走 Tool Search,不付這筆)。

### 先講最重要的:超過 128 個工具,OpenAI 直接回 400

```
400 invalid_request_error — Invalid 'tools': array too long.
Expected an array with maximum length 128, but got an array with length 152 instead.
```

**所以在延後載入之前,這個 app 的每一個 request 都是失敗的**(不是變慢,是完全不能用)。
`examples/` 最後一次真實產生是 2026-07-27,那時工具數還在上限內;`b4a7ec1d`
(2026-08-05)加上 14 個 `batch_destroy_*` 之後是 152,而在那之前就已經是 138。
也就是這條路在 2026-07-27 到 08-05 之間某個時點壞掉,沒有人發現。

這也是為什麼 eager 清單**不只是成本設定,而是功能的一部分**:清單長度加上
`search_tools` 必須留在 128 以內。有測試守著(`test_eager_set_stays_under_the_openai_tool_cap`)。

### 實測

攔真正的 HTTP request body 量 `tools[]`(不是估的):

| 設定 | 送出工具數 | tokens / 每一輪 |
|---|---|---|
| 全部送(改之前) | 152 | 50,464 —— **但 OpenAI 直接 400** |
| `EAGER_TOOLS` 內建清單(預設) | 35 | **27,262(−46.0%,每輪省 23,202)** |
| `SEGMA_EAGER_TOOLS=none` | 1 | 187(−99.6%) |

(35 = eager 34 個加上一個 `search_tools`。)

再用**真的 gpt-4o** 跑同一個任務(建一個事件彙總標籤 + 一個用它的分群,查資料庫確認
真的建出來),每個設定 3 次:

| 設定 | 完成 | requests | 工具呼叫 | search | input tokens | 成本 |
|---|---|---|---|---|---|---|
| 全部送 | **0/3** | — | — | — | — | 400,連一次都沒送出去 |
| `EAGER_TOOLS`(預設) | **3/3** | 4–5 | 5 | 0 | 101k–126k | **$0.26–0.32** |
| 不含 `create_*` | 3/3 | 6–9 | 7–9 | 1 | 127k–154k | $0.32–0.39 |
| `none` | **0/3** | 5–6 | 8–10 | 2 | 48k–62k | 便宜但沒做完 |

**結論:預設的 eager 清單就是最好的,兩種更激進的設定都實測比它差。**

- **不含 `create_*`**:3/3 會做完,但每次都多一輪搜尋、多 1–4 個 request、token 多約
  25%。省下的 schema 抵不過多出來的往返 —— 不要延後 `create_*`。
- **`none`**:0/3。agent 跳過 name→id 的查詢就直接呼叫 create,參數是猜的,後端回 500
  (見 OPEN_ISSUES OI-142),它最後跑來問使用者要資料庫連線密碼。便宜是因為沒做完。

**這是延後,不是過濾。** 沒被列進 eager 的工具標上 `defer_loading=True`,schema 不隨
每一輪送出,但 agent 仍然可以透過 `search_tools` 自己撈進來——能力一個都沒少。所以
清單寫漏了不會壞掉,只是多花一輪去搜。也因此「不在清單裡」是預設:server 之後新增的
工具會自動落在延後那邊,而不是自動變成每輪都送。

清單怎麼來的:對 `PROMPTS.md` + `streamlit_app.py` 的提示詞、`segma-mcp/demos/
build-workflow.md`、以及 `examples/` 底下所有實錄 transcript 取聯集,也就是「文件明確
叫 agent 用的」加上「真的被用過的」。要調整改 `agent_runtime.EAGER_TOOLS`。

**只算正面提及。** 第一版是「提示詞裡出現過工具名」就收進來,於是收進了 `get_profile`
—— 它在提示詞裡唯一的出現是被禁止的(「別用 `get_profile` 之類的工具亂湊」),六次真實
gpt-4o 跑測中被呼叫 0 次,卻每輪都在付它的 schema。已移除,有測試守著
(`test_eager_list_holds_nothing_the_system_prompt_forbids`)。

延後**不會**繞過破壞性動作的確認閘門:延後只影響 schema 何時送,呼叫時仍然經過
`ApprovalRequiredToolset`(有測試守著)。

`search_tools` 本身是有效的(不含 `create_*` 那組 3/3 都靠它找到 `create_trait` /
`create_segment` 並成功建出來),所以延後不是「找不到」的問題;`none` 的失敗是 agent
連 name→id 的查詢都跳過。要再往下砍之前,先解決的是**提示詞要求它先查再建**,而不是
工具清單。

## 回歸測試

- **runtime 單元測試**:`.venv/bin/python tests/test_agent_runtime.py`(或
  `pytest tests/`)——用 pydantic-ai 的 TestModel / FunctionModel 假 model + 假工具,
  不連真 LLM / MCP,驗證串流事件、破壞性閘門的攔截 / 拒絕(不執行)/ 核准(執行),
  以及工具定義延後載入——其中 `test_deferred_schemas_never_reach_the_wire` 用
  MockTransport 攔真正的 request body,確認延後的 schema 真的沒被送出去(第一版斷言
  在序列化**之前**的清單,兩種情況都一樣,會誤報成功);另有一條確認延後的破壞性工具
  仍然被閘門攔住。
- **prompt 回歸 case**:app 左側的常用範本,旗艦流程在 `segma-mcp/tests/prompt_eval/`
  有對應的 case(例如 RFM 近因標籤 → `cases/trait-recency-on-demo.yaml`),餵 prompt
  給連著 MCP 的 agent,斷言最終 backend 狀態。跑法見該 harness 的 README。

## 可以再加的(邊用邊改)

- **串流時也顯示核准**:目前核准 UI 在該輪串流結束後才出現;可改成一偵測到破壞性
  呼叫就即時彈出。
- **更多 prompt 回歸 case**:把常用範本裡其他旗艦流程(分群、特徵商店、一鍵完整流程)
  也補成 `prompt_eval` case。
