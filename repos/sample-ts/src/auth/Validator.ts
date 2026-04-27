/** Base validator — kiểm tra format thô của username/password. */
export class Validator {
  validate(username: string, password: string): boolean {
    return this.checkUsername(username) && this.checkPassword(password);
  }

  protected checkUsername(username: string): boolean {
    return username.length >= 4 && /^[a-zA-Z0-9_]+$/.test(username);
  }

  protected checkPassword(password: string): boolean {
    return password.length >= 8;
  }
}

/** Validator nghiêm ngặt hơn cho admin — yêu cầu password phức tạp. */
export class StrictValidator extends Validator {
  protected checkPassword(password: string): boolean {
    return (
      password.length >= 12 &&
      /[A-Z]/.test(password) &&
      /[0-9]/.test(password) &&
      /[^A-Za-z0-9]/.test(password)
    );
  }
}
