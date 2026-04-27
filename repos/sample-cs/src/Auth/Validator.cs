namespace SampleApp.Auth;

/// <summary>Kiểm tra format username + password.</summary>
public class Validator
{
    public virtual bool Validate(string username, string password)
    {
        return CheckUsername(username) && CheckPassword(password);
    }

    protected virtual bool CheckUsername(string username)
    {
        return !string.IsNullOrEmpty(username) && username.Length >= 4;
    }

    protected virtual bool CheckPassword(string password)
    {
        return !string.IsNullOrEmpty(password) && password.Length >= 8;
    }
}

/// <summary>Validator nghiêm ngặt cho admin.</summary>
public class StrictValidator : Validator
{
    protected override bool CheckPassword(string password)
    {
        if (string.IsNullOrEmpty(password) || password.Length < 12) return false;
        bool hasUpper = false, hasDigit = false, hasSymbol = false;
        foreach (var c in password)
        {
            if (char.IsUpper(c)) hasUpper = true;
            else if (char.IsDigit(c)) hasDigit = true;
            else if (!char.IsLetterOrDigit(c)) hasSymbol = true;
        }
        return hasUpper && hasDigit && hasSymbol;
    }
}
