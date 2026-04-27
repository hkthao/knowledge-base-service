import { AuthService } from "./auth/AuthService";
import { StrictValidator } from "./auth/Validator";
import { VipCustomer } from "./credit/Customer";

const auth = new AuthService(new StrictValidator());

const vip = new VipCustomer(
  {
    id: "kh-0001",
    fullName: "Nguyễn Văn A",
    monthlyIncome: 50_000_000,
    outstandingDebt: 100_000_000,
    creditScore: 720,
  },
  /* vipTier */ 2,
);

console.log(auth.login("nguyen.a", "Str0ng-Pass!"));
console.log(auth.approveLoan(vip, 300_000_000));
