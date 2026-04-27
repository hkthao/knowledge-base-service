# Chính sách hạn mức tín dụng

Tài liệu mô tả quy tắc tính và kiểm tra hạn mức tín dụng được áp dụng
cho khách hàng cá nhân khi xét duyệt khoản vay.

## Công thức cơ bản

Hạn mức tín dụng tối đa của một khách hàng được tính như sau:

```
hạn_mức = thu_nhập_tháng × hệ_số_BASE − dư_nợ_hiện_tại
```

Hệ số BASE mặc định là 5 (tương đương khả năng chi trả trong 5 tháng).
Nếu kết quả âm, hạn mức được làm tròn về 0.

## Khách VIP

Khách hàng VIP được nhân thêm hệ số bonus theo cấp độ tier (tier 1 nhân
2, tier 2 nhân 4, tier 3 nhân 6). Cấp tier do bộ phận chăm sóc khách
hàng cập nhật thủ công, không tính tự động từ lịch sử giao dịch.

## Điều kiện duyệt vay

Một yêu cầu vay được chấp nhận khi đồng thời:

- Số tiền yêu cầu không vượt hạn mức.
- Điểm tín dụng (credit score) của khách hàng từ 600 trở lên.

Nếu một trong hai điều kiện không thỏa mãn, hệ thống trả về lý do cụ thể
(vượt hạn mức / điểm tín dụng thấp) thay vì chỉ trả về quyết định
boolean.

## Liên quan tới code

- TS: `src/credit/limits.ts::checkCreditLimit` là entry point chính được
  AuthService gọi khi xét duyệt.
- C#: `SampleApp.Credit::CreditChecker.CheckLimit` là entry point bên
  service C#.

Cả hai cài đặt phải khớp logic; bất kỳ thay đổi nào ở chính sách trên
phải được phản ánh đồng thời ở cả hai stack.
