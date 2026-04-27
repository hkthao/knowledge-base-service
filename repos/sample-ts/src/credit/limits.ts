import { Customer, VipCustomer } from "./Customer";

const BASE_LIMIT_RATIO = 5;
const VIP_BONUS_RATIO = 2;

/**
 * Tính hạn mức tín dụng tối đa cho khách hàng theo thu nhập + dư nợ hiện tại.
 * Khách VIP có hệ số nhân thêm.
 */
export function calculateCreditLimit(customer: Customer): number {
  const baseLimit = customer.monthlyIncome * BASE_LIMIT_RATIO - customer.outstandingDebt;
  if (customer instanceof VipCustomer) {
    return baseLimit * VIP_BONUS_RATIO * customer.vipTier;
  }
  return Math.max(0, baseLimit);
}

/**
 * Kiểm tra hạn mức tín dụng có đủ để cấp khoản vay không.
 * Đây là entry point được gọi từ AuthService khi xét duyệt hồ sơ.
 */
export function checkCreditLimit(customer: Customer, requestedAmount: number): boolean {
  const limit = calculateCreditLimit(customer);
  return requestedAmount <= limit && customer.creditScore >= 600;
}

/** Helper trả mô tả ngắn về kết quả check. */
export function explainCreditDecision(customer: Customer, requested: number): string {
  const limit = calculateCreditLimit(customer);
  if (requested > limit) {
    return `Vượt hạn mức: yêu cầu ${requested}, tối đa ${limit}`;
  }
  if (customer.creditScore < 600) {
    return `Điểm tín dụng thấp: ${customer.creditScore}`;
  }
  return "OK";
}
