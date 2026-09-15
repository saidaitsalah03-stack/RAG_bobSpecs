#!/usr/bin/env python3

import asyncio
import os
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CRASH_LOG = BASE / "mcp_crash.log"
PERSIST_DIR = str(BASE / "chroma_db_bornflow")
COLLECTION = "bornflow_specs"
MODEL_NAME = "intfloat/multilingual-e5-small"


def _log_crash(stage, exc):
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 70}\n[{datetime.now().isoformat()}] {stage}\n")
            f.write(f"cwd={os.getcwd()}\nexe={sys.executable}\n")
            traceback.print_exc(file=f)
    except Exception:
        pass


try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
except (KeyboardInterrupt, SystemExit):
        raise    
except BaseException as e:
    _log_crash("imports", e)
    sys.exit(1)


class E5Embeddings(HuggingFaceEmbeddings):
    """Doit être STRICTEMENT identique à celle de ingestion.py : si les
    préfixes diffèrent entre indexation et interrogation, les vecteurs ne
    sont plus dans le même espace et la recherche renvoie n'importe quoi."""

    def embed_documents(self, texts):
        return super().embed_documents([f"passage: {t}" for t in texts])

    def embed_query(self, text):
        return super().embed_query(f"query: {text}")


app = Server("BornFlow_Specs_Server")
VECTORSTORE = None


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="rechercher_regles_bornflow",
            description=(
                "Purpose: recherche sémantique dans les spécifications "
                "fonctionnelles détaillées (SFD) de l'application BornFlow. "
                "Retourne les règles de gestion pertinentes avec leur "
                "identifiant, leur chapitre et la version du document.\n"
                "Usage: appeler cet outil AVANT de répondre à toute question "
                "portant sur le comportement attendu de l'application, les "
                "droits d'accès, les règles métier ou les écrans. Formuler la "
                "requête en termes métier.\n"
                "Limitations: retourne l'intention DOCUMENTÉE, qui peut "
                "diverger de l'implémentation réelle. Un écart entre la "
                "spécification et le code est un constat à signaler, jamais à "
                "arbitrer silencieusement. Vérifier le champ 'version' : une "
                "spécification peut être périmée."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Question ou mots-clés "
                                       "(ex. « Qui peut supprimer un ordre ? »)",
                    },
                    "k": {"type": "integer", "default": 3,
                          "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="lire_regle_bornflow",
            description=(
                "Purpose: retourne une règle de gestion précise à partir de "
                "son identifiant exact (ex. BF100-RDG-011).\n"
                "Usage: appeler quand l'identifiant est déjà connu, "
                "typiquement après rechercher_regles_bornflow ou lorsqu'une "
                "règle en cite une autre.\n"
                "Limitations: correspondance exacte uniquement ; retourne une "
                "erreur si l'identifiant n'existe pas dans le corpus indexé."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rdg_id": {"type": "string",
                               "description": "Identifiant, ex. « BF100-RDG-011 »"}
                },
                "required": ["rdg_id"],
            },
        ),
    ]


def _format_hit(doc, score=None):
    m = doc.metadata
    out = {
        "rdg_id": m.get("rdg_id"),
        "titre": m.get("titre"),
        "chapitre": m.get("chapitre"),
        "document": m.get("document"),
        "version": m.get("version"),
        "date_maj": m.get("date_maj"),
        "texte": doc.page_content,
    }
    if score is not None:
        out["score"] = round(float(score), 4)
    return out


@app.call_tool()
async def call_tool(name, arguments):
    try:
        if name == "rechercher_regles_bornflow":
            query = (arguments or {}).get("query", "").strip()
            if not query:
                res = {"error": "Le paramètre 'query' est manquant."}
            else:
                k = int(arguments.get("k", 3))
                hits = VECTORSTORE.similarity_search_with_score(query, k=k)
                res = {
                    "query": query,
                    "n_results": len(hits),
                    "resultats": [_format_hit(d, s) for d, s in hits],
                    "avertissement": (
                        "Intention documentée. Un écart avec le code est un "
                        "constat à signaler, pas une erreur à corriger "
                        "silencieusement."
                    ),
                }

        elif name == "lire_regle_bornflow":
            rid = (arguments or {}).get("rdg_id", "").strip().upper()
            got = VECTORSTORE.get(ids=[rid])
            if got and got.get("ids"):
                res = {
                    "rdg_id": rid,
                    **{k: v for k, v in got["metadatas"][0].items()},
                    "texte": got["documents"][0],
                }
            else:
                res = {"error": f"Règle inconnue : {rid}"}

        else:
            res = {"error": f"Outil inconnu : {name}"}

    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        _log_crash(f"call_tool({name})", e)
        res = {"error": str(e), "tool": name}

    return [TextContent(type="text",
                        text=json.dumps(res, indent=2, ensure_ascii=False))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream,
                      app.create_initialization_options())


if __name__ == "__main__":
    try:
        embeddings = E5Embeddings(model_name=MODEL_NAME)
        VECTORSTORE = Chroma(
            persist_directory=PERSIST_DIR,
            collection_name=COLLECTION,
            embedding_function=embeddings,
        )
        if VECTORSTORE._collection.count() == 0:
            raise RuntimeError(
                f"La collection '{COLLECTION}' est vide. "
                f"Exécuter d'abord : python ingestion.py")
    except (KeyboardInterrupt, SystemExit):
        raise

    except BaseException as e:
        _log_crash("initialisation (modèle / ChromaDB)", e)
        sys.exit(1)

    for s in ("stdout", "stderr"):
        st = getattr(sys, s, None)
        if st is not None and hasattr(st, "reconfigure"):
            try:
                st.reconfigure(encoding="utf-8")
            except Exception:
                pass

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass   
    except BaseException as e:
        _log_crash("asyncio.run", e)
        sys.exit(1)
