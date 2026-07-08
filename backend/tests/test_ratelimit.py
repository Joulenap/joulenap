from app.core.ratelimit import LoginRateLimiter


def test_locks_after_max_failures():
    t = [0.0]
    rl = LoginRateLimiter(max_failures=3, lockout_seconds=100, now=lambda: t[0])
    for _ in range(2):
        rl.record_failure("1.1.1.1")
    assert rl.locked_for("1.1.1.1") == 0.0
    rl.record_failure("1.1.1.1")  # 3rd = lock
    assert rl.locked_for("1.1.1.1") == 100.0

def test_lock_expires_and_resets():
    t = [0.0]
    rl = LoginRateLimiter(max_failures=1, lockout_seconds=50, now=lambda: t[0])
    rl.record_failure("2.2.2.2")
    assert rl.locked_for("2.2.2.2") == 50.0
    t[0] = 51.0
    assert rl.locked_for("2.2.2.2") == 0.0  # window expired, entry cleared

def test_success_resets():
    rl = LoginRateLimiter(max_failures=1, lockout_seconds=50, now=lambda: 0.0)
    rl.record_failure("3.3.3.3")
    rl.reset("3.3.3.3")
    assert rl.locked_for("3.3.3.3") == 0.0
