from .ConcreteLocal.Export.C_MarkdownLatexRenderer import (
    C_MarkdownLatexRenderer as MarkdownLatexRenderer,
    PREAMBLE,
    math_source,
    prepare_math,
    tex_escape,
    validate_text,
    wrappable_text,
)
from .ConcreteLocal.Export.C_TexDocumentRenderer import C_TexDocumentRenderer
from .ConcreteLocal.Export.C_TexExporter import C_TexExporter


local_time_text = C_TexDocumentRenderer.formatTimestamp
render_document = C_TexDocumentRenderer().render
export_destination = C_TexExporter.validateDestination
export_document = C_TexExporter().exportDocument
write_document = C_TexExporter.writeDocument
