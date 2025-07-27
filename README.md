# Zotero MCP Connector

A Model Control Protocol (MCP) connector for integrating your local Zotero with Claude.  
This enables direct read access to your local Zotero library through Claude's Desktop interface.
It depends on the ability to access a local web-api in Zotero 7.

This was inspired by a repository using Node.js and the web api: [mcp-zotero](https://github.com/kaliaboi/mcp-zotero).  
This builds on the shoulders of the fantastic [pyzotero](https://github.com/urschrei/pyzotero) library.

## Installation

### Run from local code
Information about Claude Desktop interacting with MCPs can be found [here](https://modelcontextprotocol.io/quickstart/user).

1. Use `uv`. Installation instructions can be found [here](https://docs.astral.sh/uv/getting-started/installation/).

2. Checkout the git project to local space and activate the virtual environment inside:
```bash
git clone https://github.com/gyger/mcp-pyzotero.git
cd mcp-pyzotero
uv sync
```

3. Enable the local API in Zotero 7:
   ![Zotero Local API Settings](assets/LocalAPISettings.png)

4. Add the server to your local Claude installation:
```bash
uv run mcp install zotero.py
```

### Run encapsulated with uvx
Edit the configuration for your Claude Desktop softare in the file.

    - macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
    - Windows: %APPDATA%\Claude\claude_desktop_config.json

and add the Zotero entry
{
    "mcpServers": {
        "Zotero": {
            "command": "uvx",
            "args": ["--from", "git+https://github.com/gyger/mcp-pyzotero.git", 
                     "--with", "mcp[cli]",
                     "--with", "pyzotero",
                     "mcp", "run", "zotero.py"
                    ],
        }
    }
}

## Configuration

The connector is configured to work with local Zotero installations and currently only `user` libraries are supported. 
By default it uses the userid `0`, but you can also set the environment variable `ZOTERO_USER_ID` if needed:

```bash
uv run mcp install zotero.py -v ZOTERO_USER_ID=0
```

## Available Functions

### Available tools
- `get_library_metadata()`: Lists properties about your library e.g. collections, recent items or tags.
- `get_collection_items(collection_key)`: Get all items in a specific collection
- `get_items_metadata(item_key)`: Get detailed information about specific paper(s), including abstract.
- `search_library(query, mode)`: Search your Zotero library, with two possible modes: everything or titleCreatorYear.

This functionality should be extended in the future.

## Requirements

- Python 3.10+
  - pyzotero
  - mcp[cli]
- Local Zotero installation

## Contributing

Contributions are welcome! Please visit the [GitHub repository](https://github.com/gyger/mcp-pyzotero) to:
- Report issues
- Submit pull requests
- Suggest improvements

## License

MIT