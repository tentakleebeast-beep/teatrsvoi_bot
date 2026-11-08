"""Ключ идемпотентности: docs/development/16 §4.4 п.21."""

from app.rules import idempotency_key


def test_ключ_стабилен_для_одного_сообщения():
    assert idempotency_key(111, 42) == idempotency_key(111, 42)


def test_ключ_различается_по_сообщению():
    assert idempotency_key(111, 42) != idempotency_key(111, 43)


def test_ключ_различается_по_чату():
    assert idempotency_key(111, 42) != idempotency_key(222, 42)


def test_без_message_id_ключа_нет():
    assert idempotency_key(111, None) == ""
