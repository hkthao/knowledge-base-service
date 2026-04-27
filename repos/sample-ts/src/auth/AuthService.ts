import { Validator } from "./Validator";
import { checkCreditLimit, explainCreditDecision } from "../credit/limits";
import { Customer } from "../credit/Customer";

/**
 * Service xử lý đăng nhập + xét duyệt khoản vay nhanh.
 * Login đơn giản chỉ check format; quyết định cho vay dựa trên hạn mức tín dụng.
 */
export class AuthService {
  constructor(private validator: Validator) {}

  login(username: string, password: string): boolean {
    if (!this.validator.validate(username, password)) {
      return false;
    }
    return this.lookupActiveSession(username) !== null;
  }

  /** Khách đã đăng nhập có thể yêu cầu khoản vay; trả lý do nếu bị từ chối. */
  approveLoan(customer: Customer, requestedAmount: number): { ok: boolean; reason: string } {
    const ok = checkCreditLimit(customer, requestedAmount);
    return { ok, reason: ok ? "Approved" : explainCreditDecision(customer, requestedAmount) };
  }

  private lookupActiveSession(username: string): string | null {
    return username ? `session-${username}` : null;
  }
}
