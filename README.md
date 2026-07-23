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

## 回歸測試

- **runtime 單元測試**:`.venv/bin/python tests/test_agent_runtime.py`(或
  `pytest tests/`)——用 pydantic-ai 的 TestModel / FunctionModel 假 model + 假工具,
  不連真 LLM / MCP,驗證串流事件、破壞性閘門的攔截 / 拒絕(不執行)/ 核准(執行)。
- **prompt 回歸 case**:app 左側的常用範本,旗艦流程在 `segma-mcp/tests/prompt_eval/`
  有對應的 case(例如 RFM 近因標籤 → `cases/trait-recency-on-demo.yaml`),餵 prompt
  給連著 MCP 的 agent,斷言最終 backend 狀態。跑法見該 harness 的 README。

## 可以再加的(邊用邊改)

- **串流時也顯示核准**:目前核准 UI 在該輪串流結束後才出現;可改成一偵測到破壞性
  呼叫就即時彈出。
- **更多 prompt 回歸 case**:把常用範本裡其他旗艦流程(分群、特徵商店、一鍵完整流程)
  也補成 `prompt_eval` case。
