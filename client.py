"""Fallback CLI for existing sessions whose MCP catalog has not reloaded."""
import argparse
import asyncio
import json
import sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('tool',nargs='?',default='list')
    parser.add_argument('--json-file',help='Arguments JSON file; use - to read stdin')
    parser.add_argument('--url',default='http://127.0.0.1:7331/mcp')
    args=parser.parse_args()
    data={}
    if args.json_file:
        if args.json_file=='-': data=json.load(sys.stdin)
        else:
            with open(args.json_file) as f:data=json.load(f)
    async with streamable_http_client(args.url) as (read,write,_):
        async with ClientSession(read,write) as session:
            await session.initialize()
            if args.tool=='list':
                result=await session.list_tools()
                print(json.dumps([{'name':t.name,'description':t.description,'inputSchema':t.inputSchema} for t in result.tools],indent=2))
            else:
                result=await session.call_tool(args.tool,data)
                if result.isError:
                    print(result.model_dump_json(),file=sys.stderr)
                    raise SystemExit(1)
                print(json.dumps(result.structuredContent or json.loads(result.content[0].text),indent=2))

if __name__=='__main__': asyncio.run(main())
