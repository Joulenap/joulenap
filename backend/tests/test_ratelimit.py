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


def test_the_lock_is_still_in_force_at_the_last_instant():
    # <= 0 means "expired", so the boundary matters: an off-by-one either releases a
    # brute-force lock a second early or holds one that should have lapsed.
    t = [0.0]
    rl = LoginRateLimiter(max_failures=1, lockout_seconds=50, now=lambda: t[0])
    rl.record_failure("4.4.4.4")

    t[0] = 49.9
    assert rl.locked_for("4.4.4.4") > 0  # still locked

    t[0] = 50.0
    assert rl.locked_for("4.4.4.4") == 0.0  # exactly expired, not a moment later


def test_a_lapsed_lock_starts_a_fresh_streak():
    # The entry is dropped on expiry, so the next attempt gets the full allowance again
    # rather than being locked out by one more failure.
    t = [0.0]
    rl = LoginRateLimiter(max_failures=2, lockout_seconds=10, now=lambda: t[0])
    rl.record_failure("5.5.5.5")
    rl.record_failure("5.5.5.5")
    assert rl.locked_for("5.5.5.5") == 10.0

    t[0] = 11.0
    assert rl.locked_for("5.5.5.5") == 0.0
    rl.record_failure("5.5.5.5")
    assert rl.locked_for("5.5.5.5") == 0.0  # one failure into a new streak, not locked


def test_the_expiry_instant_clears_the_entry_not_just_the_answer():
    # locked_for reports 0 either way at the exact instant; what has to happen *as well* is
    # that the entry is dropped, so the next failure starts a fresh streak instead of
    # re-locking immediately off the old count.
    t = [0.0]
    rl = LoginRateLimiter(max_failures=2, lockout_seconds=50, now=lambda: t[0])
    rl.record_failure("7.7.7.7")
    rl.record_failure("7.7.7.7")

    t[0] = 50.0
    assert rl.locked_for("7.7.7.7") == 0.0

    rl.record_failure("7.7.7.7")
    assert rl.locked_for("7.7.7.7") == 0.0  # one of two, not locked again


def test_an_ip_that_never_failed_is_never_locked():
    rl = LoginRateLimiter(max_failures=1, lockout_seconds=50, now=lambda: 0.0)
    assert rl.locked_for("6.6.6.6") == 0.0
