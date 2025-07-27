from typing import Annotated, Optional, Any, Literal
from pydantic import Field

import os
import re
import json
import base64
import pathlib
import urllib

import httpx
from pyzotero import zotero

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import EmbeddedResource, BlobResourceContents

mcp = FastMCP("Zotero", dependencies=["pyzotero",
                                      "mcp[cli]"])

class ZoteroWrapper(zotero.Zotero):
    """ Wrapper for pyzotero client with error handling and User ID selection
    """
    BBT: bool = False
    _bbt_ready_cache: Optional[bool] = None

    def __init__(self):
        try:
            user_id = os.getenv('ZOTERO_USER_ID')
            if user_id is None:
                user_id = 0
            super().__init__(1, 'user', '', local=True) #FIXME: Work around a bug #202 in pyzotero.
            self.library_id = user_id
            
            # Check if BetterBibTeX endpoint exists and verify if the library is ready.
            self.BBT = self._check_better_bibtex_endpoint()
            self._check_bbt_library_ready()
            
        except Exception as e:
            return json.dumps({
                "error": "Failed to initialize Zotero connection.",
                "message": str(e)
            }, indent=2)

    def _check_better_bibtex_endpoint(self) -> bool:
        """Check if the Better BibTeX JSON-RPC endpoint exists"""
        try:
            response = httpx.get("http://localhost:23119/better-bibtex/json-rpc", timeout=1.0)
            return response.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            return False

    def _check_bbt_library_ready(self) -> bool:
        """Check if the Better BibTeX library is ready via JSON-RPC api.ready() call
        
           Caches the result, because this is just delay after Zotero is started.
        """
        if not self.BBT:
            return False
            
        # Return cached result if we've already confirmed it's ready
        if self._bbt_ready_cache is True:
            return True
            
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "api.ready",
                "params": [],
                "id": 1
            }
            
            response = httpx.post(
                "http://localhost:23119/better-bibtex/json-rpc",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                timeout=2.0
            )
            
            if response.status_code == 200:
                result = response.json()
                # Check if we got a valid response with both zotero and betterbibtex versions
                if "result" in result and isinstance(result["result"], dict):
                    ready = "betterbibtex" in result["result"] and "zotero" in result["result"]
                    if ready:
                        # Cache positive result
                        self._bbt_ready_cache = True
                    return ready
            
            return False
            
        except (httpx.RequestError, httpx.TimeoutException, json.JSONDecodeError):
            return False

    def format_item(self, item: dict[str, Any], 
                    include_abstract: bool = True) -> dict[str, Any]:
        """Format a Zotero item into a standardized dictionary"""
        data = item.get('data', {})

        itemType = data.get('itemType', 'Unknown type')
        
        formatted = {
            'title': data.get('title', 'Untitled'),
            'key': data.get('key'),
            'itemType': itemType,
            'date': data.get('date', 'No date'),
        }

        if itemType == 'note':
            formatted.update(self.format_note(item))
        else:
            formatted.update({
                'authors': self.format_creators(data.get('creators', [])),
            })
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
        if 'children' in data:
            formatted['numAttachements'] = data.get("meta", {}).get("numChildren", 0)

        if 'BBT_key' in item:
            formatted['bibtexKey'] = item['BBT_key']

        return formatted

    def format_creators(self, creators: list[dict[str, str]]) -> str:
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

    def format_note(self, item: dict[str, Any]) -> dict[str, Any]:
        data = item.get('data', {})
        formatted = {}

        formatted['parent'] = data.get("parentItem", "")
        formatted['last_modified'] = data.get("dateModified", "")

        note_content = data.get("note", "")

        def simple_html_to_md(html):
            """Convert simple HTML formatting to Markdown
            
            Could be replaced with more advanced code in the future.
            """
            # Handle headings
            for i in range(6, 0, -1):
                html = html.replace(f"<h{i}>", f"{'#' * i} ").replace(f"</h{i}>", "\n\n")
            
            # Handle basic formatting
            html = html.replace("<strong>", "**").replace("</strong>", "**")
            html = html.replace("<b>", "**").replace("</b>", "**")
            html = html.replace("<em>", "*").replace("</em>", "*")
            html = html.replace("<i>", "*").replace("</i>", "*")
            
            # Handle lists
            html = html.replace("<ul>", "\n").replace("</ul>", "\n")
            html = html.replace("<ol>", "\n").replace("</ol>", "\n")
            html = html.replace("<li>", "- ").replace("</li>", "\n")
            
            # Handle paragraphs and line breaks
            html = html.replace("<p>", "").replace("</p>", "\n\n")
            html = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
            
            # Handle links
            html = re.sub(r'<a href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', html)
            
            # Remove any remaining HTML tags
            html = re.sub(r'<[^>]*>', '', html)
            return html.strip()

        formatted['note'] = simple_html_to_md(note_content)
        
        return formatted

    def citation_keys(self, item_keys: list[str]) -> dict[str, str]:
        """
        Fetch citation keys for given item keys using Better BibTeX JSON-RPC API.
        
        Args:
            item_keys: A list of [libraryID]:[itemKey] strings. 
                       If [libraryID] is omitted, assume 'My Library'
        
        Returns:
            dict[string, string] mapping item keys to citation keys
        """
        if not self.BBT:
            raise Exception("Better BibTeX is not available")
            
        if not self._check_bbt_library_ready():
            raise Exception("Better BibTeX library is not ready. Needs some time after starting Zotero.")
            
        if not item_keys:
            return {}
            
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "item.citationkey",
                "params": [item_keys],
                "id": 1
            }
            
            response = httpx.post(
                "http://localhost:23119/better-bibtex/json-rpc",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                timeout=5.0
            )
            
            if response.status_code == 200:
                result = response.json()
                if "result" in result:
                    return result["result"]
                elif "error" in result:
                    raise Exception(f"Better BibTeX error: {result['error']}")
                else:
                    raise Exception("Invalid response from Better BibTeX")
            else:
                raise Exception(f"HTTP error {response.status_code}")
                
        except (httpx.RequestError, httpx.TimeoutException, json.JSONDecodeError) as e:
            raise Exception(f"Failed to fetch citation keys: {str(e)}")

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

# Descriptions for textbased API endpoints.
_zotero_item_type_desc = """
itemType supports Boolean searches. E.g. the following examples are possible to limit or choose the item Type.
 - itemType: book
 - itemType: book || journalArticle (OR)
 - itemType: -attachment (NOT)

Default choice is to exclude attachements
"""

_zotero_tag_type_desc = """
tag supports Boolean searches. E.g. the following examples are possible to limit or choose the item Type.
 - tag: foo
 - tag: foo bar (tag with space)
 - tag: foo&tag=bar (AND)
 - tag: foo bar || bar (OR)
 - tag: -foo (NOT)
 - tag: \\-foo (literal first-character hyphen)

Default choice is empty.
"""

@mcp.tool(description="Returns by default information on the Zotero library containing Research Papers collected by the User." \
                      "Good to call if you dont have a clear understanding yet what to ask from this tool. " \
                      "The returned information can be finetuned by asking for summary, collections, recent items, tags.")
async def get_zotero_information(properties: str = Field(default='summary', # pyright: ignore[reportRedeclaration]
                                                         description='Properties you are interested in getting. By default returns informational summary on the connected library.' \
                                                                     'otherwise provide comma-separated list containing: collections, recent, tags'),
                                 limit: Optional[int] = None,
                                 itemType: str = Field(default='-attachment', 
                                                        description=_zotero_item_type_desc),
                                 context: Optional[Context] = None) -> str:
    """Returns information of the library such as recent items, tags, or available collections
    
    Args:
        properties: Properties you are interested in getting. Comma-separated list, containing: ' \
                    'recent, tags, collections
        limit: Optional how many items to return for each property.
        itemType: Define item types to include/exclude, by default excludes attachments (-attachment).
    """
    try:
        properties : list[str] = [p.strip() for p in properties.split(',')]

        response_data = {}
        
        if 'summary' in properties:
            # FIXME This should give a useful summary to setup the tool.
            response_data['library'] = _get_library_info()
            response_data['groups'] = _get_groups()

            properties = ['recent', 'collections']
            limit = 10

        if 'recent' in properties:
            response_data['recent'] = _get_recent_items(limit=limit, itemType=itemType)
        if 'tags' in properties:
            response_data['tags'] = _get_tags(limit=limit)
        if 'collections' in properties:
            response_data['collections'] = _get_collections(limit=limit)
        return json.dumps(response_data, indent=2)
    
    except Exception as e:
        if context and hasattr(context, '_fastmcp'):
            await context.error(f"Failed to fetch library metadata for: {properties}. Message: {str(e)}")
        
        return json.dumps({
                "error": f"Failed to fetch library metadata for: {properties}. Message: {str(e)}",
                "properties": properties,
        }, indent=2)

def _get_library_info():
    client = _get_zotero_client()
    data: dict[str, Any] = {}
    data['info'] = \
    """ This is a primary Zotero library through the locally running Zotero library. 
    
    Additionally the user can have access to further groups that behave like new libraries. 
    Currently those can not be queried through this tool.

    The recent and collections that are provided are limited to 10 items each, to give you a feeling what you can request.
    """
    data['number_of_entries'] = client.count_items()
    return data

def _get_groups():
    client = _get_zotero_client()
    groups: list[dict[str, Any]] = client.groups() # pyright: ignore[reportAssignmentType]
    data: dict[int, dict] = {}

    for group in groups:
        id = group['id']
        data[id] = {}
        data[id]['name']  = group['data']['name']
        data[id]['number_of_entries'] = group['meta']['numItems']

    return data

def _get_recent_items(limit: Optional[int] = Field(default=10), 
                      itemType: str = Field(default='-attachment', 
                                            description='Define item types to include, by default excludes attachments'),
                     ) -> list[dict[str, Any]] | dict[str, str]:
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
            return {
                    "error": "No recent items found",
                    "suggestion": "Add some items to your Zotero library first"
                   }
            
        formatted_items = [client.format_item(item, include_abstract=False) for item in items]
        return formatted_items
    except ValueError:
        return {
                "error": "Invalid limit parameter",
                "suggestion": "Please provide a valid number for limit"
               }
    
def _get_tags(query: Optional[str] = None, 
              qmode: Optional[Literal["contains"] | Literal["startsWith"]] = Field(default=None, 
                                                                                   description='Searching tags that contain query (`contains`), or start with query (`startsWith`)'),
              limit: Optional[int] = None) -> list[dict[str, Any]] | dict[str, str]:
    """Return tags used in the Zotero library.

        Args:
        limit: Optionally limit how many tags to return.
    """
    client = _get_zotero_client()
    items = client.tags(limit=limit, sort='dateModified')
    if not items:
        return {
                "error": "No tags found",
                "suggestion": "You need to create tags in your library"
               }
    
    return items # pyright: ignore[reportReturnType]

def _get_collections(limit: Optional[int] = None,) -> str:
    """Get all collections in the Zotero library
    
    Args:
        limit: Optional how many items to return.
    """
    client = _get_zotero_client()
    collections = client.collections(limit=limit, sort='dateModified')

    return collections

@mcp.tool(description="Gets all items in a specific Zotero collection.")
async def get_collection_items(collection_key: str, 
                         limit: Optional[int] = None, 
                         context: Optional[Context] = None) -> str:
    """
    Gets all items in a specific Zotero collection
    
    Args:
        collection_key: The collection key/ID
        limit: Optional how many items to return.
    """
    try:
        client = _get_zotero_client()
        items: list = client.collection_items(collection_key, limit=limit) # type: ignore
        if not items:
            return json.dumps({
                "error": "Collection is empty",
                "collection_key": collection_key,
                "suggestion": "Add some items to this collection in Zotero"
            }, indent=2)
            
        formatted_items = [client.format_item(item) for item in items]
        return json.dumps(formatted_items, indent=2)
    except Exception as e:
        if context and hasattr(context, '_fastmcp'):
            await context.error(f"Failed to fetch collection items {collection_key}. Message: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch collection items. Message: {str(e)}",
                "collection_key": collection_key,
        }, indent=2)

@mcp.tool(description="Get detailed information on specific item(s) in the library")
async def get_items_metadata(item_key: Annotated[str, Field(description='Item key(s) to retrieve. Multiple keys are separated by comma.')],
                             include_fulltext: bool = Field(default=False, 
                                                            description='Include fulltext content (default: False)'),
                             include_abstract: bool = Field(default=True, 
                                                            description='Include abstract (default: True)'),
                             include_bibtex: bool   = Field(default=True,
                                                            description='Include BibTeX key (default: True)'),
                             context: Optional[Context] = None) -> str:
    """
    Get detailed information and metadata about specific Zotero item(s) 
    in the library as JSON metadata.
    
    Args:
        item_key: The papers Zotero Key. Can be a comma separated list.
    """ 
    info = None
    item_keys : list[str] = [p.strip() for p in item_key.split(',')]
    
    try:
        client = _get_zotero_client()
        items = {item['key']: item for item in client.get_subset(item_keys)}

        if len(items) == 0:
            return json.dumps({
                "error": "Items not found",
                "missing_keys": list(item_keys),
                "suggestion": "Verify the items exist and you have permission to access them"
            }, indent=2)

        if include_bibtex:
            try:
                bbt_keys = client.citation_keys(list(items.keys()))
                for key, BBT_key in bbt_keys.items():
                    items[key]['BBT_key'] = BBT_key
            except Exception as e:
                await context.warning(f"BBT: {str(e)}")
    
        formatted_items = [client.format_item(item, include_abstract=True) for key, item in items.items()]
        
        if len(items) < len(item_keys):
            missing_keys = set(item_keys) - set(items.keys())
            info = {
                "error": "Some items not found",
                "missing_keys": list(missing_keys),
                "suggestion": "Verify the items exist and you have permission to access them"
            }
        

        if info:
            return json.dumps({'log': info, 'items': formatted_items}, indent=2)
        else:
            return json.dumps(formatted_items, indent=2)
    
    except Exception as e:
        if context and hasattr(context, '_fastmcp'):
            await context.error(f"Failed to fetch item details {item_key}. Message: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch item details. Message: {str(e)}",
                "item_key": item_key,
        }, indent=2)

# Blocked by zotero release 7.1 (fulltext)
# @mcp.tool(description="Get fulltext as indexed by Zotero")
async def get_item_fulltext(item_key: str, context: Optional[Context] = None) -> str:
    """
    Gets the full text content as indexed by Zotero.
    
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
async def get_item_pdf(item_key: str, 
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
                await context.error(f"PDF file not found at {pdf_path} for item {item_key}")
            return json.dumps({
                    "error": "PDF file not found",
                    "item_key": item_key,
                    "path": str(pdf_path),
                    "suggestion": "Check if the PDF file exists in the expected location"
            }, indent=2)
        
    except Exception as e:
        if context and hasattr(context, '_fastmcp'):
            await context.error(f"Failed to fetch PDF: {str(e)}")
        return json.dumps({
                "error": f"Failed to fetch PDF. {str(e)}",
                "item_key": item_key,
        }, indent=2)

@mcp.tool(description="Search the Zotero library for an item.")
async def search_library(query: str, 
                         qmode: Literal["everything"] | Literal["titleCreatorYear"] = Field(default='titleCreatorYear', 
                                                                                            description='Use all field and full text search (`everything`), or only Title, Creator and Year search (`titleCreatorYear`)'),
                         itemType: str = Field(default='-attachment', 
                                               description=_zotero_item_type_desc),
                         tag: Optional[str] = Field(default=None, 
                                          description=_zotero_tag_type_desc),
                         include_abstract: bool = Field(default=False, description='Should search results include the abstract?'),
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
        optional_args = {'tag': tag,
                         'limit': limit}
        items = client.items(q=query, qmode=qmode, itemType=itemType, 
                             **{key: value for key, value in optional_args.items() if value is not None})
        if len(items) < 1:
            return json.dumps({
                "error": "No results found",
                "query": query,
                "suggestion": "Try a different search term or verify your library contains matching items"
            }, indent=2)
            
        formatted_items = [client.format_item(item, include_abstract=include_abstract) for item in items]
        return json.dumps(formatted_items, indent=2)
    except Exception as e:
        if context and hasattr(context, '_fastmcp'):
            await context.error(f"Search failed ({query}). Message: {str(e)}")
        return json.dumps({
                "error": f"Search failed. Message: {str(e)}",
                "query": query,
        }, indent=2)

if __name__ == "__main__":
    # Talk with Zotero once
    client = _get_zotero_client()
    client.creator_fields()
    
    # Initialize and run the server
    mcp.run(transport="streamable-http")