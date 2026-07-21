# /simplify — Code simplification and refactoring

Reduce complexity and improve clarity without changing behavior.

## What to do

- **Remove dead code** (unreachable branches, unused variables, imports)
- **Consolidate duplicates** (but only if it doesn't create over-abstraction)
- **Simplify conditionals** (flatten nested ifs, use guard clauses)
- **Reduce function size** (functions > 40 lines often hide concepts)
- **Simplify data structures** (dict → dataclass, tuple → named tuple)
- **Extract magic constants** (3.14159 → PI, "admin" → ADMIN_ROLE)
- **Improve naming** (x → count, f → format_date)
- **Reduce cognitive load** (fewer branches, simpler logic)

## Constraints

- **No behavior changes** — output before and after simplification must be identical
- **No new abstractions** — wait until you see the pattern 3 times
- **No optimization** — that's a separate skill (/perf)
- **Tests must still pass** — run test suite after each change

## Patterns to apply

### Guard clauses
```python
# Before: nested if
def validate(x):
    if x is not None:
        if len(x) > 0:
            return process(x)
    return None

# After: early exit
def validate(x):
    if x is None or len(x) == 0:
        return None
    return process(x)
```

### Consolidate duplicates
```python
# Before: two functions with same logic
def add_user(name, email):
    user = User(name, email)
    db.save(user)
    return user

def add_admin(name, email):
    admin = Admin(name, email)
    db.save(admin)
    return admin

# After: parametrized function
def add_account(name, email, role):
    account = (Admin if role == "admin" else User)(name, email)
    db.save(account)
    return account
```

### Extract magic constants
```python
# Before
if user.age > 18 and user.account_age_days > 365:
    allow_sensitive_operations(user)

# After
ADULT_AGE = 18
MIN_ACCOUNT_AGE_DAYS = 365

if user.age > ADULT_AGE and user.account_age_days > MIN_ACCOUNT_AGE_DAYS:
    allow_sensitive_operations(user)
```

## Output

Describe each simplification (before/after code snippets), verify tests still pass.
