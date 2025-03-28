from __future__ import annotations
from typing import Optional, List, Dict, Any, Literal
from pydantic import Field

import os
import json
import base64
import pathlib
import urllib

from pyzotero import zotero

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import EmbeddedResource, BlobResourceContents

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
    
    @zotero.retrieve
    def file_url(self, item, **kwargs) -> str:
        """Get the file from a specific item"""
        query_string = "/{t}/{u}/items/{i}/file/view/url".format(
            u=self.library_id, t=self.library_type, i=item.upper()
        )
        return self._build_query(query_string, no_params=True)

_zotero_client = None
def _get_zotero_client() -> ZoteroWrapper:
    global _zotero_client
    if _zotero_client is None:
        _zotero_client = ZoteroWrapper()
    return _zotero_client

@mcp.tool(description="List all collections in the local Zotero library.")
def get_collections(limit: Optional[int] = None, context: Optional[Context] = None) -> str:
    """List all collections in the local Zotero library
    
    Args:
        limit: Optional how many items to return.
    """
    try:
        client = _get_zotero_client()
        collections = client.collections(limit=limit)

        return json.dumps(collections, indent=2)
    except Exception as e:
        if hasattr(context, '_fastmcp'):
            context.error(f"Failed to fetch collections. Message: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch collections. Message: {str(e)}"
        }, indent=2)

@mcp.tool(description="Gets all items in a specific Zotero collection.")
def get_collection_items(collection_key: str, limit: Optional[int] = None, context: Optional[Context] = None) -> str:
    """
    Gets all items in a specific Zotero collection
    
    Args:
        collection_key: The collection key/ID
        limit: Optional how many items to return.
    """
    try:
        client = _get_zotero_client()
        items = client.collection_items(collection_key, limit=limit)
        if not items:
            return json.dumps({
                "error": "Collection is empty",
                "collection_key": collection_key,
                "suggestion": "Add some items to this collection in Zotero"
            }, indent=2)
            
        formatted_items = [client.format_item(item) for item in items]
        return json.dumps(formatted_items, indent=2)
    except Exception as e:
        if hasattr(context, '_fastmcp'):
            context.error(f"Failed to fetch collection items {collection_key}. Message: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch collection items. Message: {str(e)}",
                "collection_key": collection_key,
        }, indent=2)

@mcp.tool(description="Get detailed information about a specific item in the library")
def get_item_details(item_key: str, context: Optional[Context] = None) -> str:
    """
    Get detailed information about a specific item in the library
    
    Args:
        item_key: The paper's item key/ID
    """
    try:
        client = _get_zotero_client()
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
        if hasattr(context, '_fastmcp'):
            context.error(f"Failed to fetch item details {item_key}. Message: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch item details. Message: {str(e)}",
                "item_key": item_key,
        }, indent=2)

# Blocked by zotero release 7.1 (fulltext)
# @mcp.tool(description="Get fulltext as indexed by Zotero")
def get_item_fulltext(item_key: str, context: Optional[Context] = None) -> str:
    """
    Gets the full text content as indexed by Zotero.
    There can be no fulltext.
    
    Args:
        item_key: The paper's item key/ID
    """
    try:
        client = _get_zotero_client()
        fulltext = client.fulltext_item(item_key)
        if not fulltext:
            return json.dumps({
                "error": "No fulltext found",
                "suggestion": "You need to index this file."
            }, indent=2)
        
        return json.dumps(fulltext, indent=2)
    except Exception as e:
        if hasattr(context, '_fastmcp'):
            context.error(f"Retrieving fulltext failed. Message: {str(e)}")
        return json.dumps({
                "error": f"Retrieving fulltext failed. Message: {str(e)}",
                "item_key": item_key,
        }, indent=2)

# FIXME: Misses way to provide PDF to Claude
# @mcp.tool(description="Retrieve PDF for item in the library")
def get_item_pdf(item_key: str, 
                 attachment_index: int = Field(default=0), 
                 context: Optional[Context] = None) -> EmbeddedResource | str:
    """
    Get the PDF content for a specific paper.
    This returns the first PDF that is an attachement by default.
    
    Args:
        item_key: The paper's item key/ID
        attachement_index: Look at attachement with index (Default 0)
    """
    try:
        client = _get_zotero_client()
        children = client.children(item_key)
        pdf_attachments = [
            {
                'key': item['key'],
                'title': item['data'].get('title', 'Untitled'),
                'filename': item['data'].get('filename', 'Unknown'),
                'index': idx
            }
            for idx, item in enumerate(children)
            if item['data']['itemType'] == 'attachment' 
            and item['data'].get('contentType') == 'application/pdf'
        ]
        if len(pdf_attachments) == 0:
            return json.dumps({
                    "error": f"No PDF attachements found.",
                    "item_key": item_key,
                    "suggestion": "Check if this item has an attached PDF"
            }, indent=2)
        elif attachment_index >= len(pdf_attachments):
            return json.dumps({
                    "error": f"Invalid attachment index {attachment_index}",
                    "item_key": item_key,
                    "available_attachments": pdf_attachments,
                    "suggestion": f"Choose an index between 0 and {len(pdf_attachments)-1}"
                    }, indent=2)
        
        selected_attachment = pdf_attachments[attachment_index]
        pdf_uri = urllib.parse.unquote(client.file_url(selected_attachment['key']), encoding='utf-8', errors='replace')
        parsed_uri = urllib.parse.urlparse(pdf_uri)
        pdf_path = pathlib.Path(parsed_uri.path.lstrip('/'))
        try:
            with pdf_path.open('rb') as fp:
                pdf_content = fp.read()
                # pdf_resource = BlobResourceContents(
                #     uri=f"zotero://items/{item_key}/pdf", 
                #     mimeType="application/pdf", 
                #     blob=base64.b64encode(pdf_content).decode())
                # return EmbeddedResource(type='resource', resource=pdf_resource)
                return json.dumps({
                    "type": "resource",
                    "resource": {
                        "uri": f"zotero://items/{item_key}/pdf",
                        "mimeType": "application/pdf",
                        "blob": base64.b64encode(pdf_content).decode()
                    }
                }, indent=2)
            
        except FileNotFoundError:
            if hasattr(context, '_fastmcp'):
                context.error(f"PDF file not found at {pdf_path} for item {item_key}")
            return json.dumps({
                    "error": "PDF file not found",
                    "item_key": item_key,
                    "path": str(pdf_path),
                    "suggestion": "Check if the PDF file exists in the expected location"
            }, indent=2)
        
    except Exception as e:
        if hasattr(context, '_fastmcp'):
            context.error(f"Failed to fetch PDF: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch PDF. {str(e)}",
                "item_key": item_key,
        }, indent=2)
    
@mcp.tool(description="Get tags used in the Zotero library")
def get_tags(limit: Optional[int] = None, context: Optional[Context] = None) -> str:
    """Return tags used in the Zotero library.

        Args:
        limit: Optionally limit how many tags to return.
    """
    try:
        client = _get_zotero_client()
        items = client.tags(limit=limit)
        if not items:
            return json.dumps({
                "error": "No tags found",
                "suggestion": "You need to create tags in your library"
            }, indent=2)
        
        return json.dumps(items, indent=2)
    except Exception as e:
        if hasattr(context, '_fastmcp'):
            context.error(f"Retrieving tags failed. Message: {str(e)}")
        return json.dumps({
                "error": f"Retrieving tags failed. Message: {str(e)}"
        }, indent=2)

@mcp.tool(description="Get recently added items to your library")
def get_recent(limit: Optional[int] = Field(default=10), 
               itemType: str = Field(default='-attachment', 
                                     description='Define item types to include, by default excludes attachments'), 
               context: Optional[Context] = None) -> str:
    """Get recently added items (this by default excludes attachements) to your library
    
    Args:
        limit: Number of items to return (default: 10)
        itemType: Define item types to include, by default excludes attachments (default: -attachment)
    """
    try:
        client = _get_zotero_client()
        # Convert string limit to int and apply constraints
        limit_int = min(int(limit or 10), 100)
        
        items = client.items(limit=limit_int,
                             itemType=itemType,
                             sort='dateAdded', 
                             direction='desc',
                             )
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
        if hasattr(context, '_fastmcp'):
            context.error(f"Failed to fetch recent items: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch recent items: {str(e)}"
        }, indent=2)

@mcp.tool(description="Search the Zotero library for an item.")
def search_library(query: str, 
                   qmode: Literal["everything"] | Literal["titleCreatorYear"] = Field(default='titleCreatorYear', 
                                                                                      description='Use all field and full text search (`everything`), or only Title, Creator and Year search (`titleCreatorYear`)') ,
                   itemType: str = '-attachment',
                   limit: Optional[int] = None,
                   context: Optional[Context] = None) -> str:
    """
    Search the Zotero library for an item.
    
    Args:
        query: Search query
        qmode: Query mode (`titleCreatorYear` or `everything` (default))
        itemType: Configuration on items to search, (default no attachements).
        limit: How many items to return (default unlimited)
    """
    if not query.strip():
        return json.dumps({
            "error": "Search query is required"
        }, indent=2)
        
    try:
        client = _get_zotero_client()
        items = client.items(q=query, qmode=qmode, itemType=itemType, limit=limit)
        if len(items) < 1:
            return json.dumps({
                "error": "No results found",
                "query": query,
                "suggestion": "Try a different search term or verify your library contains matching items"
            }, indent=2)
            
        formatted_items = [client.format_item(item, include_abstract=False) for item in items]
        return json.dumps(formatted_items, indent=2)
    except Exception as e:
        if hasattr(context, '_fastmcp'):
            context.error(f"Search failed ({query}). Message: {str(e)}")
        return json.dumps({
                "error": f"Search failed. Message: {str(e)}",
                "query": query,
        }, indent=2)

if __name__ == "__main__":
    # Talk with Zotero once
    client = _get_zotero_client()
    client.creator_fields()
    
    # Initialize and run the server
    mcp.run(transport='stdio')