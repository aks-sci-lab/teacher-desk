"""드라이브에 올릴 자료 암호화 (0.5.96) — 이 파일만 암호 방식을 압니다.

- 방식: AES-256-GCM (검증된 표준, `cryptography` 패키지) + 암호문에서 키 만들기는 표준 라이브러리 scrypt.
  표준 라이브러리에는 AES 가 없어서, 윈도우용 ZIP 에 `cryptography` 를 pypdf 처럼 함께 넣습니다.
  패키지가 없으면(개발 폴더 등) `available()` 이 False 이고 드라이브 동기화만 꺼집니다 — 앱은 그대로.
- 파일 형식: b"TDSK" · 형식 1 · scrypt log2(n)·r·p · salt 16 · nonce 12 · 암호문+태그(16).
  머리 부분 전체를 추가 인증 자료(AAD)로 넣어 머리를 바꾸면 풀리지 않습니다.
- 복구 키(암호문)는 선생님 PC 의 config.json 에만 두고 드라이브·로그·화면에 다시 보내지 않습니다.
  잃으면 드라이브의 자료를 풀 수 없습니다 (구글도, 개발자도 못 풂).
"""

import hashlib
import os
import secrets

MAGIC = b"TDSK"
FORMAT = 1
LOG_N, R, P = 15, 8, 1          # scrypt: 메모리 약 32MB, 보통 PC 에서 0.1~0.3초
SALT, NONCE = 16, 12
HEAD = len(MAGIC) + 4 + SALT + NONCE
MIN_LEN = 10
# 복구 키 글자: 헷갈리는 0·O·1·I·L 을 뺀 영문 대문자·숫자
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_cache = {}   # (암호문 해시, salt, 변수) → 키. 같은 파일을 여러 번 풀 때 다시 계산하지 않게 (이 프로세스 안에서만)


class CryptError(Exception):
    pass


def available():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except Exception:
        return False


def new_recovery_key():
    """선생님이 적어 둘 복구 키: XXXXX-XXXXX-XXXXX-XXXXX-XXXXX (약 124비트)."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(25))
    return "-".join(raw[i:i + 5] for i in range(0, 25, 5))


def normalize(passphrase):
    """복구 키는 띄어쓰기·하이픈·대소문자와 상관없이 같게. 직접 정한 암호문은 앞뒤 빈칸만 정리."""
    p = (passphrase or "").strip()
    compact = p.replace("-", "").replace(" ", "").upper()
    if len(compact) == 25 and all(c in ALPHABET for c in compact):
        return compact
    return p


def check_passphrase(passphrase):
    p = normalize(passphrase)
    if len(p) < MIN_LEN:
        raise CryptError(f"복구 키(암호문)는 {MIN_LEN}자 이상이어야 합니다. [복구 키 만들기]를 쓰면 쉽습니다.")
    if len(p) > 200:
        raise CryptError("복구 키(암호문)가 너무 깁니다 (200자까지)")
    return p


def _key(passphrase, salt, log_n, r, p):
    # 파일 머리의 scrypt 변수는 이 형식(1)에서 정한 값만 받음 — 바뀐 파일이 메모리·시간을 크게 쓰게 하거나
    # hashlib 오류로 동기화가 멈추지 않게 (총괄 검토 2026-11-14)
    if (log_n, r, p) != (LOG_N, R, P):
        raise CryptError("암호 파일 형식이 잘못되었습니다 (손상되었거나 다른 프로그램이 만든 파일)")
    p_norm = normalize(passphrase)
    tag = (hashlib.sha256(p_norm.encode("utf-8")).digest(), salt, log_n, r, p)
    if tag not in _cache:
        try:
            _cache[tag] = hashlib.scrypt(p_norm.encode("utf-8"), salt=salt, n=1 << log_n, r=r, p=p,
                                         maxmem=256 * 1024 * 1024, dklen=32)
        except (ValueError, MemoryError) as e:
            raise CryptError(f"암호 키를 만들지 못했습니다 ({type(e).__name__})")
        if len(_cache) > 8:
            _cache.pop(next(iter(_cache)))
    return _cache[tag]


def encrypt(data, passphrase):
    if not available():
        raise CryptError("암호화 도구(cryptography)가 없습니다. 윈도우용 ZIP 설치본에서 쓰세요")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(SALT), os.urandom(NONCE)
    head = MAGIC + bytes([FORMAT, LOG_N, R, P]) + salt + nonce
    return head + AESGCM(_key(passphrase, salt, LOG_N, R, P)).encrypt(nonce, bytes(data), head)


def decrypt(blob, passphrase):
    if not available():
        raise CryptError("암호화 도구(cryptography)가 없습니다. 윈도우용 ZIP 설치본에서 쓰세요")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = bytes(blob)
    if len(blob) < HEAD + 16 or blob[:4] != MAGIC:
        raise CryptError("업무 데스크가 암호화한 파일이 아닙니다")
    if blob[4] != FORMAT:
        raise CryptError("더 새 버전의 업무 데스크가 만든 파일입니다. 업무 데스크를 업데이트하세요")
    log_n, r, p = blob[5], blob[6], blob[7]
    salt = blob[8:8 + SALT]
    nonce = blob[8 + SALT:HEAD]
    try:
        return AESGCM(_key(passphrase, salt, log_n, r, p)).decrypt(nonce, blob[HEAD:], blob[:HEAD])
    except InvalidTag:
        raise CryptError("복구 키(암호문)가 맞지 않거나 파일이 손상되었습니다")
