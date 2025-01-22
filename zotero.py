from __future__ import annotations
from typing import Optional, List, Dict, Any

import os
import json
import base64

from pyzotero import zotero

from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("Zotero", dependencies=["pyzotero",
                                      "mcp[cli]"])

class ZoteroWrapper(zotero.Zotero):
    """Wrapper for pyzotero client with error handling"""
    def __init__(self):
        try:
            user_id = os.getenv('ZOTERO_USER_ID')
            if user_id is None:
                user_id = 0
            super().__init__(1, 'user', '', local=True) #FIXME: Work around a bug #202 in pyzotero.
            self.library_id = user_id
        except Exception as e:
            return json.dumps({
                "error": "Failed to initialize Zotero connection.",
                "message": str(e)
            }, indent=2)
    
    def format_creators(self, creators: List[Dict[str, str]]) -> str:
        """Format creator names into a string"""
        names = []
        for creator in creators:
            name_parts = []
            if creator.get('firstName'):
                name_parts.append(creator['firstName'])
            if creator.get('lastName'):
                name_parts.append(creator['lastName'])
            if name_parts:
                names.append(' '.join(name_parts))
        return ', '.join(names) or "No authors listed"

    def format_item(self, item: Dict[str, Any], include_abstract: bool = True) -> Dict[str, Any]:
        """Format a Zotero item into a standardized dictionary"""
        data = item.get('data', {})
        formatted = {
            'title': data.get('title', 'Untitled'),
            'authors': self.format_creators(data.get('creators', [])),
            'date': data.get('date', 'No date'),
            'key': data.get('key'),
            'itemType': data.get('itemType', 'Unknown type'),
        }
        
        if include_abstract:
            formatted['abstractNote'] = data.get('abstractNote', 'No abstract available')
            
        if 'DOI' in data:
            formatted['doi'] = data['DOI']
        if 'url' in data:
            formatted['url'] = data['url']
        if 'publicationTitle' in data:
            formatted['publicationTitle'] = data['publicationTitle']
        if 'tags' in data:
            formatted['tags'] = [t.get('tag') for t in data.get('tags', []) if t.get('tag')]
            
        return formatted

@mcp.resource("zotero://collections")
def get_collections() -> str:
    """List all collections in your Zotero library"""
    try:
        client = ZoteroWrapper()
        collections = client.collections()
        return json.dumps(collections, indent=2)
    except Exception as e:
        return json.dumps({
            "error": f"Failed to fetch collections: {str(e)}"
        }, indent=2)

@mcp.resource("zotero://collections/{collection_key}/items")
def get_collection_items(collection_key: str) -> str:
    """
    Get all items in a specific collection
    
    Args:
        collection_key: The collection key/ID
    """
    try:
        client = ZoteroWrapper()
        items = client.collection_items(collection_key)
        if not items:
            return json.dumps({
                "error": "Collection is empty",
                "collection_key": collection_key,
                "suggestion": "Add some items to this collection in Zotero"
            }, indent=2)
            
        formatted_items = [client.format_item(item) for item in items]
        return json.dumps(formatted_items, indent=2)
    except Exception as e:
        return json.dumps({
            "error": f"Failed to fetch collection items: {str(e)}",
            "collection_key": collection_key
        }, indent=2)

@mcp.resource("zotero://items/{item_key}")
def get_item_details(item_key: str) -> str:
    """
    Get detailed information about a specific paper
    
    Args:
        item_key: The paper's item key/ID
    """
    try:
        client = ZoteroWrapper()
        item = client.item(item_key)
        if not item:
            return json.dumps({
                "error": "Item not found",
                "item_key": item_key,
                "suggestion": "Verify the item exists and you have permission to access it"
            }, indent=2)
            
        formatted_item = client.format_item(item, include_abstract=True)
        return json.dumps(formatted_item, indent=2)
    except Exception as e:
        return json.dumps({
            "error": f"Failed to fetch item details: {str(e)}",
            "item_key": item_key
        }, indent=2)

@mcp.resource("zotero://items/{item_key}/pdf", mime_type="application/pdf")
def get_item_pdf(item_key: str) -> bytes:
    """
    Get the PDF contents of a specific paper if available
    
    Args:
        item_key: The paper's item key/ID
    """
    try:
        client = ZoteroWrapper()
        pdf_content = client.file(item_key)
        if not pdf_content:
            return json.dumps({
                    "error": "No PDF found",
                    "item_key": item_key,
                    "suggestion": "Check if this item has an attached PDF"
            }, indent=2)
               
        return pdf_content

    except Exception as e:
        return json.dumps({
            "error": f"Failed to fetch PDF: {str(e)}",
            "item_key": item_key
        }, indent=2)

@mcp.resource("zotero://tags")
def get_tags() -> str:
    """Return all tags in the Zotero library"""
    try:
        client = ZoteroWrapper()
        items = client.tags()
        if not items:
            return json.dumps({
                "error": "No tags found",
                "suggestion": "You need to create tags in your library"
            }, indent=2)
        
        return json.dumps(items, indent=2)
    except Exception as e:
        # Since resources don't have access to Context object, 
        # we'll return the error as part of the response
        return json.dumps({
            "error": f"Listing tags failed: {str(e)}"
        }, indent=2)

@mcp.resource("zotero://recent/items/{limit}")
def get_recent_items(limit = "10") -> str:
    """Get recently added papers to your library
    
    Args:
        limit: Number of papers to return (default 10, max 100)
    """
    try:
        client = ZoteroWrapper()
        # Convert string limit to int and apply constraints
        limit_int = min(int(limit or 10), 100)
        
        items = client.items(limit=limit_int, sort='dateAdded', direction='desc')
        if not items:
            return json.dumps({
                "error": "No recent items found",
                "suggestion": "Add some items to your Zotero library first"
            }, indent=2)
            
        formatted_items = [client.format_item(item, include_abstract=False) for item in items]
        return json.dumps(formatted_items, indent=2)
    except ValueError:
        return json.dumps({
            "error": "Invalid limit parameter",
            "suggestion": "Please provide a valid number for limit"
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "error": f"Failed to fetch recent items: {str(e)}"
        }, indent=2)

@mcp.tool(description="Search the local Zotero library of the user.")
def search_library(query: str, *, ctx: Context) -> str:
    """
    Search your entire Zotero library
    
    Args:
        query: Search query
    """
    if not query.strip():
        return json.dumps({
            "error": "Search query is required"
        }, indent=2)
        
    try:
        client = ZoteroWrapper()
        items = client.items(q=query)
        if not items:
            return json.dumps({
                "error": "No results found",
                "query": query,
                "suggestion": "Try a different search term or verify your library contains matching items"
            }, indent=2)
            
        formatted_items = [client.format_item(item, include_abstract=False) for item in items]
        return json.dumps(formatted_items, indent=2)
    except Exception as e:
        ctx.error(f"Search failed ({query}). Message: {str(e)}")
        return


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')