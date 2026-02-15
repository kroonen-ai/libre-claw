"""Memory integration for Libre Claw.

HTTP client for ChromaDB semantic memory search and storage.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx


class MemoryClient:
    """HTTP client for ChromaDB memory server."""

    def __init__(
        self,
        url: str = "http://localhost:8420",
        collection_name: str = "libre_claw_memories",
    ):
        self.url = url.rstrip("/")
        self.collection_name = collection_name
        self._client = httpx.Client(timeout=30.0)

    def is_available(self) -> bool:
        try:
            response = self._client.get(f"{self.url}/api/v1/heartbeat")
            return response.status_code == 200
        except Exception:
            return False

    def search(
        self,
        query: str,
        n_results: int = 10,
        where: Optional[Dict[str, Any]] = None,
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search memories by semantic similarity."""
        collection = collection or self.collection_name
        try:
            # Get or create collection
            col_response = self._client.post(
                f"{self.url}/api/v1/collections",
                json={"name": collection, "get_or_create": True},
            )
            col_data = col_response.json()
            col_id = col_data.get("id")
            if not col_id:
                return []

            payload: Dict[str, Any] = {
                "query_texts": [query],
                "n_results": n_results,
            }
            if where:
                payload["where"] = where

            response = self._client.post(
                f"{self.url}/api/v1/collections/{col_id}/query",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            documents = data.get("documents", [[]])[0]
            ids = data.get("ids", [[]])[0]
            distances = data.get("distances", [[]])[0]
            metadatas = data.get("metadatas", [[]])[0]

            for i, doc in enumerate(documents):
                results.append({
                    "id": ids[i] if i < len(ids) else "",
                    "document": doc,
                    "distance": distances[i] if i < len(distances) else 0,
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                })
            return results

        except Exception as e:
            print(f"Memory search error: {e}")
            return []

    def add(
        self,
        document: str,
        metadata: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> Optional[str]:
        """Add a memory to the collection."""
        collection = collection or self.collection_name
        import uuid

        if not doc_id:
            doc_id = str(uuid.uuid4())

        try:
            col_response = self._client.post(
                f"{self.url}/api/v1/collections",
                json={"name": collection, "get_or_create": True},
            )
            col_data = col_response.json()
            col_id = col_data.get("id")
            if not col_id:
                return None

            payload: Dict[str, Any] = {
                "documents": [document],
                "ids": [doc_id],
            }
            if metadata:
                payload["metadatas"] = [metadata]

            response = self._client.post(
                f"{self.url}/api/v1/collections/{col_id}/add",
                json=payload,
            )
            response.raise_for_status()
            return doc_id

        except Exception as e:
            print(f"Memory add error: {e}")
            return None

    def close(self) -> None:
        self._client.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class MemoryManager:
    """High-level memory management."""

    def __init__(
        self,
        url: str = "http://localhost:8420",
        collection_name: str = "libre_claw_memories",
    ):
        self.client = MemoryClient(url, collection_name)

    def remember(
        self,
        content: str,
        memory_type: str = "general",
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
    ) -> bool:
        metadata: Dict[str, Any] = {
            "type": memory_type,
            "importance": importance,
            "timestamp": datetime.now().isoformat(),
        }
        if tags:
            metadata["tags"] = ",".join(tags)

        result = self.client.add(document=content, metadata=metadata)
        return result is not None

    def recall(
        self,
        query: str,
        memory_type: Optional[str] = None,
        min_importance: float = 0.0,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        where: Optional[Dict[str, Any]] = None
        if memory_type:
            where = {"type": memory_type}
        # ChromaDB doesn't support $gte on float easily, filter client-side
        results = self.client.search(query=query, n_results=limit, where=where)
        if min_importance > 0:
            results = [
                r for r in results
                if r.get("metadata", {}).get("importance", 0) >= min_importance
            ]
        return results
