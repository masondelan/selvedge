#!/usr/bin/env node
/**
 * npx shim for the Selvedge MCP server.
 *
 * Selvedge is a Python package (https://pypi.org/project/selvedge/) whose
 * MCP server ships as the `selvedge-server` console script. This shim lets
 * Node-only environments wire it up as `npx -y selvedge-mcp` by delegating
 * to whichever Python runner is available, in order:
 *
 *   1. uvx              -> uvx --from selvedge==<version> selvedge-server
 *   2. pipx             -> pipx run --spec selvedge==<version> selvedge-server
 *   3. selvedge-server  -> an existing pip/pipx install already on PATH
 *
 * If none exist, it prints install guidance to stderr and exits 1.
 *
 * Invariants:
 *   - NOTHING is ever written to stdout by this shim — stdout belongs to the
 *     MCP stdio protocol. All diagnostics go to stderr.
 *   - No npm dependencies; plain Node >= 18.
 *   - Deterministic: the PyPI version is pinned to package.json's
 *     `pypiVersion` (the four-segment PEP 440 server version, which npm's
 *     semver `version` field can't express) unless SELVEDGE_VERSION
 *     overrides it ("latest" unpins).
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const PKG = require(path.join(__dirname, "..", "package.json"));

/**
 * Resolve the PyPI requirement spec to run.
 *
 * Defaults to `selvedge==<pypiVersion>` so `npx selvedge-mcp` is
 * reproducible. `pypiVersion` is a package.json field distinct from the
 * npm `version` on purpose: the Python package uses four-segment PEP 440
 * versions (e.g. 0.3.9.2) that npm's semver `version` field can't hold,
 * so the shim's own npm version and the server version it pins are two
 * separate lines, and the pin comes from `pypiVersion`. Falls back to the
 * npm `version` when `pypiVersion` is absent (older shims). Set
 * SELVEDGE_VERSION to an exact version to override, or to "latest" to
 * unpin.
 *
 * @returns {string} a pip requirement spec, e.g. "selvedge==0.3.9.2".
 */
function pypiSpec() {
  const v = (process.env.SELVEDGE_VERSION || PKG.pypiVersion || PKG.version).trim();
  return v === "latest" ? "selvedge" : `selvedge==${v}`;
}

/**
 * Return the absolute path of `cmd` if it exists on PATH, else null.
 *
 * Pure filesystem scan — never executes anything, so probing for
 * `selvedge-server` can't accidentally boot the server. Honors PATHEXT on
 * Windows (uvx/pipx/selvedge-server all install as .exe there).
 *
 * @param {string} cmd bare command name, without extension.
 * @returns {string|null} resolved absolute path, or null when not found.
 */
function findOnPath(cmd) {
  const dirs = (process.env.PATH || "").split(path.delimiter).filter(Boolean);
  const exts =
    process.platform === "win32"
      ? (process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean)
      : [""];
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = path.join(dir, cmd + ext.toLowerCase());
      try {
        fs.accessSync(candidate, fs.constants.X_OK);
        if (fs.statSync(candidate).isFile()) return candidate;
      } catch {
        /* not here — keep scanning */
      }
    }
  }
  return null;
}

/**
 * Exec the chosen runner with inherited stdio and mirror its exit.
 *
 * stdio: "inherit" hands the MCP stdin/stdout streams straight to the
 * Python server; SIGINT/SIGTERM are forwarded so MCP hosts can shut the
 * server down cleanly through the shim.
 *
 * @param {string} cmd resolved absolute path of the runner.
 * @param {string[]} args argv for the runner.
 * @returns {void}
 */
function run(cmd, args) {
  // Node >= 18.20 refuses to spawn .bat/.cmd without a shell (CVE-2024-27980).
  // uvx/pipx are .exe on Windows so this rarely triggers; args here contain
  // no spaces or shell metacharacters, so shell mode is safe when it does.
  const needsShell = process.platform === "win32" && /\.(cmd|bat)$/i.test(cmd);
  const child = spawn(cmd, args, { stdio: "inherit", shell: needsShell });

  for (const sig of ["SIGINT", "SIGTERM"]) {
    process.on(sig, () => {
      try {
        child.kill(sig);
      } catch {
        /* child already gone */
      }
    });
  }

  child.on("error", (err) => {
    process.stderr.write(`selvedge-mcp: failed to launch ${cmd}: ${err.message}\n`);
    process.exit(1);
  });
  child.on("exit", (code, signal) => {
    process.exit(signal ? 1 : code === null ? 1 : code);
  });
}

/**
 * Print install guidance to stderr and exit 1.
 *
 * @param {string} spec the pip requirement spec we tried to run.
 * @returns {never} process exits.
 */
function fail(spec) {
  process.stderr.write(
    [
      "selvedge-mcp: no way to run the Selvedge MCP server was found on PATH.",
      "",
      "Tried, in order: uvx, pipx, selvedge-server.",
      "Selvedge is a Python package (Python >= 3.10): https://pypi.org/project/selvedge/",
      "",
      "Fix with ONE of:",
      `  1. Install uv   (https://docs.astral.sh/uv/)  — this shim will run: uvx --from ${spec} selvedge-server`,
      `  2. Install pipx (https://pipx.pypa.io/)       — this shim will run: pipx run --spec ${spec} selvedge-server`,
      `  3. pip install "${spec}"                       — puts selvedge-server on PATH`,
      "     (equivalent module form: python3 -m selvedge.server)",
      "",
    ].join("\n")
  );
  process.exit(1);
}

/**
 * Entry point: pick the first available runner and delegate to it.
 *
 * @returns {void}
 */
function main() {
  const args = process.argv.slice(2);
  const spec = pypiSpec();

  const uvx = findOnPath("uvx");
  if (uvx) return run(uvx, ["--from", spec, "selvedge-server", ...args]);

  const pipx = findOnPath("pipx");
  if (pipx) return run(pipx, ["run", "--spec", spec, "selvedge-server", ...args]);

  const existing = findOnPath("selvedge-server");
  if (existing) {
    if (spec !== "selvedge") {
      process.stderr.write(
        `selvedge-mcp: using existing selvedge-server at ${existing} ` +
          `(the ${spec} pin does not apply to a pre-installed binary).\n`
      );
    }
    return run(existing, args);
  }

  fail(spec);
}

main();
