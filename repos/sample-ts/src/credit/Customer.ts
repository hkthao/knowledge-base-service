/** Khách hàng vay vốn — model dùng xuyên suốt module credit. */
export interface Customer {
  id: string;
  fullName: string;
  monthlyIncome: number;
  outstandingDebt: number;
  creditScore: number;
}

/** Khách VIP có hạn mức gấp đôi. */
export class VipCustomer implements Customer {
  id: string;
  fullName: string;
  monthlyIncome: number;
  outstandingDebt: number;
  creditScore: number;
  vipTier: number;

  constructor(data: Customer, vipTier: number) {
    this.id = data.id;
    this.fullName = data.fullName;
    this.monthlyIncome = data.monthlyIncome;
    this.outstandingDebt = data.outstandingDebt;
    this.creditScore = data.creditScore;
    this.vipTier = vipTier;
  }
}
