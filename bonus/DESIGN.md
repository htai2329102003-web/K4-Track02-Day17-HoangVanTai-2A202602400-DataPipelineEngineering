# B2 — Brainstorm pipeline phát hiện giao dịch thẻ bất thường

## Bài toán và giả định MVP

Thiết kế này xem xét pipeline dữ liệu cho dịch vụ phát hiện giao dịch thẻ bất
thường tại Việt Nam. Người dùng là nhóm vận hành rủi ro và hệ thống quyết định
giao dịch; mục tiêu ban đầu là xếp hạng giao dịch cần kiểm tra, không tự động
khẳng định khách hàng gian lận. Giao dịch bị chặn nhầm có thể làm khách hàng
mất khả năng thanh toán, còn gian lận lọt qua gây thiệt hại tài chính. Vì vậy
cần cân bằng độ trễ, cảnh báo sai, khả năng giải thích và quyền riêng tư.

MVP giả định một tổ chức phát hành thẻ, một nguồn giao dịch trực tuyến và lịch
sử tối đa 13 tháng. Dữ liệu gồm thời điểm giao dịch, số tiền, loại tiền, mã
đơn vị chấp nhận thẻ, kênh, token thẻ giả danh và kết quả xác minh sau đó.
Không lưu số thẻ đầy đủ hay CVV. Nhãn gian lận đến trễ: thường chỉ có sau khi
khách hàng khiếu nại hoặc điều tra được xác nhận. Đây là giả định cần xác nhận
với nhóm nghiệp vụ và pháp chế trước khi mở rộng.

## Sáu câu hỏi thiết kế

### 1. Nguồn nào là sự thật cho giao dịch, và làm sao xử lý bản ghi đến trễ hoặc trùng?

Tôi sẽ nhận sự kiện giao dịch cùng cập nhật trạng thái từ hệ thống thanh toán;
mỗi sự kiện có `transaction_id`, `event_id`, thời điểm nghiệp vụ, `ingested_at`
và sequence/version của nguồn. Bronze lưu bản ghi gốc bất biến theo lô/ngày để
có thể phát lại. Silver khử trùng theo `event_id`, rồi chọn phiên bản giao dịch
mới nhất theo sequence của nguồn, không theo thời điểm máy nhận. Đánh đổi là
tốn dung lượng và cần quản lý phiên bản, đổi lại có thể giải thích giao dịch
được chấm bằng trạng thái nào. Tôi sẽ đo độ trễ thực tế trước khi đặt cửa sổ
xử lý dữ liệu muộn thay vì chọn lookback theo cảm tính.

### 2. Điểm gian lận phải có trước khi giao dịch được duyệt bao lâu?

Nếu mục tiêu là hỗ trợ quyết định tức thời, tôi chọn luồng streaming cho đặc
trưng ngắn hạn như số lần thử trong vài phút và giao dịch bất thường so với
thiết bị vừa dùng; xử lý batch cho đặc trưng lịch sử, báo cáo và huấn luyện.
Phương án lai tốn vận hành hơn batch-only, nhưng batch hằng đêm không giúp
quyết định giao dịch đang chờ. Tôi sẽ không đưa mọi phép tính vào streaming
ngay: chỉ làm phần có ngân sách độ trễ và hành động nghiệp vụ rõ ràng, phần
còn lại tính trước trong feature table.

### 3. Dữ liệu nào được phép vào feature, và bản ghi sai đi đâu?

Trước khi cập nhật feature, hợp đồng kiểm tra ID sự kiện, số tiền không âm,
đơn vị tiền hợp lệ, timestamp, trạng thái và schema version. Bản ghi thiếu
định danh hoặc sai kiểu được quarantine kèm mã lỗi và metadata nguồn; không tự
thay giá trị thiếu bằng 0 vì lỗi ingestion có thể bị biến thành tín hiệu gian
lận giả. Cảnh báo kích hoạt khi tỷ lệ quarantine tăng vượt ngưỡng nền đã đo,
kèm dashboard theo nguồn và phiên bản schema. Cách này làm giảm số dòng có
thể chấm trong lúc upstream hỏng, nhưng an toàn hơn âm thầm đưa dữ liệu không
đáng tin vào mô hình.

### 4. Làm sao giữ feature lúc huấn luyện giống feature lúc chấm điểm?

Tôi sẽ định nghĩa feature một lần, gồm quy tắc chuẩn hóa, cửa sổ thời gian và
cách xử lý thiếu dữ liệu, rồi dùng cùng hợp đồng cho batch training và online
serving. Mỗi giá trị lưu `event_time` và `available_at`; không dùng kết quả
khiếu nại hoặc nhãn điều tra phát sinh sau giao dịch để tạo feature cho chính
giao dịch đó. Tập huấn luyện được dựng point-in-time “as of” thời điểm giao
dịch. Đánh đổi là truy vấn point-in-time và lưu lịch sử phức tạp hơn snapshot
mới nhất, nhưng snapshot mới nhất có thể rò rỉ tương lai và khiến điểm offline
cao giả tạo. Tôi sẽ so sánh phân phối feature offline/online và chạy replay
trên giao dịch đã có kết quả.

### 5. Dữ liệu cá nhân và các nhóm khách hàng được bảo vệ thế nào?

Token thẻ giả danh được dùng thay số thẻ; bảng nối token với định danh nằm ở
hệ thống có quyền riêng, không đưa vào lakehouse phân tích. Cần giới hạn
quyền theo vai trò, mã hóa, thời hạn lưu, audit truy vấn và quy trình xử lý
yêu cầu xóa phù hợp chính sách áp dụng. Thiết kế phải kiểm tra false positive
theo kênh và nhóm khách hàng được phép đo lường, nhưng không thu thập thuộc
tính nhạy cảm chỉ để tiện phân nhóm. Đánh đổi là giảm khả năng truy vết tùy
tiện; bù lại giảm rủi ro lộ dữ liệu và buộc đội vận hành đưa ra lý do cảnh báo
từ tín hiệu giao dịch phù hợp. Thời hạn lưu cụ thể cần được chủ sở hữu dữ liệu
và pháp chế xác nhận.

### 6. Khi nào phản hồi điều tra trở thành dữ liệu huấn luyện?

Một cảnh báo được nhân viên đóng không đồng nghĩa giao dịch chắc chắn hợp lệ;
khách hàng không khiếu nại cũng không chứng minh giao dịch là thật. Tôi sẽ
giữ trạng thái riêng (chưa rõ, đang điều tra, xác nhận gian lận, xác nhận hợp
lệ), lưu thời điểm và nguồn xác nhận, rồi chỉ dùng nhãn đủ tin cậy cho tập
huấn luyện. Mẫu đánh giá được đóng băng theo thời gian để tránh giao dịch trùng
hoặc nhãn tương lai lọt vào train. Đánh đổi là có ít nhãn sạch và nhãn đến
chậm hơn, nhưng giảm tự đầu độc mô hình bằng dự đoán cũ của chính nó. Chỉ sau
khi đo được lợi ích và kiểm soát drift mới cân nhắc đưa tín hiệu yếu vào vòng
học.

## Phương án bị loại

Tôi loại phương án chấm toàn bộ giao dịch bằng batch mỗi đêm: dễ vận hành,
nhưng kết quả đến sau khi giao dịch được duyệt nên không giải quyết mục tiêu
ngăn tổn thất tức thời. Tôi cũng không chọn chặn giao dịch hoàn toàn tự động
ở MVP. Một ngưỡng điểm duy nhất có thể làm tăng false positive cho một nhóm
người dùng hoặc kênh mới; giai đoạn đầu nên ưu tiên hàng đợi kiểm tra của con
người, đo precision/recall và chi phí chặn nhầm trước khi tự động hóa hành
động ảnh hưởng trực tiếp tới khách hàng.

## Sơ đồ kiến trúc

```text
Hệ thống thanh toán ──► Bronze bất biến (raw + event_time + ingested_at)
                                  │
                                  ▼
                    Schema / quality gate / quarantine
                                  │
                                  ▼
             Silver: dedup theo event_id, trạng thái theo sequence
                    │                             │
                    ▼                             ▼
       Batch feature lịch sử             Stream feature ngắn hạn
                    └──────────────┬──────────────┘
                                   ▼
                    Feature contract + fraud score
                                   │
                    ┌──────────────┴────────────┐
                    ▼                           ▼
           Cho phép / xác minh          Hàng đợi điều tra
                                                │
                              nhãn đã xác nhận ─┘
                                      ▼
                         Eval và train snapshot PIT
```

Các chỉ số MVP gồm p95/p99 độ trễ chấm điểm, tỷ lệ sự kiện trùng và
quarantine, false positive theo kênh, precision trên cảnh báo đã điều tra,
tổn thất phát hiện muộn, độ lệch feature online/offline và chi phí xử lý mỗi
giao dịch. Ngưỡng cảnh báo và hành động tự động chỉ được chọn sau khi nhóm vận
hành xác định mức rủi ro chấp nhận được.
