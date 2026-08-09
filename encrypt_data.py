# -*- coding: utf-8 -*-
"""
מצפין את קבצי הנתונים הרגישים ב-docs/ כך שרק מי שמקליד את הסיסמה
(SITE_PASSWORD) יוכל לפענח אותם בדפדפן (Web Crypto).

פורמט מעטפה (JSON תואם Web Crypto):
  {"v":1, "salt":<b64>, "iv":<b64>, "ct":<b64>}
  - KDF: PBKDF2-HMAC-SHA256, 200,000 iterations, 32-byte key
  - Cipher: AES-256-GCM (ct כולל את ה-tag בסוף, כמו ב-Web Crypto)

לכל הרצה salt+iv אקראיים. הדפדפן גוזר את המפתח פעם אחת לכל salt (caching).
לא מוחק את קובצי המקור — הסרתם מהמאגר מנוהלת ע"י git/.gitignore.
"""
import os, sys, json, base64, secrets
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 200_000

# כל הקבצים הרגישים שהדשבורד טוען
TARGETS = [
    "data.json", "today.json", "search.json", "reports.json",
    "sales.json", "users.json", "promos.json",
    "historical_today.json", "historical_sales.json",
    "staff_reports.json",
]


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    return kdf.derive(password.encode("utf-8"))


def main():
    password = os.environ.get("SITE_PASSWORD")
    if not password:
        print("ERROR: SITE_PASSWORD environment variable is not set", file=sys.stderr)
        sys.exit(1)

    docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    salt = secrets.token_bytes(16)
    key = derive_key(password, salt)
    aes = AESGCM(key)
    salt_b64 = base64.b64encode(salt).decode()

    encrypted = 0
    for name in TARGETS:
        path = os.path.join(docs, name)
        if not os.path.exists(path):
            print(f"skip (missing): {name}")
            continue
        with open(path, "rb") as f:
            plaintext = f.read()
        iv = secrets.token_bytes(12)
        ct = aes.encrypt(iv, plaintext, None)  # ct||tag (16-byte tag) — תואם Web Crypto
        envelope = {
            "v": 1,
            "salt": salt_b64,
            "iv": base64.b64encode(iv).decode(),
            "ct": base64.b64encode(ct).decode(),
        }
        with open(path + ".enc", "w", encoding="utf-8") as f:
            json.dump(envelope, f)
        encrypted += 1
        print(f"encrypted: {name} -> {name}.enc ({len(plaintext):,} bytes)")

    print(f"\nDone. {encrypted} file(s) encrypted.")


if __name__ == "__main__":
    main()
