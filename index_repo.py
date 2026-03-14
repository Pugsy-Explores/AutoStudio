# index_repo.py
from pathlib import Path
from sentence_transformers import SentenceTransformer
import chromadb
import json, hashlib

EMB_MODEL = "all-mpnet-base-v2"   # small & fast; swap for better open models
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    out=[]
    i=0
    while i < len(text):
        out.append(text[i:i+size])
        i += size - overlap
    return out

def make_id(path, idx):
    h = hashlib.sha1(f"{path}-{idx}".encode()).hexdigest()
    return f"{path.name}-{idx}-{h[:8]}"

def index_repo(root="."):
    model = SentenceTransformer(EMB_MODEL)
    client = chromadb.Client()
    coll = client.get_collection("codebase", embedding_function=None)
    if coll is None:
        coll = client.create_collection("codebase")
    files = list(Path(root).rglob("*.py"))
    docs=[]; embs=[]
    meta=[]
    for f in files:
        text = f.read_text(encoding="utf-8")
        chunks = chunk_text(text)
        for i,c in enumerate(chunks):
            docs.append(c)
            emb = model.encode(c).tolist()
            embs.append(emb)
            meta.append({"path": str(f.relative_to(root)), "chunk_idx": i})
    coll.add(documents=docs, embeddings=embs, metadatas=meta, ids=[make_id(Path(m["path"]),m["chunk_idx"]) for m in meta])
    print("Indexed", len(docs), "chunks")

if __name__=="__main__":
    index_repo(".")