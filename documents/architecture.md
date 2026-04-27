# Kiến trúc hệ thống cho vay

## Tổng quan

Hệ thống được tách thành hai service: `auth` xử lý đăng nhập + điều
phối yêu cầu vay; `credit` chứa logic tính hạn mức và quy tắc xét duyệt.
Hai module giao tiếp qua interface trong cùng process; không có gọi
mạng giữa chúng.

## Luồng đăng nhập

1. Người dùng gửi username + password lên `AuthService.login`.
2. `AuthService` ủy quyền cho `Validator` để kiểm tra format. Admin
   phải đi qua `StrictValidator` (yêu cầu password 12 ký tự với chữ
   hoa, số và ký tự đặc biệt).
3. Nếu format hợp lệ, service tra session trong store nội bộ và trả
   token tương ứng.

## Luồng xét duyệt vay

1. Khách gọi `AuthService.approveLoan` với customerId và số tiền yêu
   cầu.
2. Service look up `Customer` từ in-memory store, sau đó gọi
   `CreditChecker.CheckLimit` (C#) hoặc `checkCreditLimit` (TS) tùy
   stack.
3. Logic kiểm tra: số tiền yêu cầu ≤ hạn mức tính được, và credit score
   ≥ 600.
4. Nếu từ chối, helper `explainCreditDecision` (TS) trả lý do cụ thể.

## Quan hệ ngầm

- `AuthService` phụ thuộc `Validator` và `CreditChecker` qua constructor
  injection.
- `StrictValidator` extends `Validator`, override `checkPassword`.
- `VipCustomer` implements `Customer` (TS) — chỉ hiện ở stack TS, C#
  dùng record `Customer` không phân biệt VIP.

## Lịch sử thay đổi gần đây

- Tách `StrictValidator` ra để admin login không dùng cùng rule với
  user thường.
- Đổi `BaseLimitRatio` từ 4 thành 5 (cho phép vay nhiều hơn theo tỷ lệ
  thu nhập).
- Thêm `explainCreditDecision` để trả lý do từ chối thay vì boolean.
