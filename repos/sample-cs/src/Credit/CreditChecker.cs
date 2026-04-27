namespace SampleApp.Credit;

/// <summary>Khách hàng vay vốn.</summary>
public record Customer(string Id, decimal MonthlyIncome, decimal OutstandingDebt, int CreditScore);

/// <summary>Tính và kiểm tra hạn mức tín dụng cho khách hàng.</summary>
public class CreditChecker
{
    private const int BaseLimitRatio = 5;

    /// <summary>Tính hạn mức tối đa: thu nhập tháng × hệ số − dư nợ hiện tại.</summary>
    public decimal CalculateLimit(Customer customer)
    {
        var raw = customer.MonthlyIncome * BaseLimitRatio - customer.OutstandingDebt;
        return raw > 0 ? raw : 0;
    }

    /// <summary>Kiểm tra hạn mức tín dụng có đủ để cấp khoản vay không.</summary>
    public bool CheckLimit(Customer customer, decimal requestedAmount)
    {
        var limit = CalculateLimit(customer);
        return requestedAmount <= limit && customer.CreditScore >= 600;
    }
}
