"""Contract ingestion: markdown source -> PDF -> extracted clauses -> vector store.

The PDFs are generated rather than committed as binaries, but the retrieval
path is the real one: clauses are parsed back out of the rendered PDF with
pypdf, so the pipeline exercises PDF extraction exactly as it would against
customer-supplied contract documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dra.rag.store import Chunk, RetrievedChunk, get_vector_store
from dra.settings import PACKAGE_ROOT, get_settings

SOURCE_DIR = PACKAGE_ROOT / "rag" / "contract_sources"

_HEADING = re.compile(r"^#{0,6}\s*(\d+\.\d+)\s+(.{3,90})$")


@dataclass
class ContractClause:
    contract_number: str
    customer_code: str
    customer_name: str
    clause_ref: str
    clause_title: str
    text: str
    source_pdf: str
    page: int

    @property
    def chunk_id(self) -> str:
        return f"{self.contract_number}:{self.clause_ref}"

    def to_chunk(self) -> Chunk:
        header = f"{self.contract_number} clause {self.clause_ref} — {self.clause_title}"
        return Chunk(
            id=self.chunk_id,
            text=f"{header}\n\n{self.text}",
            metadata={
                "contract_number": self.contract_number,
                "customer_code": self.customer_code,
                "customer_name": self.customer_name,
                "clause_ref": self.clause_ref,
                "clause_title": self.clause_title,
                "source_pdf": self.source_pdf,
                "page": self.page,
            },
        )


@dataclass
class ContractDocument:
    meta: dict[str, str]
    body: str
    source_path: Path

    @property
    def contract_number(self) -> str:
        return self.meta["contract_number"]

    @property
    def pdf_name(self) -> str:
        return f"{self.contract_number}.pdf"


def load_sources(source_dir: Path | None = None) -> list[ContractDocument]:
    docs: list[ContractDocument] = []
    for path in sorted((source_dir or SOURCE_DIR).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        header, _, body = raw.partition("\n---\n")
        meta: dict[str, str] = {}
        for line in header.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        docs.append(ContractDocument(meta=meta, body=body.strip(), source_path=path))
    return docs


def render_pdf(doc: ContractDocument, out_dir: Path) -> Path:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / doc.pdf_name

    pdf = FPDF(format="letter", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    pdf.multi_cell(0, 8, doc.meta.get("title", "Agreement"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(
        0,
        5,
        f"{doc.meta.get('customer_name', '')} and Acme Supply Co.\n"
        f"Contract {doc.contract_number} | effective "
        f"{doc.meta.get('effective_date', '')} to {doc.meta.get('expiry_date', '')} | "
        f"governed by the laws of the {doc.meta.get('governing_law', 'State of Illinois')}",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(4)

    for line in doc.body.splitlines():
        stripped = line.strip()
        if not stripped:
            pdf.ln(3)
            continue
        heading = _HEADING.match(stripped)
        if heading:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(
                0,
                6,
                f"{heading.group(1)} {heading.group(2).strip()}",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.set_font("Helvetica", "", 10)
            continue
        pdf.multi_cell(0, 5, stripped, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.output(str(target))
    return target


def extract_clauses(doc: ContractDocument, pdf_path: Path) -> list[ContractClause]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    clauses: list[ContractClause] = []
    current: ContractClause | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current is not None:
            body = re.sub(r"\s+", " ", " ".join(buffer)).strip()
            if body:
                clauses.append(
                    ContractClause(
                        contract_number=current.contract_number,
                        customer_code=current.customer_code,
                        customer_name=current.customer_name,
                        clause_ref=current.clause_ref,
                        clause_title=current.clause_title,
                        text=body,
                        source_pdf=current.source_pdf,
                        page=current.page,
                    )
                )
        current = None
        buffer = []

    for page_no, page in enumerate(reader.pages, start=1):
        for raw_line in (page.extract_text() or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            heading = _HEADING.match(line)
            if heading:
                flush()
                current = ContractClause(
                    contract_number=doc.contract_number,
                    customer_code=doc.meta.get("customer_code", ""),
                    customer_name=doc.meta.get("customer_name", ""),
                    clause_ref=heading.group(1),
                    clause_title=heading.group(2).strip(),
                    text="",
                    source_pdf=pdf_path.name,
                    page=page_no,
                )
            elif current is not None:
                buffer.append(line)
    flush()
    return clauses


def build_contract_index(reset: bool = True) -> dict[str, object]:
    """Render the PDFs, parse them back, and (re)index every clause."""
    settings = get_settings()
    settings.ensure_dirs()
    store = get_vector_store()
    if reset:
        store.reset()

    docs = load_sources()
    all_clauses: list[ContractClause] = []
    pdfs: list[str] = []
    for doc in docs:
        pdf_path = render_pdf(doc, settings.contracts_pdf_dir)
        pdfs.append(pdf_path.name)
        all_clauses.extend(extract_clauses(doc, pdf_path))

    store.add([c.to_chunk() for c in all_clauses])
    return {
        "backend": store.backend,
        "contracts": len(docs),
        "pdfs": pdfs,
        "clauses": len(all_clauses),
        "pdf_dir": str(settings.contracts_pdf_dir),
    }


# Terms that make a clause authoritative for a given dispute reason. Used to
# rerank semantically similar clauses so the citation is the operative one.
REASON_ANCHORS: dict[str, tuple[str, ...]] = {
    "damaged_goods": ("damage", "damaged", "concealed", "proof of delivery", "exception"),
    "short_shipment": ("shortage", "short shipment", "quantity", "packing list"),
    # Deliberately excludes "unauthorized"/"deduction": those appear in the
    # generic set-off clause, and the operative term here is the one that says
    # when the discount is actually earned.
    "unauthorized_discount": ("early payment", "discount", "cleared funds"),
    "pricing_discrepancy": ("price", "prices", "price list", "rebate"),
    "duplicate_billing": ("deduct", "set-off", "offset", "credit memorandum"),
    "service_quality": ("return", "complaint", "delivery", "escalat"),
    "other": ("deduct", "set-off", "credit memorandum"),
}


def retrieve_clauses(
    query: str,
    customer_code: str | None = None,
    reason_code: str | None = None,
    k: int | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    store = get_vector_store()
    top_k = k or settings.rag_top_k
    where = {"customer_code": customer_code} if customer_code else None
    hits = store.query(query, k=max(top_k, 6), where=where)

    anchors = REASON_ANCHORS.get(reason_code or "", ())
    if anchors:
        def rerank(chunk: RetrievedChunk) -> float:
            lowered = chunk.text.lower()
            overlap = sum(1 for anchor in anchors if anchor in lowered)
            return chunk.score + 0.08 * overlap

        hits = sorted(hits, key=rerank, reverse=True)
    return hits[:top_k]
