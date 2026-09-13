# Publishing a complete Selvedge release

A release is complete when PyPI, the official MCP Registry, npm, and Smithery
serve the intended version. A successful tag workflow only covers the first two.
Keep a receipt for each registry; record an authentication or upload failure as
pending and continue the remaining independent steps.

## Prepare and verify

Follow the version and validation checklist in [CLAUDE.md](../CLAUDE.md).
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

## Publish Smithery

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
smithery mcp publish ./dist/selvedge-VERSION.mcpb --name masondelan/selvedge
```

Replace `VERSION` with the release version. The explicit file list excludes local
databases, credentials, development environments, and private launch materials.
Verify the upload succeeds and the listing's deployment/version reflects it at
[Smithery](https://smithery.ai/servers/masondelan/selvedge). Its public description
is managed separately from the bundle; inspect it when product positioning changes.

## Close the release

Record the commit, version, registry URLs, bundle checksum, public package version
and pin, and installation/protocol smoke-test results. Treat website deployment
as a separate receipt. Tell the release coordinator which channels are verified
and which remain pending; do not infer completion from a manifest edit alone.
