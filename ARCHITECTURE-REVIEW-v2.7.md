# WhiteBoardOS architecture review v2.7

## Source-of-truth model

`/data/whiteboardos/documents` is authoritative. SQLite is a synchronized catalog/index, not a replacement filesystem.

## Mutation invariants

### Content mutation
Create, edit, import, restore, or externally modify content:

1. filesystem changes
2. base `files` and FTS rows reconcile
3. stale embedding and content-derived graph edges are invalidated
4. metadata becomes pending if its content hash is stale
5. background Qwen metadata is regenerated
6. all-minilm embedding is regenerated
7. sparse graph is rebuilt from fresh state

### Path-only mutation
Rename/move through WhiteBoardOS:

1. filesystem path changes
2. `files.path` and FTS path update with the same stable file ID
3. content hash and semantic embedding remain valid
4. path/folder graph relationships are rebuilt

### Gemini mutations
Gemini never directly edits the filesystem in the full-stack benchmark. It may request bounded WhiteBoardOS actions. The OS validates and executes them through the same catalog mutation methods used by the UI.

`consolidate_files` is intentionally non-destructive: it creates one combined Markdown/text file and leaves sources untouched.

## Hierarchical retrieval

Flat file ranking is augmented by top-level folder scope. Folder names, common human synonyms, and child-vector centroids produce a folder relevance score. Explicit phrases such as `school classes` strongly boost descendants of `Classes/`, while semantic folder centroids provide a smaller general boost.

This solves vague human requests without hard-coding a fixed result count or requiring users to know filesystem paths.

## Dynamic context size

Retrieval defaults to a score-based variable result count, with a cap of 32. Broad/action requests can return a wider set; narrow questions stop earlier at relevance cliffs.

## Warmup policy

The server warms all-minilm after boot and keeps it resident with Ollama configuration. `wb-full-test` does not pay that cost every time. It first retrieves normally; only if the query falls back to the hash provider does it warm Ollama and retry once.

## Safe action routing

The integrated Gemini path exposes only bounded operations:

- `read_file` for retrieved files
- `consolidate_files` for retrieved files
- `batch_move` for simple moves of retrieved files
- `organize_files` to create folders and batch-move retrieved files
- `create_file`

No delete action is exposed in this path. File IDs supplied by Gemini are checked against the retrieved context before mutation.
