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
```

> **La version de `mcp` est épinglée volontairement.** L'API à décorateurs
> (`@app.list_tools()`, `@app.call_tool()`) utilisée ici existe en **1.27.2**
> — ta version — mais a été **supprimée en 2.0.0**. Un `pip install mcp` non
> épinglé installerait la 2.0 et le serveur planterait au chargement avec
> `AttributeError: 'Server' object has no attribute 'list_tools'`.
> *Vérifié en installant les deux versions côte à côte.*

Vérification :

```powershell
python -c "import importlib.metadata as m; print(m.version('mcp'))"   # -> 1.27.2
```

---

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

⚠️ **Le premier lancement télécharge ~2,2 Go.** Prévois le temps et l'espace
disque. Le modèle est ensuite mis en cache dans `%USERPROFILE%\.cache\huggingface`.

### Contrôle avant d'aller plus loin

```powershell
python -c "from langchain_chroma import Chroma; from ingestion import E5Embeddings, PERSIST_DIR, COLLECTION, MODEL_NAME; vs=Chroma(persist_directory=PERSIST_DIR, collection_name=COLLECTION, embedding_function=E5Embeddings(model_name=MODEL_NAME)); print('règles indexées :', vs._collection.count()); [print(d.metadata['rdg_id'], '-', d.metadata['titre']) for d in vs.similarity_search('Qui peut supprimer un ordre de maintenance ?', k=3)]"
```

Tu dois voir `BF100-RDG-011` en tête. **Si ce n'est pas le cas, ne branche pas
l'agent** — le problème est dans l'indexation, pas dans MCP.

---

## Étape 3 — Test manuel du serveur

```powershell
python mcp_server.py
```

Le terminal reste **silencieux, sans rendre la main**. C'est le comportement
normal d'un serveur MCP stdio : il attend une connexion. `Ctrl+C` pour sortir.

S'il rend la main immédiatement, ouvre `mcp_crash.log` — il contient la trace
complète (import manquant, modèle introuvable, collection vide…).

---

## Étape 4 — Déclaration dans BOB AI

Dans `.bob/mcp.json`, **ajouter sans toucher aux serveurs existants** :

```json
{
  "mcpServers": {
    "borneflow":  { "...": "ne pas modifier" },
    "jira":       { "...": "ne pas modifier" },
    "github":     { "...": "ne pas modifier" },

    "BornFlowSpecs": {
      "command": "C:/Users/CGI/Documents/borneflow/tools/bornflow-rag/venv_bornflow/Scripts/python.exe",
      "args": ["C:/Users/CGI/Documents/borneflow/tools/bornflow-rag/mcp_server.py"]
    }
  }
}
```

**Deux différences importantes avec le guide d'origine :**

| Guide initial | Ici | Pourquoi |
|---|---|---|
| `"command": "python"` + `PYTHONPATH` vers site-packages | Chemin absolu vers `venv_bornflow\Scripts\python.exe` | `PYTHONPATH` ne suffit pas à activer un venv (chemins de DLL, `sys.prefix`). Pointer l'interpréteur du venv est la seule méthode fiable. |
| Chemin de script + `cwd` implicite | Chemin absolu du script, **aucun champ `cwd`** | Un chemin relatif est résolu depuis le répertoire de spawn du client, pas celui du script — cause exacte de l'échec `-32000 Connection closed` rencontré sur `borneflow-ctx`. |

Puis **redémarrer complètement VS Code** (pas seulement une nouvelle session) :
les connexions MCP se chargent au démarrage.

---

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

## Étape 6 — Test de bout en bout

Question à poser à BOB :

> Quels sont les droits nécessaires pour supprimer un ordre de maintenance ?

Réponse attendue, citant `BF100-RDG-011` et mentionnant le profil
« Gestionnaire de parc ».

**Test plus révélateur**, celui qui justifie le système :

> Compare le comportement de `MaintenanceOrderService.tryFindOrCreate()` avec
> ce que dit la spécification fonctionnelle.

L'agent devrait remonter `BF100-RDG-006` (« l'application ne doit en aucun cas
assimiler une erreur technique à une absence de résultat ») et signaler que le
code retourne `null` sur exception — une divergence que la lecture du code
seul ne permet pas de qualifier.

---

## Écarts assumés par rapport au guide initial

Tous vérifiés à l'exécution, pas supposés.

| # | Point | Effet si non corrigé |
|---|---|---|
| 1 | **`vectorstore.persist()` supprimé** | `AttributeError` — la méthode n'existe plus dans ChromaDB 1.x. La persistance est automatique avec `persist_directory`. |
| 2 | **Chunking par règle** au lieu de `RecursiveCharacterTextSplitter(500, 50)` | Un découpage à taille fixe coupe les règles au milieu et perd les métadonnées. Le document expose déjà ses frontières via ses tableaux. |
| 3 | **Préfixes `query:` / `passage:`** | Le modèle E5 est entraîné avec ces préfixes ; sans eux la qualité de récupération chute. Ils doivent être **identiques** entre `ingestion.py` et `mcp_server.py`, sinon les vecteurs ne sont plus dans le même espace. |
| 4 | **Métadonnées retournées** (identifiant, version, chapitre) | Le guide ne retournait que `page_content` : l'agent ne pouvait ni citer la règle, ni détecter une spécification périmée. |
| 5 | **`langchain_huggingface`** au lieu de `langchain_community.embeddings` | Ce dernier est déprécié et émet un avertissement à chaque appel. |
| 6 | **Journalisation de crash** | Sans elle, un échec au démarrage se traduit chez le client par un « Connection closed » sans cause — le scénario qui a coûté huit tentatives sur `borneflow-ctx`. |
| 7 | **Second outil de lookup exact** | Quand l'identifiant est connu, la recherche vectorielle est un détour inutile et faillible. |
| 8 | **Réindexation propre** (`shutil.rmtree`) | Sans purge, les anciens vecteurs cohabitent avec les nouveaux et la recherche renvoie des règles supprimées. |

---

## Points de vigilance

**Temps de démarrage.** Le chargement de `multilingual-e5-large` prend 10 à
20 s à chaque démarrage du serveur (le modèle fait 2,2 Go). Si BOB applique un
délai d'attente court à la connexion MCP, le serveur sera déclaré indisponible.
Si tu observes ce symptôme, bascule sur un modèle plus léger :

```python
MODEL_NAME = "intfloat/multilingual-e5-small"   # ~470 Mo, démarrage ~2 s
```

À changer **dans les deux fichiers simultanément**, puis réindexer.

**Péremption documentaire.** Une SFD obsolète ne produit pas une absence
d'information : elle produit une information fausse et confiante. Les champs
`version` et `date_maj` sont remontés dans chaque résultat pour que l'agent
puisse le signaler — encore faut-il que le prompt système le lui demande
(étape 5).

**Ce qui n'a pas pu être testé ici.** Mon environnement n'a pas accès à
HuggingFace : l'extraction, la chaîne ChromaDB (indexation, persistance,
relecture, lookup par identifiant) et la journalisation de crash ont été
validées à l'exécution avec un embedding de substitution. Le chargement réel du
modèle E5 et la qualité sémantique de la recherche restent à vérifier chez toi,
à l'étape 2.
