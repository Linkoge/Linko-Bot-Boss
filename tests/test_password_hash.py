"""Password hashing: the app always uses argon2's default parameters; only disposable test data opts out (conftest.py)."""
from argon2 import PasswordHasher


def test_default_parameters_unless_test_data_opts_out_and_hashes_stay_compatible(monkeypatch):
    from ai_manager.auth import WEAK_HASH_ENV, password_hasher

    default = PasswordHasher()
    for value in (None, "0", "true", "yes"):  # only the exact test switch weakens it
        if value is None:
            monkeypatch.delenv(WEAK_HASH_ENV, raising=False)
        else:
            monkeypatch.setenv(WEAK_HASH_ENV, value)
        hasher = password_hasher()
        assert (hasher.memory_cost, hasher.time_cost, hasher.parallelism) == \
            (default.memory_cost, default.time_cost, default.parallelism)
    strong = password_hasher().hash("correct horse battery")
    monkeypatch.setenv(WEAK_HASH_ENV, "1")
    weak = password_hasher()
    assert (weak.memory_cost, weak.time_cost, weak.parallelism) == (8, 1, 1)
    # Parameters live in the encoded hash, so either hasher verifies the other's hashes.
    assert weak.verify(strong, "correct horse battery") and default.verify(weak.hash("pw"), "pw")
