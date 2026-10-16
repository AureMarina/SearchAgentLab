"""Explicit, optional installation of the original official UI font.

The font is not a package asset. Only ``setup_font`` can access the network;
viewer reads use a fixed workspace path and accept the audited original bytes.
This module never opens a database or changes evaluation records.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .core import ValidationError

FONT_FAMILY = "仓耳玉楷"
FONT_CSS_FAMILY = "TsangerYuKai"
FONT_FILENAME = "TsangerYuKai-Regular.ttf"
FONT_URL = "/fonts/" + FONT_FILENAME
FONT_SHA256 = "3402a3a595edceb5f0cb69143be236807cff5d74382c3816635354f23e4e1a16"
FONT_BYTES = 4927040
OFFICIAL_PAGE = "https://tsanger.cn/product/362"
OFFICIAL_DOWNLOAD = "https://tsanger.cn/download/%E4%BB%93%E8%80%B3%E7%8E%89%E6%A5%B7.ttf"
FALLBACK_HELP = (
    "查看器仍可继续使用本机字体。联网后重试 setup-font，"
    "或使用 --font-file 指定官方原始 TTF；不会修改任何评测数据。"
)


class FontSetupError(ValidationError):
    """Optional font installation failed without affecting the viewer."""


def _paths(data_dir: str | Path) -> tuple[Path, Path, Path]:
    root = Path(data_dir).expanduser().resolve()
    folder = root / "ui-fonts"
    return folder, folder / FONT_FILENAME, folder / "font.json"


def _font_bytes(data_dir: str | Path) -> tuple[str, bytes | None]:
    """Return verified bytes without following font-directory/file symlinks."""
    folder, path, _ = _paths(data_dir)
    try:
        if folder.is_symlink() or path.is_symlink():
            return "invalid_file", None
        if not path.exists():
            return "not_installed", None
        if not path.is_file() or path.stat().st_size != FONT_BYTES:
            return "invalid_file", None
        data = path.read_bytes()
        if len(data) != FONT_BYTES or hashlib.sha256(data).hexdigest() != FONT_SHA256:
            return "invalid_file", None
        return "ready", data
    except OSError:
        return "invalid_file", None


def font_bytes(data_dir: str | Path) -> bytes | None:
    """Fixed local HTTP resource; unavailable or corrupt fonts are not served."""
    return _font_bytes(data_dir)[1]


def font_status(data_dir: str | Path) -> dict:
    status, data = _font_bytes(data_dir)
    available = data is not None
    source = None
    if available:
        source = "verified_existing"
        _, _, metadata = _paths(data_dir)
        try:
            if not metadata.is_symlink() and metadata.is_file() and metadata.stat().st_size <= 4096:
                saved = json.loads(metadata.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and saved.get("sha256") == FONT_SHA256:
                    if saved.get("source") in ("official_download", "local_file"):
                        source = saved["source"]
        except (OSError, ValueError, UnicodeError):
            pass
    return {
        "available": available,
        "family": FONT_FAMILY,
        "css_family": FONT_CSS_FAMILY,
        "url": FONT_URL if available else None,
        "sha256": FONT_SHA256 if available else None,
        "source": source,
        "official_page": OFFICIAL_PAGE,
        "status": status,
    }


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".font-", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup_font(data_dir: str | Path, font_file: str | Path | None = None, timeout: float = 20) -> dict:
    """Install verified original bytes once, independently of workspace databases.

    A supplied file must match the same audited official TTF. This prevents
    silently presenting a different font as 仓耳玉楷 and preserves original bytes.
    """
    current = font_status(data_dir)
    if current["available"]:
        return current | {"status": "already_installed", "changed": False}
    try:
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise FontSetupError("--timeout 必须大于 0 且不超过 60 秒。 " + FALLBACK_HELP)
        folder, path, metadata = _paths(data_dir)
        if folder.is_symlink() or path.is_symlink() or metadata.is_symlink():
            raise FontSetupError("字体安装目录或固定文件不能是符号链接。 " + FALLBACK_HELP)
        if font_file is not None:
            source_path = Path(font_file).expanduser()
            if not source_path.is_file() or source_path.stat().st_size != FONT_BYTES:
                raise FontSetupError("--font-file 不是已核验的仓耳玉楷官方原始 TTF。 " + FALLBACK_HELP)
            data = source_path.read_bytes()
            source = "local_file"
        else:
            request = Request(OFFICIAL_DOWNLOAD, headers={"User-Agent": "SearchAgentLab-font-setup/1"})
            with urlopen(request, timeout=timeout) as response:
                actual = urlsplit(response.geturl())
                if actual.scheme != "https" or actual.hostname not in ("tsanger.cn", "www.tsanger.cn"):
                    raise FontSetupError("官方下载跳转到未核验来源，未安装字体。 " + FALLBACK_HELP)
                data = response.read(FONT_BYTES + 1)
            source = "official_download"
        if len(data) != FONT_BYTES or hashlib.sha256(data).hexdigest() != FONT_SHA256:
            raise FontSetupError("字体与已核验的官方原始文件 SHA256 不匹配，未安装。 " + FALLBACK_HELP)
        folder.mkdir(parents=True, exist_ok=True)
        # Check again after any network wait before writing fixed local paths.
        if folder.is_symlink() or path.is_symlink() or metadata.is_symlink():
            raise FontSetupError("字体安装目录或固定文件不能是符号链接。 " + FALLBACK_HELP)
        saved = {
            "family": FONT_FAMILY,
            "css_family": FONT_CSS_FAMILY,
            "sha256": FONT_SHA256,
            "source": source,
            "official_page": OFFICIAL_PAGE,
            "download_url": OFFICIAL_DOWNLOAD if source == "official_download" else None,
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write(path, data)
        _atomic_write(metadata, (json.dumps(saved, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return font_status(data_dir) | {"status": "installed", "changed": True}
    except FontSetupError:
        raise
    except (OSError, URLError, HTTPException, ValueError) as exc:
        raise FontSetupError("字体安装未完成：" + str(exc) + "。 " + FALLBACK_HELP) from exc
