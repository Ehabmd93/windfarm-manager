"""
Cloudflare R2 storage helper (S3-compatible).

Required environment variables:
    R2_ACCOUNT_ID        — Cloudflare account ID (32-char hex)
    R2_ACCESS_KEY_ID     — R2 API token access key
    R2_SECRET_ACCESS_KEY — R2 API token secret key
    R2_BUCKET_NAME       — bucket name (default: windfarm-docs)

If any of these are missing the module is silently disabled and the app
falls back to base64-in-database storage.
"""
import os
import uuid
import unicodedata
import re
from urllib.parse import quote as _q

_BOTO_OK = False
try:
    import boto3
    from botocore.config import Config as _BotoConfig
    _BOTO_OK = True
except ImportError:
    pass


# ── Config ────────────────────────────────────────────────────────────────────

def r2_enabled() -> bool:
    """True when all required env vars are present and boto3 is installed."""
    return _BOTO_OK and bool(
        os.environ.get('R2_ACCOUNT_ID') and
        os.environ.get('R2_ACCESS_KEY_ID') and
        os.environ.get('R2_SECRET_ACCESS_KEY')
    )


def _client():
    account_id = os.environ['R2_ACCOUNT_ID']
    return boto3.client(
        's3',
        endpoint_url         = f'https://{account_id}.r2.cloudflarestorage.com',
        aws_access_key_id    = os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key= os.environ['R2_SECRET_ACCESS_KEY'],
        config               = _BotoConfig(signature_version='s3v4'),
        region_name          = 'auto',
    )


def _bucket():
    return os.environ.get('R2_BUCKET_NAME', 'windfarm-docs')


# ── Key helpers ───────────────────────────────────────────────────────────────

def make_key(original_filename: str, project_id=None) -> str:
    """Generate a unique, URL-safe object key."""
    ext       = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else 'bin'
    uid       = uuid.uuid4().hex
    prefix    = f'projects/{project_id}' if project_id else 'uploads'
    return f'{prefix}/{uid}.{ext}'


def _safe_ascii(name: str) -> str:
    """Strip non-ASCII chars for use in HTTP header values."""
    n = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    n = re.sub(r'[\\"]', '', n).strip()
    return n or 'download'


# ── Core operations ───────────────────────────────────────────────────────────

def upload(key: str, data: bytes, content_type: str = 'application/octet-stream') -> str:
    """Upload bytes to R2. Returns the key on success, raises on error."""
    _client().put_object(
        Bucket      = _bucket(),
        Key         = key,
        Body        = data,
        ContentType = content_type,
    )
    return key


def delete(key: str) -> bool:
    """Delete an object from R2. Returns True on success."""
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
        return True
    except Exception:
        return False


def presigned_url(key: str, original_filename: str,
                  disposition: str = 'inline', expiry: int = 3600) -> str:
    """
    Generate a pre-signed URL valid for `expiry` seconds.
    disposition = 'inline'     → open in browser (PDF viewer, image)
    disposition = 'attachment' → force download
    """
    ascii_name = _safe_ascii(original_filename)
    utf8_name  = _q(original_filename, safe='')
    cd = f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"

    return _client().generate_presigned_url(
        'get_object',
        Params    = {
            'Bucket'                    : _bucket(),
            'Key'                       : key,
            'ResponseContentDisposition': cd,
        },
        ExpiresIn = expiry,
    )
