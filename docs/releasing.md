# Publishing a complete Selvedge release

A release is complete when its GitHub Release and tag are verified, and PyPI,
the official MCP Registry and npm serve the intended version. These are three
active registries plus GitHub Releases. The tag workflow covers GitHub Releases,
PyPI and the MCP Registry; npm requires separate publication. Keep a receipt for
each destination. Record authentication or upload failures as pending.

Smithery [announced](https://www.smithery.ai/future) retirement of its former
CLI/API/account publishing path effective October 31, 2026, without specifying
a shutdown time zone. Its public directory remains. Legacy publishing docs and
an operational status page do not establish supported post-sunset uploads; no
replacement publishing path has been verified. Record the announced sunset and
unverified publication/version status separately. Do not infer an observed
outage or a new-version publication from the notice or a live directory listing.

## Prepare and verify

Follow the version and validation checklist in [CLAUDE.md](../CLAUDE.md) and the
[engineering standards](engineering-standards.md). Recheck runtime support and
dependency advisories, record the source revision and artifact SHA-256 hashes,
and verify PyPI attestations against the publishing workflow. Preserve the prior
deployment/version as a rollback target; do not overwrite released artifacts.
Work from a clean release checkout. The npm package has its own semver version;
its `pypiVersion` must match the Python release. Every change to that pin requires
a new npm publication, even when the JavaScript launcher is unchanged.

Refresh the Smithery manifest's tool descriptions and schemas from the actual
server before packaging. Use the release environment with its dependencies:

```sh
python - <<'PY'
import asyncio
import json
from pathlib import Path
from selvedge.server import mcp

path = Path('manifest.json')
manifest = json.loads(path.read_text())
manifest['tools'] = [
    tool.model_dump(mode='json', exclude_none=True)
    for tool in asyncio.run(mcp.list_tools())
]
path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
PY
```

Review and commit the metadata change. `tests/test_plugin.py` checks that the
manifest matches the registered tools. Do not move an already published tag or
attempt to overwrite a published npm/PyPI version.

## Publish npm

After PyPI serves the pinned version, run these commands from `npm/`:

```sh
npm pack --dry-run --json
npm publish --access public --registry=https://registry.npmjs.org
npm view selvedge-mcp version pypiVersion dist-tags --json --registry=https://registry.npmjs.org
```

Confirm both the npm version and Python pin, plus the `latest` tag. Complete any
interactive npm authentication in the browser; never put credentials in the repo.
Existing installations pinned to an older version still need an explicit upgrade.

## Retain the legacy bundle artifact

Hand-zip the bundle; do not use `mcpb pack`, whose schema validation is incompatible
with the per-tool `inputSchema` fields used by this listing. From the checkout:

```sh
python - <<'PY'
import json
import zipfile
from pathlib import Path

root = Path.cwd()
version = json.loads((root / 'manifest.json').read_text())['version']
output = root / 'dist' / f'selvedge-{version}.mcpb'
output.parent.mkdir(exist_ok=True)
files = [root / name for name in (
    'manifest.json', 'server.json', 'pyproject.toml', 'README.md',
    'CHANGELOG.md', 'LICENSE', 'glama.json', 'docs/icon.png',
)]
files += list((root / 'selvedge').rglob('*.py'))
files += list((root / 'selvedge').rglob('*.json'))
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
    for path in sorted(files):
        bundle.write(path, path.relative_to(root))
print(output)
PY
```

Attach the `.mcpb` artifact to the GitHub Release and retain its SHA-256 with the
release evidence. The explicit file list excludes local databases, credentials,
development environments and private launch materials. Building this artifact
does not verify replacement-service compatibility or publication to Smithery's
remaining directory. Preserve the [legacy listing](https://smithery.ai/servers/masondelan/selvedge)
as historical context and record its observed state separately.

## Close the release

Record the commit, tag, GitHub Release URL, three registry receipts, artifact
checksums, npm version and Python pin, and installation/protocol smoke results.
Include the Smithery sunset disclosure and any unresolved listing status.
Website deployment remains a separate receipt. Do not infer publication from
manifest edits or artifact creation.
