"""Adapt an existing JSON API to FOS Knowledge Feed v1. Edit map_record for your API.

stdout contains ONLY the batch JSON; errors go to stderr and exit nonzero.
Run externally via cron, then pipe to feed_client.py or manage.py feed_import.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
from urllib.request import Request,build_opener
from feed_client import NoRedirect,MAX_BYTES,SCHEMA


def map_record(row):
    # Keep upstream IDs stable. Never use a timestamp/random UUID as the item ID.
    item={'id':str(row['id']),'title':row['title'],'content':row['markdown'],'format':'markdown'}
    for name in ('source_url','updated_at','metadata'):
        if name in row:item[name]=row[name]
    return item


def main():
    url=os.environ['UPSTREAM_API_URL']
    headers={'Accept':'application/json'}
    if os.environ.get('UPSTREAM_API_AUTH'):headers['Authorization']=os.environ['UPSTREAM_API_AUTH']
    with build_opener(NoRedirect()).open(Request(url,headers=headers),timeout=30) as response:
        raw=response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Upstream response too large; paginate in your adapter.')
    upstream=json.loads(raw)
    rows=upstream if isinstance(upstream,list) else upstream['items']
    batch={'schema':SCHEMA,'items':[map_record(row) for row in rows],'deleted':[]}
    # Populate deleted only from explicit upstream tombstones, never missing rows.
    if isinstance(upstream,dict):batch['deleted']=[str(key) for key in upstream.get('deleted',[])]
    fingerprint=hashlib.sha256(json.dumps(batch,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    batch['batch_id']='api-'+fingerprint
    sys.stdout.write(json.dumps(batch,ensure_ascii=False)+'\n')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        # URLs and credentials are deliberately excluded from error output.
        print('Adapter failed: '+type(exc).__name__,file=sys.stderr);sys.exit(1)
