from dataclasses import dataclass
import base64
import binascii
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
from typing import Callable
from urllib.parse import quote, unquote, urlsplit
import warnings

from ...errors import HistoryReadWarning, TexExportError
from ...Abstract.History.D_ChatMessage import D_ChatMessage


NATIVE_IMAGES = {".png", ".jpg", ".jpeg", ".pdf"}


def remote_reference(reference: str) -> bool:
    return reference.lower().startswith(("http://", "https://", "mailto:"))


@dataclass(frozen=True)
class D_Asset:
    filename: str
    staged: Path
    digest: str


class C_AssetBundle:
    def __init__(
        self,
        staging: Path,
        destination: Path,
        *,
        bases: tuple[Path, ...] = (),
        protected: tuple[Path, ...] = (),
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.staging = staging
        self.destination = destination
        self.bases = tuple(dict.fromkeys(path.expanduser().resolve() for path in bases))
        self.protected = tuple(path.resolve() for path in protected)
        self.warn_callback = warn
        self.assets: dict[Path | str, D_Asset | None] = {}
        self.notices: set[str] = set()
        self.created: list[Path] = []

    @property
    def files(self) -> dict[str, Path]:
        return {asset.filename: asset.staged for asset in self.assets.values() if asset is not None}

    def warn(self, text: str) -> None:
        if text in self.notices:
            return
        self.notices.add(text)
        if self.warn_callback:
            self.warn_callback(text)
        else:
            warnings.warn(text, HistoryReadWarning, stacklevel=3)

    @staticmethod
    def _withoutLocation(reference: str) -> str:
        match = re.search(r"(?:#L\d+(?:C\d+)?(?:-L?\d+(?:C\d+)?)?|(?::|%3a)\d+(?:(?::|%3a)\d+)?)$", reference, flags=re.I)
        if match is None:
            return reference
        decoded = unquote(reference)
        if decoded.startswith(("\\\\", "//")):
            return reference
        windows_location = os.name == "nt" and re.search(r":\d+(?::\d+)?$", decoded) is not None
        if windows_location or not Path(decoded).expanduser().is_file():
            return reference[:match.start()]
        return reference

    def local_path(self, reference: str) -> Path | None:
        raw = reference.strip()
        if remote_reference(raw):
            return None
        if not raw.lower().startswith("file:"):
            raw = self._withoutLocation(raw)
        if raw.lower().startswith("file:"):
            parsed = urlsplit(raw)
            if parsed.netloc not in ("", "localhost"):
                self.warn("Network file references are not copied: " + reference)
                return None
            raw = unquote(parsed.path)
            if re.match(r"^/[A-Za-z]:/", raw):
                raw = raw[1:]
        elif not PureWindowsPath(raw).is_absolute():
            parsed = urlsplit(raw)
            if parsed.scheme:
                self.warn("Unsupported attachment reference: " + reference)
                return None
            raw = unquote(parsed.path)
        else:
            raw = unquote(raw)
        if raw.startswith(("\\\\", "//")):
            self.warn("Network file references are not copied: " + reference)
            return None
        raw = self._withoutLocation(raw)
        path = Path(raw).expanduser()
        if PureWindowsPath(raw).is_absolute() and not path.is_absolute():
            self.warn("Attachment belongs to another operating system: " + reference)
            return None
        if path.is_absolute():
            return path.resolve()
        candidates = {candidate.resolve() for base in self.bases if (candidate := base / path).is_file()}
        if len(candidates) == 1:
            return candidates.pop()
        reason = "Ambiguous relative attachment" if candidates else "Cannot locate relative attachment without its original working directory"
        self.warn(reason + ": " + reference)
        return None

    def add(self, reference: str) -> D_Asset | None:
        if reference.lower().startswith("data:"):
            return self.embedded_image(unquote(reference))
        try:
            source = self.local_path(reference)
        except (OSError, ValueError) as error:
            raise TexExportError("Cannot resolve a local attachment: " + reference) from error
        if source is None:
            return None
        if source in self.assets:
            return self.assets[source]
        if source == self.destination or any(source == path or source.is_relative_to(path) for path in self.protected):
            self.warn("Protected history or export file was not copied: " + str(source))
            self.assets[source] = None
            return None
        if not source.is_file():
            self.warn("Local attachment is missing or is not a regular file: " + str(source))
            self.assets[source] = None
            return None
        pending = self.staging / "pending-attachment"
        try:
            before = source.stat()
            digest = hashlib.sha256()
            with source.open("rb") as original, pending.open("xb") as copy:
                while chunk := original.read(1024 * 1024):
                    digest.update(chunk)
                    copy.write(chunk)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise TexExportError("An attachment changed while being copied: " + str(source))
            stem = re.sub(r"[^A-Za-z0-9_-]", "_", source.stem).strip("_").lower()[:64] or "attachment"
            suffix = source.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
                suffix = ".bin"
            checksum = digest.hexdigest()
            filename = "asset-" + stem + "-" + checksum[:16] + suffix
            staged = self.staging / filename
            if not staged.exists():
                pending.replace(staged)
            asset = D_Asset(filename, staged, checksum)
            self.assets[source] = asset
            return asset
        except OSError as error:
            raise TexExportError("Cannot read or stage a local attachment: " + str(source)) from error
        finally:
            pending.unlink(missing_ok=True)

    def embedded_image(self, reference: str) -> D_Asset:
        key = "embedded:" + hashlib.sha256(reference.encode("utf-8")).hexdigest()
        if key in self.assets:
            return self.assets[key]
        match = re.fullmatch(r"data:image/(png|jpeg|gif|webp|bmp|tiff);base64,([A-Za-z0-9+/=\s]+)", reference, flags=re.I)
        if match is None:
            raise TexExportError("Only supported base64 image data URLs may be exported as embedded assets")
        try:
            content = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
        except (ValueError, binascii.Error) as error:
            raise TexExportError("An embedded image contains invalid base64 data") from error
        digest = hashlib.sha256(content).hexdigest()
        suffix = ".jpg" if match.group(1).lower() == "jpeg" else "." + match.group(1).lower()
        filename = "asset-image-" + digest[:16] + suffix
        staged = self.staging / filename
        if not staged.exists():
            staged.write_bytes(content)
        asset = D_Asset(filename, staged, digest)
        self.assets[key] = asset
        return asset

    def publish(self) -> None:
        for filename, staged in self.files.items():
            target = self.destination.parent / filename
            if target == self.destination or target.is_symlink():
                raise TexExportError("Attachment destination is reserved or is a symbolic link: " + str(target))
            try:
                with target.open("xb") as output:
                    self.created.append(target)
                    with staged.open("rb") as original:
                        shutil.copyfileobj(original, output)
            except FileExistsError:
                if not target.is_file() or file_digest(target) != file_digest(staged):
                    raise TexExportError("An existing attachment has different content; it was not overwritten: " + str(target))

    def rollback(self) -> None:
        for path in reversed(self.created):
            path.unlink(missing_ok=True)


def file_digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


def attachment_text(record: dict, message: D_ChatMessage) -> D_ChatMessage:
    payload = record.get("payload", {})
    references = []
    kinds = {"input_image": ("图片", True), "image": ("图片", True), "image_url": ("图片", True), "input_file": ("附件", False), "input_audio": ("音频", False), "audio": ("音频", False)}
    content = payload.get("content")
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict) or part.get("type") not in kinds:
            continue
        label, image = kinds[part["type"]]
        reference = part.get("path") or part.get("file_path") or part.get("image_url") or part.get("url")
        if isinstance(reference, dict):
            reference = reference.get("url")
        explicit_path = isinstance(part.get("path") or part.get("file_path"), str)
        if isinstance(reference, str) and (explicit_path or reference.startswith(("/", "~", "file:", "http://", "https://")) or PureWindowsPath(reference).is_absolute() or image and reference.lower().startswith("data:image/")):
            references.append((label, image, reference))
    for field, label, image in (("local_images", "图片", True), ("images", "图片", True), ("local_audio", "音频", False), ("audio", "音频", False)):
        values = payload.get(field)
        if not isinstance(values, list):
            values = [values] if values else []
        for value in values:
            reference = value.get("path") or value.get("url") if isinstance(value, dict) else value
            if isinstance(reference, str) and not reference.lower().startswith("data:"):
                references.append((label, image, reference))
    text = message["content"]
    cursor = 0
    for label, image, reference in references:
        placeholder = "[" + label + "]"
        position = text.find(placeholder, cursor)
        if position < 0:
            continue
        target = reference if reference.lower().startswith(("data:", "file:", "http://", "https://")) else quote(reference.replace("\\", "/"), safe="/:~")
        replacement = ("!" if image else "") + "[" + label + "](<" + target + ">)"
        text = text[:position] + replacement + text[position + len(placeholder):]
        cursor = position + len(replacement)
    return {"role": message["role"], "content": text}
