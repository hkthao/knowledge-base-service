using SampleApp.Credit;

namespace SampleApp.Auth;

/// <summary>Service đăng nhập + xét duyệt vay; entry point của module Auth.</summary>
public class AuthService : IAuthService
{
    private readonly Validator _validator;
    private readonly CreditChecker _creditChecker;
    private readonly Dictionary<string, Customer> _customers;

    public AuthService(Validator validator, CreditChecker creditChecker)
    {
        _validator = validator;
        _creditChecker = creditChecker;
        _customers = new Dictionary<string, Customer>();
    }

    public bool Login(string username, string password)
    {
        return _validator.Validate(username, password);
    }

    /// <summary>Khách đã đăng nhập có thể yêu cầu khoản vay.</summary>
    public bool ApproveLoan(string customerId, decimal requestedAmount)
    {
        if (!_customers.TryGetValue(customerId, out var customer))
        {
            return false;
        }
        return _creditChecker.CheckLimit(customer, requestedAmount);
    }

    public void RegisterCustomer(Customer customer)
    {
        _customers[customer.Id] = customer;
    }
}
