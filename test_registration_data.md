# Test Registration Data

## Valid Test Cases (Should Pass All Validation)

| First Name | Last Name | Email | Password | Confirm Password | Expected Result |
|------------|-----------|-------|----------|------------------|-----------------|
| John | Smith | john.smith@gmail.com | Password123! | Password123! | ✅ Success |
| Sarah | Johnson | sarah.j@gmail.com | MySecure99 | MySecure99 | ✅ Success |
| Michael | Brown | m.brown@company.org | StrongPass1 | StrongPass1 | ✅ Success |
| Emma | Wilson | emma_wilson@outlook.com | Testing456 | Testing456 | ✅ Success |

## Invalid Test Cases (Should Trigger Validation Errors)

### Name Validation Errors
| First Name | Last Name | Email | Password | Confirm Password | Expected Error |
|------------|-----------|-------|----------|------------------|----------------|
| J | Smith | j.smith@example.com | Password123! | Password123! | ❌ First name too short |
| John | S | john.s@example.com | Password123! | Password123! | ❌ Last name too short |
| | Johnson | empty@example.com | Password123! | Password123! | ❌ First name required |
| Michael | | michael@example.com | Password123! | Password123! | ❌ Last name required |

### Email Validation Errors
| First Name | Last Name | Email | Password | Confirm Password | Expected Error |
|------------|-----------|-------|----------|------------------|----------------|
| John | Smith | invalid-email | Password123! | Password123! | ❌ Invalid email format |
| Sarah | Johnson | missing@domain | Password123! | Password123! | ❌ Invalid email format |
| Michael | Brown | @missinglocal.com | Password123! | Password123! | ❌ Invalid email format |
| Emma | Wilson | spaces in@email.com | Password123! | Password123! | ❌ Invalid email format |
| Test | User | | Password123! | Password123! | ❌ Email required |

### Password Validation Errors
| First Name | Last Name | Email | Password | Confirm Password | Expected Error |
|------------|-----------|-------|----------|------------------|----------------|
| John | Smith | john@example.com | short | short | ❌ Password too short |
| Sarah | Johnson | sarah@example.com | alllowercase1 | alllowercase1 | ❌ No uppercase letter |
| Michael | Brown | michael@example.com | ALLUPPERCASE1 | ALLUPPERCASE1 | ❌ No lowercase letter |
| Emma | Wilson | emma@example.com | NoNumbers! | NoNumbers! | ❌ No numbers |
| Test | User | test@example.com | Password123! | DifferentPass1 | ❌ Passwords don't match |
| Jane | Doe | jane@example.com | Password123! | | ❌ Confirmation required |

### Edge Cases & Special Characters
| First Name | Last Name | Email | Password | Confirm Password | Expected Error |
|------------|-----------|-------|----------|------------------|----------------|
| José | García | jose.garcia@example.com | Password123! | Password123! | ✅ Should work (accented chars) |
| Mary-Ann | O'Connor | mary-ann@example.com | Password123! | Password123! | ✅ Should work (hyphens/apostrophes) |
| A | B | a@b.co | 12345678 | 12345678 | ❌ Multiple errors |
| VeryLongFirstNameThatExceedsTheMaximumLengthAllowedInTheDatabase | Smith | long@example.com | Password123! | Password123! | ❌ Name too long |

## SQL Injection & XSS Test Cases (Should Be Safely Handled)

| First Name | Last Name | Email | Password | Confirm Password | Expected Result |
|------------|-----------|-------|----------|------------------|-----------------|
| Robert'; DROP TABLE users; -- | Smith | robert@example.com | Password123! | Password123! | ✅ Safely escaped |
| `<script>alert('xss')</script>` | Johnson | script@example.com | Password123! | Password123! | ✅ Safely escaped |
| admin' OR '1'='1 | hacker | admin@example.com | Password123! | Password123! | ✅ Safely escaped |

## Duplicate Email Test
1. First register with: `john.doe@example.com`
2. Then try to register again with same email but different name
3. Should get: "An account with this email already exists"

## Client-Side vs Server-Side Testing

### Client-Side Only (JavaScript validation)
- Try submitting without checking "Terms of Service" checkbox
- Type passwords that don't match (should show validation immediately)
- Try submitting empty required fields

### Server-Side Testing
- Disable JavaScript in browser and test all invalid cases above
- Use browser dev tools to modify form validation attributes
- Send direct POST requests with invalid data

## Test Automation Script (Optional)

```python
# Quick test data generator for automated testing
test_cases = [
    # Valid cases
    ("John", "Smith", "john.smith@test.com", "Password123!", "Password123!"),
    ("Sarah", "Johnson", "sarah.j@test.com", "MySecure99", "MySecure99"),
    
    # Invalid cases
    ("J", "Smith", "j@test.com", "Password123!", "Password123!"),  # Short first name
    ("John", "", "john@test.com", "Password123!", "Password123!"),  # Empty last name
    ("John", "Smith", "invalid-email", "Password123!", "Password123!"),  # Bad email
    ("John", "Smith", "john@test.com", "short", "short"),  # Short password
    ("John", "Smith", "john@test.com", "Password123!", "Different123!"),  # Mismatched passwords
]
```

## Testing Checklist

- [ ] Valid registrations complete successfully
- [ ] Short names rejected (< 2 characters)
- [ ] Empty required fields rejected
- [ ] Invalid email formats rejected
- [ ] Weak passwords rejected (no uppercase/lowercase/numbers)
- [ ] Password confirmation mismatch rejected
- [ ] Duplicate emails rejected
- [ ] Terms checkbox required
- [ ] SQL injection attempts safely handled
- [ ] XSS attempts safely escaped
- [ ] Client-side validation works with JavaScript enabled
- [ ] Server-side validation works with JavaScript disabled