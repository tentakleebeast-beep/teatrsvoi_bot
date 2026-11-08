#!/usr/bin/env python3
"""Проверка хранилища фактом: положили — прочитали — отдали по ссылке (Ш-1).

    python scripts/storage_smoke.py                 # оба бакета
    python scripts/storage_smoke.py --bucket shared # только общий
    python scripts/storage_smoke.py --keep          # не удалять пробный объект

Проверяется то, на чём держится вся работа с файлами (docs/development/86):
  * PutObject / HeadObject / GetObject и совпадение байтов (sha256);
  * presigned PUT и presigned GET — загрузка из Mini App идёт мимо Telegram именно так;
  * ListObjectsV2 и DeleteObject;
  * каждый бакет доступен, а ключ имеет права read+write.

Код выхода 1 при первой же неудаче: молча «почти работает» не бывает.
Если STORAGE_ENDPOINT не задан, скрипт ничего не проверяет и честно об этом говорит.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request

TEST_BODY = b"\x00svoi storage smoke\xff" * 64


def load_project_env() -> None:
    """Взять настройки из .env проекта, не перебивая то, что уже задано в окружении."""
    from _cli import apply_env

    try:
        apply_env()
    except ImportError:  # python-dotenv не установлен — живём на системном окружении
        return


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def make_client():
    """S3-клиент под настройки проекта. boto3 — единственная зависимость."""
    import boto3
    from botocore.config import Config

    endpoint = env("STORAGE_ENDPOINT")
    if not endpoint:
        return None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=env("STORAGE_ACCESS_KEY"),
        aws_secret_access_key=env("STORAGE_SECRET_KEY"),
        region_name=env("STORAGE_REGION", "svoi"),
        # path-style обязателен: у Garage нет DNS на имя бакета
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def bucket_names(only: str | None = None) -> list[str]:
    private = env("STORAGE_BUCKET_PRIVATE", "svoi-private")
    shared = env("STORAGE_BUCKET_SHARED", "svoi-shared")
    chosen = {"private": [private], "shared": [shared]}
    if only in chosen:
        return chosen[only]
    return [private, shared]


def _signed_fetch(url: str, data: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, method="PUT" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, response.read()


def check_bucket(client, bucket: str, keep: bool = False) -> list[str]:
    """Полный круг по одному бакету. Возвращает список проблем (пусто — хорошо)."""
    problems: list[str] = []
    key = f"_smoke/{bucket}-probe.bin"
    try:
        client.put_object(Bucket=bucket, Key=key, Body=TEST_BODY,
                          ContentType="application/octet-stream")
        head = client.head_object(Bucket=bucket, Key=key)
        if head["ContentLength"] != len(TEST_BODY):
            problems.append(f"{bucket}: размер после put {head['ContentLength']} != {len(TEST_BODY)}")
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        if hashlib.sha256(body).hexdigest() != hashlib.sha256(TEST_BODY).hexdigest():
            problems.append(f"{bucket}: байты не совпали после get")

        put_url = client.generate_presigned_url(
            "put_object", Params={"Bucket": bucket, "Key": key + ".presigned"}, ExpiresIn=600)
        status, _ = _signed_fetch(put_url, b"presigned body")
        if status != 200:
            problems.append(f"{bucket}: presigned PUT вернул {status}")
        get_url = client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key + ".presigned"}, ExpiresIn=600)
        status, payload = _signed_fetch(get_url)
        if status != 200 or payload != b"presigned body":
            problems.append(f"{bucket}: presigned GET вернул {status}, тело {payload[:40]!r}")

        listed = client.list_objects_v2(Bucket=bucket, Prefix="_smoke/")
        names = {item["Key"] for item in listed.get("Contents", [])}
        if key not in names:
            problems.append(f"{bucket}: list_objects_v2 не увидел {key}")
    except Exception as exc:                        # noqa: BLE001 — отчёт важнее типа ошибки
        problems.append(f"{bucket}: {type(exc).__name__}: {exc}")
    finally:
        if not keep:
            for suffix in ("", ".presigned"):
                try:
                    client.delete_object(Bucket=bucket, Key=key + suffix)
                except Exception:                   # noqa: BLE001 — уборка не должна ломать отчёт
                    pass
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка объектного хранилища")
    parser.add_argument("--bucket", choices=["private", "shared"], help="только один бакет")
    parser.add_argument("--keep", action="store_true", help="оставить пробные объекты")
    args = parser.parse_args(argv)
    load_project_env()

    client = make_client()
    if client is None:
        print("STORAGE_ENDPOINT не задан — проверять нечего (хранилище не настроено)")
        return 0

    problems: list[str] = []
    for bucket in bucket_names(args.bucket):
        found = check_bucket(client, bucket, args.keep)
        problems.extend(found)
        print(f"{bucket}: {'ошибка' if found else 'ок'}")

    if problems:
        print("\nпроблемы:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nхранилище отвечает: put/get, presigned PUT и GET, list, delete — ок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
