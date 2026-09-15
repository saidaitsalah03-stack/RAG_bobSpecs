# BornFlow RAG — intégration ChromaDB + MCP avec BOB AI

Architecture : SFD (.docx) → chunking par règle → embeddings E5 → ChromaDB →
serveur MCP → BOB AI.

---

## Étape 1 — Environnement

```powershell
cd C:\Users\CGI\Documents\borneflow\tools
mkdir bornflow-rag ; cd bornflow-rag

python -m venv venv_bornflow
.\venv_bornflow\Scripts\activate

pip install python-docx langchain-core langchain-chroma langchain-huggingface `
            sentence-transformers chromadb "mcp==1.27.2"


## Étape 2 — Déposer les SFD et indexer

```powershell
mkdir corpus
copy ..\..\docs\SFD_BF100_BornFlow_V1_0.docx corpus\

python ingestion.py
```

Sortie attendue :

```
  SFD_BF100_BornFlow_V1_0.docx                        44 règles
Chargement du modèle intfloat/multilingual-e5-large (premier lancement : téléchargement ~2 Go)...
Vectorisation et indexation...
Indexation terminée — 44 règles dans .../chroma_db_bornflow
```

**Le premier lancement télécharge ~2,2 Go.** Prévois le temps et l'espace
disque. 



## Étape 3 — Test manuel du serveur

```powershell
python mcp_server.py
```

Le terminal reste **silencieux, sans rendre la main**. C'est le comportement
normal d'un serveur MCP stdio : il attend une connexion. `Ctrl+C` pour sortir.

S'il rend la main immédiatement, ouvre `mcp_crash.log` — il contient les logs complet

---

## Étape 4 — Déclaration dans BOB AI

Dans `.bob/mcp.json`, **ajouter sans toucher aux serveurs existants** :

```json
{
  "mcpServers": {


    "BornFlowSpecs": {
      "command": "..../bornflow-rag/venv_bornflow/Scripts/python.exe",
      "args": [".../bornflow-rag/mcp_server.py"]
    }
  }
}
```


## Étape 5 — Prompt système

Dans les règles projet (`.bob/rules/`) ou le prompt système de l'agent :

```
Pour toute question portant sur le comportement attendu de BornFlow — règles
métier, droits d'accès, écrans, flux d'intégration — tu DOIS d'abord appeler
l'outil `rechercher_regles_bornflow`.

Cite systématiquement l'identifiant de la règle sur laquelle tu t'appuies
(exemple : « Selon BF100-RDG-011, la suppression est réservée… »).

Si le comportement observé dans le code diverge de la règle de gestion,
SIGNALE la divergence en citant les deux. Ne tranche jamais silencieusement
en faveur de l'un ou de l'autre : la spécification peut être périmée autant
que le code peut être fautif.
```

Le dernier paragraphe est le plus important : sans lui, l'agent résoudra les
écarts en silence au lieu de les remonter — et tu perdras la capacité la plus
utile du système.

---
