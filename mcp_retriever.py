# mcp_retriever.py
from fastapi import FastAPI
from pydantic import BaseModel
import chromadb
from sentence_transformers import SentenceTransformer

app = FastAPI()
client = chromadb.Client()
collection = client.get_collection("codebase")
embed_model = SentenceTransformer("all-MiniLM-L6-v2")

class Query(BaseModel):
    q: str
    k: int = 4

@app.post("/search")
def search(q: Query):
    q_emb = embed_model.encode(q.q).tolist()
    results = collection.query(query_embeddings=[q_emb], n_results=q.k)
    # results: documents, metadatas, ids
    hits = []
    for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
        # optionally summarize chunk server-side (one short sentence)
        snippet = doc[:1000]
        hits.append({"path": meta.get("path"), "snippet": snippet})
    return {"hits": hits}

# uvicorn mcp_retriever:app --host 127.0.0.1 --port 9002