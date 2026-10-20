import os
from pathlib import Path
import tempfile
from typing import Callable

from ...Abstract.Export.I_TexExporter import I_TexExporter
from ...Abstract.History import D_ChatMessage, I_HistoryRepository
from ...errors import TexExportWarning
from .C_AssetBundle import C_AssetBundle
from .C_TexDocumentRenderer import C_TexDocumentRenderer


class C_TexExporter(I_TexExporter):
    def __init__(
        self,
        *,
        document_renderer: C_TexDocumentRenderer | None = None,
        asset_bundle_type: type[C_AssetBundle] = C_AssetBundle,
    ) -> None:
        self.document_renderer = document_renderer if document_renderer is not None else C_TexDocumentRenderer()
        self.asset_bundle_type = asset_bundle_type

    def exportToTex(
        self,
        repository: I_HistoryRepository,
        chat_id: str,
        output_path: str | os.PathLike,
        *,
        overwrite: bool = False,
    ) -> Path:
        destination = self.validateDestination(output_path, repository.codex_home, overwrite)
        chat = repository.getChatExport(chat_id)
        if destination in chat.protected:
            raise ValueError("The TeX destination cannot replace a Codex database or history journal")
        return self.exportDocument(
            destination, chat.title, chat.messages, chat.created_at, chat.modified_at,
            overwrite=overwrite, bases=chat.bases, protected=chat.protected,
            message_timestamps=chat.message_timestamps,
            warn=repository.reportWarning,
            render_warn=lambda text: repository.reportWarning(text, category=TexExportWarning),
        )

    def exportDocument(
        self,
        destination: Path,
        title: str,
        messages: list[D_ChatMessage],
        created_at: float | None,
        modified_at: float | None,
        *,
        overwrite: bool = False,
        bases: tuple[Path, ...] = (),
        protected: tuple[Path, ...] = (),
        message_timestamps: tuple[float | None, ...] = (),
        warn: Callable[[str], None] | None = None,
        render_warn: Callable[[str], None] | None = None,
    ) -> Path:
        destination = Path(destination).expanduser().resolve()
        if destination.suffix.lower() != ".tex":
            raise ValueError("destination must end with .tex")
        if any(destination == path.resolve() or destination.is_relative_to(path.resolve()) for path in protected):
            raise ValueError("The TeX destination cannot replace protected history files")
        if destination.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}; pass overwrite=True explicitly")
        with tempfile.TemporaryDirectory(prefix="codex-history-assets-") as folder:
            bundle = self.asset_bundle_type(Path(folder), destination, bases=bases, protected=protected, warn=warn)
            source = self.document_renderer.render(title, messages, created_at, modified_at, assets=bundle, warn=render_warn, message_timestamps=message_timestamps)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                bundle.publish()
                self.writeDocument(destination, source, overwrite=overwrite)
            except BaseException:
                bundle.rollback()
                raise
        return destination

    @staticmethod
    def validateDestination(output_path: str | os.PathLike, home: Path, overwrite: bool) -> Path:
        destination = Path(output_path).expanduser().resolve()
        if destination.suffix.lower() != ".tex":
            raise ValueError("output_path must end with .tex")
        if destination.is_relative_to(home.resolve()):
            raise ValueError("The TeX destination must be outside the read-only Codex data directory")
        if destination.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}; pass overwrite=True explicitly")
        return destination

    @staticmethod
    def writeDocument(destination: Path, source: str, *, overwrite: bool) -> None:
        if not overwrite:
            stream = destination.open("x", encoding="utf-8", newline="\n")
            try:
                with stream:
                    stream.write(source)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        else:
            pending = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination.parent, prefix=".codex-export-", suffix=".tmp", delete=False) as stream:
                    pending = Path(stream.name)
                    stream.write(source)
                os.replace(pending, destination)
            finally:
                if pending is not None:
                    pending.unlink(missing_ok=True)
