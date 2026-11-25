# ECSR 案件摘要小幫手

匯入每月的 Excel（`ECSR` 頁籤）後，自動在 **Customer Service Report Information System** 逐筆查詢案件，
照人工作業的流程檢查並摘要，最後把結果寫回一份新的 Excel。

## 它會做什麼

對 `ECSR` 頁籤「CSR NO.」欄的每一筆案號：

1. 在 CSR 查詢畫面的 **CSR Ref No** 填入案號（例如 `ECSR / TPEWS / 26008261`）→ **Search** → 點開案件
2. 切到 **Related Station**：檢查是否出現 `TPEWT`（三個區塊都算，並記錄是在哪一區）
3. 若有 → 切到 **Outline**，讀取 Outline & Causes 並產生「案件摘要」
4. 切到 **IR/CPN**：找出 *Investigate & Report Dept.* 含 `TPEWT` 的 **最新一筆 IR**，點開後讀取
   **Investigation Result**，產生「最新處理狀態摘要」
5. 另外從 IR 清單推定回覆狀態：該筆 IR 之後 `TPEWT` 是否已建立回覆 IR（已回覆 / 待回覆）

> 步驟 4 不論 Related Station 有沒有 TPEWT 都會檢查。目標單位 `TPEWT` 可在「設定」改成其他代碼。

**本工具只讀取畫面**：只會點頁籤、Search、案號連結與 IR 列，不會點 Del、Forward、Comment、CPN、Read 等會異動資料的按鈕。

## 安裝

到 GitHub 專案的 **Actions → Build Windows app → 最新一次成功的執行 → Artifacts** 下載 `ECSRTool-windows`，
解壓縮後執行 `ECSRTool.exe`（不需安裝 Python；使用電腦上已安裝的 Microsoft Edge）。

想自己從原始碼建置：安裝 Python 3.10 以上後執行 `build.bat`。

## 使用步驟

1. **① 匯入 Excel**：選擇含 `ECSR` 頁籤的檔案（原始檔不會被修改）。
2. **② 開啟瀏覽器**：工具會開一個專用的 Edge 視窗。第一次請在裡面登入系統，
   並停在看得到 **CSR Ref No** 欄位的查詢畫面（登入狀態會保留，之後通常不用重登）。
3. **③ 開始**：工具會先確認畫面正確，再逐筆處理。過程中可「暫停 / 繼續 / 停止」，
   也可以用「範圍」只跑其中幾筆（建議第一次先跑 1～3 筆確認結果）。

每完成一筆就會存檔，中途關掉也沒關係；下次匯入同一個檔案，勾選「只處理未完成的」即可接續。
執行中請不要操作那個 Edge 視窗（可以縮小，自己照常使用其他視窗）。

## 輸出

與原始檔同資料夾：

| 檔案 | 內容 |
|---|---|
| `原檔名_摘要結果.xlsx` | 原檔的複本，`ECSR` 頁籤右側多出：處理結果、Related Station 有無 TPEWT、案件摘要、最新 IR、回覆狀態、最新處理狀態摘要、備註、處理時間；另有 `ECSR原文` 頁籤保存 Outline 與 IR 全文 |
| `原檔名_摘要進度.json` | 續跑用的進度檔 |

處理結果的意思：`完成`、`查無案件`（查詢結果沒有這個案號）、`略過`（案號格式不完整，例如被截斷的
`B7ECSR/TPEWS/260118`）、`錯誤`（逾時或畫面異常，下次會自動重跑；連續 3 筆錯誤會自動停止）。

## 摘要方式與資料安全

- **預設（未啟用 AI）**：所有資料只留在本機，摘要欄是原文開頭的節錄（標示〔原文節錄〕），全文在 `ECSR原文` 頁籤。
- **啟用 AI 摘要**（設定 → 勾選並填入 Anthropic API 金鑰）：由 Claude 產生繁體中文重點摘要。
  **啟用後 Outline 與 IR 內容（含旅客資料）會傳送到 Anthropic API**，請先確認符合公司資安與個資規定。
  若公司網路擋住 `api.anthropic.com` 或金鑰有誤，會自動退回原文節錄，不影響流程。
  金鑰以明碼存放在 `%APPDATA%\ECSRTool\config.json`；也可以不填，改設環境變數 `ANTHROPIC_API_KEY`。

## 疑難排解

| 狀況 | 處理 |
|---|---|
| 按「開始」說不在 CSR 查詢畫面 | 在工具開的 Edge 裡登入，切到有 **CSR Ref No** 欄位的畫面再按一次 |
| 專用 Edge 視窗無法登入（SSO / 公司政策） | 關閉所有 Edge → 執行 `start_edge_debug.bat` → 在該 Edge 登入 → 設定裡「連接已開啟的 Edge」填 `http://127.0.0.1:9222` |
| 常出現逾時 | 設定裡把「每步等待上限」調大（預設 40 秒） |
| 結果檔寫不進去 | 先關閉 Excel 中開啟的結果檔，再按「重新輸出結果」 |
| 系統網址不同 | 設定裡修改「系統網址」；實際查詢頁以按下「開始」當下的畫面為準 |

## 已知限制

- 程式是依操作錄影的畫面寫的，並用仿製的模擬網站測過（`tests/`），**尚未在真實系統上驗證**。
  為了降低對網頁結構的依賴，定位方式一律用畫面上的文字（頁籤名稱、欄位標題、案號），
  但若實際畫面與錄影有出入，仍可能需要微調——請先用少量案件試跑，並把「執行紀錄」的訊息回報。
- 「回覆狀態」是由 IR 清單的建立單位與時間推定，並非系統欄位，僅供參考。

## 開發

```bash
pip install -r requirements-dev.txt
python -m pytest tests            # 以模擬網站跑完整流程
python main.py                    # 視窗模式
python main.py 202611.xlsx --limit 3   # 命令列模式
```
