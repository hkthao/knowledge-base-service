namespace SampleApp.Auth;

/// <summary>Hợp đồng đăng nhập + xét duyệt vay.</summary>
public interface IAuthService
{
    bool Login(string username, string password);
    bool ApproveLoan(string customerId, decimal requestedAmount);
}
