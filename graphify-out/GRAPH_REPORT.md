# Graph Report - phase-1-backend-foundation  (2026-07-12)

## Corpus Check
- 49 files · ~21,156 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 153 nodes · 247 edges · 19 communities (17 shown, 2 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 11 edges (avg confidence: 0.77)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9d525d0f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]

## God Nodes (most connected - your core abstractions)
1. `Storage` - 12 edges
2. `serialize()` - 7 edges
3. `get_doc_or_404()` - 7 edges
4. `update_document()` - 6 edges
5. `make_document()` - 6 edges
6. `get_folder_or_404()` - 6 edges
7. `update_folder()` - 6 edges
8. `login()` - 6 edges
9. `get_current_user()` - 6 edges
10. `doc_tags()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `DocumentPatch` --uses--> `Storage`  [INFERRED]
  backend/app/api/documents.py → backend/app/services/storage.py
- `login()` --calls--> `create_access_token()`  [INFERRED]
  backend/app/api/auth.py → backend/app/services/auth.py
- `login()` --calls--> `verify_password()`  [INFERRED]
  backend/app/api/auth.py → backend/app/services/auth.py
- `get_current_user()` --calls--> `decode_token()`  [INFERRED]
  backend/app/api/deps.py → backend/app/services/auth.py
- `delete_document()` --references--> `Storage`  [EXTRACTED]
  backend/app/api/documents.py → backend/app/services/storage.py

## Import Cycles
- None detected.

## Communities (19 total, 2 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.25
Nodes (7): Run migrations in 'offline' mode.      This configures the context with just a U, Run migrations in 'online' mode.      In this scenario we need to create an Engi, run_migrations_offline(), run_migrations_online(), get_settings(), Settings, BaseSettings

### Community 5 - "Community 5"
Cohesion: 0.12
Nodes (18): Chunk, ChunkSource, DocStatus, DocType, Document, DocumentTag, Folder, Job (+10 more)

### Community 7 - "Community 7"
Cohesion: 0.21
Nodes (13): create_tag(), delete_tag(), list_tags(), TagCreate, TagPatch, update_tag(), make_document(), test_delete_removes_file() (+5 more)

### Community 8 - "Community 8"
Cohesion: 0.24
Nodes (4): get_storage(), UUID, Storage, Path

### Community 9 - "Community 9"
Cohesion: 0.47
Nodes (11): delete_document(), doc_tags(), DocumentPatch, get_doc_or_404(), get_document(), list_documents(), UUID, serialize() (+3 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (14): login(), LoginRequest, me(), Session, User, api_error(), get_current_user(), Session (+6 more)

### Community 13 - "Community 13"
Cohesion: 0.35
Nodes (10): create_folder(), delete_folder(), FolderCreate, FolderPatch, get_folder_or_404(), is_descendant(), list_folders(), True if candidate_id is ancestor_id or lies in its subtree. (+2 more)

### Community 14 - "Community 14"
Cohesion: 0.29
Nodes (7): create_user(), main(), hash_password(), make_user(), test_login_ok(), test_login_wrong_password(), test_me_with_token()

## Knowledge Gaps
- **1 isolated node(s):** `origami-backend`
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Storage` connect `Community 8` to `Community 9`?**
  _High betweenness centrality (0.104) - this node is a cross-community bridge._
- **Why does `hash_password()` connect `Community 14` to `Community 12`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **What connects `True if candidate_id is ancestor_id or lies in its subtree.`, `Run migrations in 'offline' mode.      This configures the context with just a U`, `Run migrations in 'online' mode.      In this scenario we need to create an Engi` to the rest of the system?**
  _4 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 5` be split into smaller, more focused modules?**
  _Cohesion score 0.11904761904761904 - nodes in this community are weakly interconnected._
- **Should `Community 12` be split into smaller, more focused modules?**
  _Cohesion score 0.14705882352941177 - nodes in this community are weakly interconnected._