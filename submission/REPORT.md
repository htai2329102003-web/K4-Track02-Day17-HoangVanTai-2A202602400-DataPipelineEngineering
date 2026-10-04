# K4-Track02-Day17 — Report cá nhân

Phần phân tích tối đa một trang, không tính output ở phần 5.
Định dạng tham chiếu và phạm vi tính trang: [SUBMISSION.md](../docs/SUBMISSION.md).

**Họ tên / MSSV:** Hoàng Văn Tài / 2A202602400

**Repo:** https://github.com/htai2329102003-web/K4-Track02-Day17-HoangVanTai-2A202602400-DataPipelineEngineering

**Commit bài nộp:** a046f6a

**AI đã dùng và phạm vi hỗ trợ (hoặc không dùng):** Codex hỗ trợ đọc đề, sửa code và viết nháp.

**Nguồn tham khảo khác (nếu có):**


## 1. Ba lỗi

| | Lỗi Silver | Lỗi late data | Lỗi xoá (CDC) |
|---|---|---|---|
| **Triệu chứng** | Check một hàng/`ticket_id` fail; T-91 không ở trạng thái mới nhất. | Check đối chiếu full recompute fail; feature u05 ngày 08-12 thiếu event muộn. | T-97 còn sau delete; các check tombstone, training và RAG fail. |
| **Nguyên nhân gốc** | Append thay vì upsert theo khóa; không chặn LSN cũ ghi đè. | `LOOKBACK_DAYS=0` không tính lại ngày event-time; P99 Bronze là 3 ngày. | Delete có `after=null`, nhưng staging chỉ tìm ID trong `after`, bỏ qua `before`/Kafka key. |
| **Cách sửa** | `pipeline/silver.py:64-93`: MERGE theo `ticket_id`, chỉ update khi LSN mới hơn. | `pipeline/config.py:28`: đặt `LOOKBACK_DAYS=3`; logic có sẵn trong `gold.py` dùng giá trị này để xoá và tính lại các partition event-time trong cửa sổ. | `pipeline/staging.py:40-42`: lấy ID theo `after` → `before` → key; MERGE ghi delete thành tombstone với PII null. |
| **Khái niệm** | Khóa, idempotency, CDC ordering. | Event time, lateness P99, lookback. | Debezium envelope, tombstone, “xóa phải lan”. |

## 2. Các con số

- P99 lateness đo từ Bronze: `3.00` ngày → `LOOKBACK_DAYS = 3`
- `submission/checksums.txt`: PASS — Gold checksum: `39e115c510ecdf526800eac227158a4f`
- `make parity`: PARITY

## 3. Lựa chọn công cụ / kỹ thuật (mỗi dòng một câu "vì sao")

- MERGE giữ một trạng thái ticket theo khóa/LSN; overwrite-partition tính lại đúng cửa sổ event-time có late data.
- Tombstone lưu dấu delete, chống replay làm sống lại dữ liệu và cho phép truyền xoá xuống downstream.
- Snapshot "as of" giữ point-in-time và tái lập được tập huấn luyện lịch sử mà không sửa phiên bản cũ.
- DuckDB phù hợp seed nhỏ, chạy cục bộ; dbt kiểm chứng cùng logic SQL và parity mà không cần Spark/cluster.

## 4. Hai câu hỏi suy ngẫm

1. Snapshot `v2026-08-12`..`v2026-08-14` vẫn chứa văn bản của T-97 (đã bị xoá ngày
   08-15). "Snapshot bất biến" và "quyền được xoá dữ liệu" mâu thuẫn — bạn xử lý thế nào?

   Chặn phục vụ/huấn luyện từ phiên bản bị ảnh hưởng; xác định snapshot, cache và backup
   liên quan; xoá hoặc tái tạo artifact chứa PII và ghi audit. Tính bất biến không miễn trừ
   yêu cầu xoá hợp lệ.
2. Regex che được email và số điện thoại, nhưng tên "Nguyễn Văn An" vẫn còn. Bạn sẽ
   đặt chốt PII nào, ở tầng nào, và đo nó ra sao?

   Phát hiện tên, địa chỉ và mã định danh trước Silver; quarantine trường hợp độ tin cậy
   thấp. Đo precision/recall và tỷ lệ bỏ sót theo loại trên bộ mẫu tiếng Việt gán nhãn;
   không ghi PII nguyên văn vào log.

## 5. Output (dán nguyên văn)

PowerShell trên Windows; các lệnh và kết quả dưới đây được chạy trong repo hiện tại.

```text
PS> .\.venv\Scripts\python.exe -m scripts.verify
=== verify.py — Day 17 pipeline contracts ===
  [OK ] Bronze  every daily batch landed as Parquet (7 days x 3 sources)
  [OK ] Bronze  re-landing a batch is a no-op (append-only, no duplicate file)
  [OK ] Bronze  Bronze keeps the raw truth: Kafka tombstone + redelivered events are still there
  [OK ] Silver  silver_tickets has exactly one row per ticket_id
  [OK ] Silver  T-91 shows its latest state: high / closed / bug
  [OK ] Silver  deleted ticket T-97 is a tombstone: is_deleted and no personal data left
  [OK ] Silver  no email / phone number survives past Bronze
  [OK ] Silver  silver_events has one row per event_id (Kafka redeliveries removed)
  [OK ] Silver  2 malformed events quarantined with a reason; the run did not halt
  [OK ] Gold    gold_feature_daily reconciles with a full recompute from Silver
  [OK ] Gold    u05's offline events of 08-12 (arrived 08-15) are counted on 08-12
  [OK ] Gold    LOOKBACK_DAYS covers measured P99 lateness (p99=3.00 days)
  [OK ] Gold    training set uses point-in-time priority (T-91 created as 'low')
  [OK ] Gold    late feedback creates a NEW snapshot version; the old one is untouched
  [OK ] Gold    latest training snapshot excludes the deleted ticket T-97
  [OK ] Gold    deletes propagate to the RAG index: no chunk of T-97
  [OK ] Gold    gold_doc_chunks: one row per chunk, and a re-run embeds 0 new chunks
  [OK ] Rerun   re-run 2026-08-12 three times -> Gold checksum identical to a fresh build

RESULT: 18/18 checks — ALL PASS
re-run checksums written to submission/checksums.txt

PS> .\.venv\Scripts\python.exe -m pytest
..................................                                       [100%]
34 passed in 3.96s

PS> .\.venv\Scripts\python.exe -m scripts.rerun_check
# Lab 17 — re-run check for 2026-08-12

run                     gold_feature_daily    gold_training_set     gold_doc_chunks       gold (combined)
fresh build             8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f
re-run #1 of 2026-08-12 8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f
re-run #2 of 2026-08-12 8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f
re-run #3 of 2026-08-12 8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f

RESULT: PASS — 3 re-runs, identical checksums

PS> .\.venv\Scripts\python.exe main.py --lateness
event lateness over 43 Bronze records (calendar days): p50=0.00 p95=2.90 p99=3.00 max=3
-> lookback must be >= ceil(p99) = 3 day(s); config.LOOKBACK_DAYS = 3

PS> .\.venv\Scripts\python.exe main.py --land-only
2026-08-10  tickets:already-landed(5)  events:already-landed(6)  transcripts:already-landed(1)
2026-08-11  tickets:already-landed(3)  events:already-landed(5)  transcripts:already-landed(2)
2026-08-12  tickets:already-landed(5)  events:already-landed(6)  transcripts:already-landed(1)
2026-08-13  tickets:already-landed(3)  events:already-landed(7)  transcripts:already-landed(1)
2026-08-14  tickets:already-landed(4)  events:already-landed(4)  transcripts:already-landed(1)
2026-08-15  tickets:already-landed(4)  events:already-landed(8)  transcripts:already-landed(1)
2026-08-16  tickets:already-landed(4)  events:already-landed(7)  transcripts:already-landed(2)
PS> $env:DO_NOT_TRACK = '1'
PS> Push-Location dbt_project
PS> ..\.venv\Scripts\dbt.exe build --profiles-dir . --event-time-start 2026-08-10 --event-time-end 2026-08-17
[Trích phần tổng kết của output dbt; các dòng chạy từng model/test được lược bớt]
Completed successfully
Done. PASS=19 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=19
PS> Pop-Location

PS> .\.venv\Scripts\python.exe -m scripts.parity
=== parity: lite pipeline vs dbt ===
  [OK ] silver_tickets       lite 3c15dfd43701  dbt 3c15dfd43701
  [OK ] gold_feature_daily   lite 8630e04a61d1  dbt 8630e04a61d1
RESULT: PARITY — both implementations agree
```

Bonus output and evidence:

```text
PS> .\.venv\Scripts\python.exe -m scripts.bonus_llm
=== bonus: LLM labelling of 11 live tickets ===
  cost estimate before running: ~506 tokens = $0.0010 per full run
  [OK ] first run labels every live ticket
  [OK ] cost estimate matches FakeLLM counted tokens
  [OK ] re-run with same model + prompt makes 0 LLM calls
  [OK ] every Gold label is bug / billing / other
  [OK ] off-schema answers go to llm_label_quarantine
  [OK ] new prompt version re-labels on purpose
  [OK ] labels carry their prompt version
BONUS PASS

B2 brainstorm evidence: bonus/DESIGN.md
```
