from app.services.security import hash_password, verify_password


def test_correct_password_verifies():
    h = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", h)


def test_wrong_password_does_not_verify():
    h = hash_password("correct horse battery staple")

    assert not verify_password("wrong", h)


def test_hash_does_not_contain_password():
    h = hash_password("my-secret-pass")

    assert "my-secret-pass" not in h
    assert h.startswith("$argon2")


def test_same_password_gives_different_hashes():
    assert hash_password("same") != hash_password("same")