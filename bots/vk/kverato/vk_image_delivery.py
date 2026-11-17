"""VK card uploads: photo (JPEG, optionally PNG) and document fallback.

Game actions are completed before this presentation-only pipeline. We retry
only uploading unsent media; messages.send is never retried here.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SOURCE_BYTES = 12 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_JPEG_BYTES = 8 * 1024 * 1024
PHOTO_ATTACHMENT = re.compile(r'^photo-?\d+_\d+(?:_[A-Za-z0-9_-]+)?$')
DOC_ATTACHMENT = re.compile(r'^doc-?\d+_\d+(?:_[A-Za-z0-9_-]+)?$')


class VKCardUploadError(RuntimeError):
    """Log-safe information; exception messages and signed URLs stay private."""

    def __init__(self, stage: str, exception_type: str = '', api_code: int | None = None,
                 photo_error: 'VKCardUploadError | None' = None):
        self.stage = stage
        self.exception_type = exception_type
        self.api_code = api_code
        self.photo_error = photo_error  # sanitized first failure, if documents also fail
        # Never put the original exception, server body or signed upload URL here.
        super().__init__(f'{stage}:{exception_type or "failed"}' +
                         (f':code={api_code}' if api_code is not None else ''))


def _safe_vk_error_code(exc: Exception) -> int | None:
    """Extract only VK's numeric error code; do not inspect/stringify messages.

    APIError variants may expose code or error_code, occasionally nested in
    raw_error. Never log the raw VK response, which may contain access tokens.
    """
    for obj in (exc, getattr(exc, 'raw_error', None)):
        if obj is None:
            continue
        for key in ('error_code', 'code'):
            value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
            if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 99999:
                return value
            if isinstance(value, str) and value.isascii() and value.isdecimal():
                number = int(value)
                if 0 < number <= 99999:
                    return number
    return None


def classify_upload_server_response(server: object) -> str:
    """Privacy-safe name of VK's upload protocol (never reveal signed URL)."""
    if isinstance(server, dict):
        url = server.get('upload_url')
    else:
        url = getattr(server, 'upload_url', None)
    if not isinstance(url, str) or len(url) > 8192:
        return 'missing_upload_url'
    try:
        parsed = urlsplit(url)
    except ValueError:
        return 'invalid_upload_url'
    if parsed.scheme != 'https' or not parsed.hostname:
        return 'invalid_upload_url'
    # Read ONLY path, never params, query or fragment (often contain tokens).
    path = parsed.path.lower()
    if '/photo/v2/bulk_upload' in path:
        return 'bulk_v2'
    if 'upload.php' in path or '/upload' in path:
        return 'classic'
    return 'other'


async def probe_vk_photo_upload_protocol(api, peer_id: int) -> str:
    """Read-only API probe used by the opt-in VK diagnostic script only."""
    try:
        response = await api.photos.get_messages_upload_server(peer_id=peer_id)
    except Exception:
        # Expose no exception messages from the VK SDK.
        return 'unavailable'
    return classify_upload_server_response(response)


def _validated_image(image_bytes: bytes) -> Image.Image:
    if not isinstance(image_bytes, bytes) or not (20 <= len(image_bytes) <= MAX_SOURCE_BYTES):
        raise VKCardUploadError('image_validation', 'Size')
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            if source.format not in ('PNG', 'JPEG', 'WEBP'):
                raise VKCardUploadError('image_validation', 'Format')
            w, h = source.size
            if min(w, h) < 100 or max(w, h) > MAX_DIMENSION or w * h > 12_000_000:
                raise VKCardUploadError('image_validation', 'Dimensions')
            source.load()
            return ImageOps.exif_transpose(source).convert('RGB')
    except VKCardUploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, TypeError, OverflowError) as exc:
        raise VKCardUploadError('image_validation', type(exc).__name__) from None


def card_to_jpeg(image_bytes: bytes) -> bytes:
    """Convert and validate once before any request to VK."""
    image = _validated_image(image_bytes)
    try:
        with BytesIO() as stream:
            image.save(stream, format='JPEG', quality=90, optimize=True,
                       progressive=True, subsampling=0)
            result = stream.getvalue()
    except (OSError, ValueError, TypeError) as exc:
        raise VKCardUploadError('jpeg_conversion', type(exc).__name__) from None
    if not result.startswith(b'\xff\xd8\xff') or len(result) > MAX_JPEG_BYTES:
        raise VKCardUploadError('jpeg_conversion', 'InvalidOrTooLarge')
    return result


def _validated_png(image_bytes: bytes) -> bytes:
    image = _validated_image(image_bytes)
    try:
        with BytesIO() as stream:
            image.save(stream, format='PNG', optimize=False)
            png = stream.getvalue()
    except (OSError, ValueError, TypeError) as exc:
        raise VKCardUploadError('png_conversion', type(exc).__name__) from None
    if len(png) > MAX_JPEG_BYTES:
        raise VKCardUploadError('png_conversion', 'TooLarge')
    return png


def _should_try_document(exc: VKCardUploadError) -> bool:
    """Only known photo API/protocol failures merit a second VK API route."""
    if exc.stage not in ('vk_photo_upload', 'vk_photo_result'):
        return False
    return (exc.exception_type in ('ValueError', 'JSONDecodeError')
            or exc.api_code == 100
            or exc.exception_type == 'VKAPIError_100'
            or exc.stage == 'vk_photo_result')


async def upload_vk_card(api, peer_id: int, image_bytes: bytes) -> str:
    """Return a verified photo... or doc... VK attachment for a *single* send.

    KVERATO_IMAGE_UPLOAD_FORMAT=auto|jpeg|png controls the photo path.
    KVERATO_IMAGE_DOCUMENT_FALLBACK=1 (default) enables a PNG document backup
    after particular VK photo protocol failures. Neither path sends a message.
    All media/temporary paths are isolated and deleted on exit.
    """
    if not isinstance(peer_id, int) or isinstance(peer_id, bool) or peer_id <= 0:
        raise VKCardUploadError('peer_validation', 'PeerID')
    mode = os.getenv('KVERATO_IMAGE_UPLOAD_FORMAT', 'auto').strip().lower()
    if mode not in ('auto', 'jpeg', 'png'):
        raise VKCardUploadError('configuration', 'InvalidImageUploadFormat')
    fallback = os.getenv('KVERATO_IMAGE_DOCUMENT_FALLBACK', '1').strip()
    if fallback not in ('0', '1'):
        raise VKCardUploadError('configuration', 'InvalidDocumentFallback')
    # Validate before any network operation. Convert once for the initial photo.
    prepared_image = (_validated_png(image_bytes) if mode == 'png'
                      else card_to_jpeg(image_bytes))
    try:
        from vkbottle import PhotoMessageUploader
    except (ImportError, AttributeError) as exc:
        raise VKCardUploadError('uploader_initialization', type(exc).__name__) from None

    try:
        with TemporaryDirectory(prefix='kverato-photo-') as folder:
            uploader = PhotoMessageUploader(api)

            async def _photo_upload(blob: bytes, suffix: str) -> str:
                filename = Path(folder) / f'card.{suffix}'
                filename.write_bytes(blob)
                try:
                    result = await uploader.upload(file_source=str(filename), peer_id=peer_id)
                except Exception as exc:
                    raise VKCardUploadError('vk_photo_upload', type(exc).__name__,
                                            _safe_vk_error_code(exc)) from None
                if not isinstance(result, str) or not PHOTO_ATTACHMENT.fullmatch(result):
                    raise VKCardUploadError('vk_photo_result', 'InvalidAttachment')
                return result

            try:
                if mode == 'png':
                    return await _photo_upload(prepared_image, 'png')
                try:
                    return await _photo_upload(prepared_image, 'jpg')
                except VKCardUploadError as exc:
                    if (mode != 'auto' or exc.stage != 'vk_photo_upload'
                            or exc.exception_type != 'ValueError'):
                        raise
                    # A PNG retry never sends a message. Preserve existing
                    # behavior for generic ValueError from JPEG upload.
                    return await _photo_upload(_validated_png(image_bytes), 'png')
            except VKCardUploadError as photo_error:
                if fallback != '1' or not _should_try_document(photo_error):
                    raise

                # Independent docs.getMessagesUploadServer/docs.save route.
                # The current VKBottle exposes DocMessagesUploader in v4.x.
                # Older/incomplete installations keep the original error.
                try:
                    from vkbottle import DocMessagesUploader
                except (ImportError, AttributeError):
                    raise photo_error from None
                try:
                    png = _validated_png(image_bytes)
                except VKCardUploadError as exc:
                    raise VKCardUploadError(exc.stage, exc.exception_type,
                                            exc.api_code, photo_error=photo_error) from None
                filename = Path(folder) / 'kverato-card.png'
                filename.write_bytes(png)
                try:
                    result = await DocMessagesUploader(api).upload(
                        file_source=str(filename), peer_id=peer_id)
                except Exception as exc:
                    raise VKCardUploadError('vk_doc_upload', type(exc).__name__,
                                            _safe_vk_error_code(exc),
                                            photo_error=photo_error) from None
                if not isinstance(result, str) or not DOC_ATTACHMENT.fullmatch(result):
                    raise VKCardUploadError('vk_doc_result', 'InvalidAttachment',
                                            photo_error=photo_error)
                return result
    except VKCardUploadError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        raise VKCardUploadError('temporary_file', type(exc).__name__) from None
