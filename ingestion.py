#!/usr/bin/env python3
"""
ingestion.py — Indexation des SFD BornFlow dans ChromaDB.

À exécuter UNE SEULE FOIS (ou après modification des SFD).

    python ingestion.py

Écarts assumés par rapport au guide initial, chacun motivé :

1. CHUNKING PAR RÈGLE plutôt que RecursiveCharacterTextSplitter.
   Un découpage à 500 caractères coupe les règles au milieu : la moitié
   d'une règle de gestion ne veut rien dire, et les métadonnées
   (identifiant, chapitre) sont perdues. Le document expose déjà ses
   frontières sémantiques via ses tableaux — on les utilise.

2. MÉTADONNÉES CONSERVÉES (rdg_id, version, chapitre, document).
   Sans elles, l'agent ne peut ni citer l'identifiant de la règle, ni
   signaler qu'une spécification est périmée.

3. PAS D'APPEL À vectorstore.persist().
   Cette méthode a été supprimée dans ChromaDB 1.x ; la persistance est
   automatique avec PersistentClient. Le code du guide lèverait
   AttributeError.

4. PRÉFIXES E5 ("query:" / "passage:").
   Le modèle intfloat/multilingual-e5-large est entraîné avec ces
   préfixes. Sans eux, la qualité de récupération chute nettement.
"""
import re
import shutil
from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

# --------------------------------------------------------------------------
CORPUS_DIR = Path(__file__).parent / "corpus"
PERSIST_DIR = str(Path(__file__).parent / "chroma_db_bornflow")
COLLECTION = "bornflow_specs"
MODEL_NAME = "intfloat/multilingual-e5-small"

RE_RDG = re.compile(r'\b([A-Z]{2,6}\d{0,4})-RDG-(\d+)\b')


class E5Embeddings(HuggingFaceEmbeddings):
    """Le modèle E5 attend un préfixe distinct pour les documents et les
    requêtes. LangChain ne l'ajoute pas : on surcharge les deux méthodes."""

    def embed_documents(self, texts):
        return super().embed_documents([f"passage: {t}" for t in texts])

    def embed_query(self, text):
        return super().embed_query(f"query: {text}")


def normalize_id(raw: str) -> str:
    m = RE_RDG.search(raw)
    return f"{m.group(1)}-RDG-{int(m.group(2)):03d}" if m else raw.strip()


def dedup_cells(row):
    """python-docx répète les cellules fusionnées : on déduplique."""
    out, seen = [], set()
    for c in row.cells:
        if id(c._tc) not in seen:
            seen.add(id(c._tc))
            out.append(c.text.strip())
    return out


def doc_version(doc):
    for tbl in doc.tables:
        if not tbl.rows:
            continue
        hdr = [c.text.strip().lower() for c in tbl.rows[0].cells]
        if any("version de référence" in h for h in hdr):
            rows = [dedup_cells(r) for r in tbl.rows[1:]]
            rows = [r for r in rows if len(r) >= 2 and r[1]]
            if rows:
                return rows[-1][1], rows[-1][0]
    return None, None


def extract_rules(path: Path):
    """Une règle de gestion = un chunk, avec son chemin hiérarchique."""
    doc = DocxDocument(str(path))
    version, date_maj = doc_version(doc)
    docs, hpath = [], []

    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            p = Paragraph(child, doc)
            style, text = p.style.name or "", p.text.strip()
            if style.startswith("Heading") and text:
                try:
                    lvl = int(style[-1])
                except ValueError:
                    continue
                hpath = hpath[: lvl - 1] + [text]

        elif child.tag.endswith("}tbl"):
            tbl = Table(child, doc)
            if not tbl.rows:
                continue
            hdr = [c.text.strip().lower() for c in tbl.rows[0].cells]
            if "identifiant" not in hdr[0]:
                continue
            # Le récapitulatif d'annexe reprend tous les identifiants avec
            # l'en-tête « Intitulé de la règle » : l'indexer créerait un
            # doublon par règle.
            if not any("description" in h for h in hdr[1:]):
                continue

            for row in tbl.rows[1:]:
                cells = dedup_cells(row)
                if len(cells) < 2 or not RE_RDG.search(cells[0]):
                    continue
                body = max(cells[1:], key=len).strip()
                if not body:
                    continue
                lines = [l.strip() for l in body.split("\n") if l.strip()]
                titre = lines[0] if lines else ""
                rid = normalize_id(cells[0])

                # Le texte vectorisé inclut l'identifiant et le chapitre :
                # c'est ce qui permet de retrouver une règle citée par son
                # numéro autant que par son sens.
                page_content = (
                    f"{rid} — {titre}\n"
                    f"Chapitre : {' > '.join(hpath)}\n\n"
                    f"{body}"
                )
                docs.append(Document(
                    page_content=page_content,
                    metadata={
                        "rdg_id": rid,
                        "titre": titre,
                        "document": path.stem,
                        "version": version or "n/a",
                        "date_maj": date_maj or "n/a",
                        "chapitre": " > ".join(hpath),
                    },
                ))
    return docs


def main():
    files = sorted(CORPUS_DIR.glob("*.docx"))
    if not files:
        raise SystemExit(f"Aucun .docx dans {CORPUS_DIR}")

    all_docs = []
    for f in files:
        d = extract_rules(f)
        print(f"  {f.name:50} {len(d):3} règles")
        all_docs.extend(d)

    ids = [d.metadata["rdg_id"] for d in all_docs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        print(f"  ATTENTION — identifiants en doublon : {sorted(dupes)[:5]}")

    print(f"\nChargement du modèle {MODEL_NAME} (premier lancement : téléchargement ~2 Go)...")
    embeddings = E5Embeddings(model_name=MODEL_NAME)

    # Réindexation propre : sinon les anciens vecteurs cohabitent avec les
    # nouveaux et la recherche renvoie des règles supprimées.
    if Path(PERSIST_DIR).exists():
        shutil.rmtree(PERSIST_DIR)

    print("Vectorisation et indexation...")
    Chroma.from_documents(
        documents=all_docs,
        embedding=embeddings,
        persist_directory=PERSIST_DIR,
        collection_name=COLLECTION,
        ids=ids,                       # l'identifiant de règle sert de clé
    )
    # Pas de .persist() : supprimé dans ChromaDB 1.x, persistance automatique.

    print(f"\nIndexation terminée — {len(all_docs)} règles dans {PERSIST_DIR}")


if __name__ == "__main__":
    main()
