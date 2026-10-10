import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import test from 'node:test';
import { sourceHashes } from '../../tools/frontend_provenance.mjs';

const build = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url))).scripts.build;
const packageName = 'node_modules/@tiptap/suggestion';
const version = '3.31.4';

function fixture(t, { state = 'matching', compilerExit = 0, viteExit = 0, hasLock = true } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'evograph manual build '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const write = (path, content, options) => {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), content, options);
  };
  const manifest = {
    private: true,
    scripts: { build },
    dependencies: { '@tiptap/suggestion': version },
  };
  const packages = {
    '': manifest,
    [packageName]: { version, integrity: 'fixture-current' },
    'node_modules/optional-platform': { version: '1.0.0', optional: true },
  };
  write('package.json', JSON.stringify(manifest));
  if (hasLock) write('package-lock.json', JSON.stringify({ packages }));
  if (state !== 'missing') {
    write(`${packageName}/package.json`, JSON.stringify({
      version: state === 'stale-version' ? '3.30.0' : version,
    }));
    const installed = structuredClone(packages);
    if (state === 'stale-integrity') installed[packageName].integrity = 'fixture-old';
    write('node_modules/.package-lock.json', JSON.stringify({ packages: installed }));
  }
  mkdirSync(join(root, 'tools'));
  for (const name of ['check_frontend_dependencies.mjs', 'frontend_provenance.mjs']) {
    copyFileSync(new URL(`../../tools/${name}`, import.meta.url), join(root, 'tools', name));
  }
  // Exercise the repository's npm build script without a compiler, bundler or install.
  for (const [name, label, exitCode] of [
    ['vue-tsc', 'compiler', compilerExit],
    ['vite', 'vite', viteExit],
  ]) {
    const script = `console.log('${label}-sentinel:' + JSON.stringify(process.argv.slice(2)));\n` +
      `process.exitCode = ${exitCode};\n`;
    write(`node_modules/.bin/${name}`, `#!/usr/bin/env node\n${script}`, { mode: 0o755 });
    write(`node_modules/.bin/${name}.cjs`, script);
    write(`node_modules/.bin/${name}.cmd`, `@"${process.execPath}" "%~dp0${name}.cjs" %*\r\n`);
  }
  // Keep npm entirely inside the fixture; do not use personal config or network access.
  write('empty-user.npmrc', '');
  write('empty-global.npmrc', '');
  const env = { PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH || ''}` };
  for (const key of ['SystemRoot', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP']) {
    if (process.env[key]) env[key] = process.env[key];
  }
  const args = [
    '--offline', '--no-audit', '--no-fund', '--update-notifier=false',
    `--userconfig=${join(root, 'empty-user.npmrc')}`,
    `--globalconfig=${join(root, 'empty-global.npmrc')}`,
    `--cache=${join(root, 'npm-cache')}`,
    'run', 'build',
  ];
  const windows = process.platform === 'win32';
  const result = spawnSync(windows ? 'npm.cmd' : 'npm',
    windows ? args.map((arg) => `"${arg}"`) : args,
    { cwd: root, env, encoding: 'utf8', shell: windows, timeout: 10000 });
  assert.ifError(result.error);
  assert.equal(result.signal, null);
  return { root, ...result, output: `${result.stdout}${result.stderr}` };
}

for (const state of ['missing', 'stale-version', 'stale-integrity']) {
  test(`manual build rejects ${state} dependencies before invoking the compiler`, (t) => {
    const result = fixture(t, { state });
    assert.equal(result.status, 1, result.output);
    const issue = state === 'missing' ? 'is missing' : 'differs from package-lock.json';
    assert.ok(result.output.includes(`${packageName} ${issue}`), result.output);
    assert.ok(result.output.includes(`Run npm ci in ${result.root}, then npm run build.`));
    assert.doesNotMatch(result.output, /(?:compiler|vite)-sentinel:/);
    t.diagnostic(`${packageName} ${issue}; npm ci guidance present; exit 1; no compiler/Vite invocation`);
  });
}

test('manual build without a lockfile preserves npm install guidance', (t) => {
  const result = fixture(t, { state: 'missing', hasLock: false });
  assert.equal(result.status, 1, result.output);
  assert.ok(result.output.includes(`Run npm install in ${result.root}, then npm run build.`));
  assert.doesNotMatch(result.output, /(?:compiler|vite)-sentinel:/);
});

for (const [compilerExit, viteExit, expectedExit] of [[0, 0, 0], [2, 0, 2], [0, 7, 7]]) {
  test(`matching dependencies preserve compiler ${compilerExit}/Vite ${viteExit} exit behavior`, (t) => {
    const result = fixture(t, { compilerExit, viteExit });
    assert.equal(result.status, expectedExit, result.output);
    assert.match(result.stdout, /compiler-sentinel:\["--noEmit"\]/);
    if (compilerExit) assert.doesNotMatch(result.output, /vite-sentinel:/);
    else assert.match(result.stdout, /compiler-sentinel:[\s\S]*vite-sentinel:\["build"\]/);
    assert.doesNotMatch(result.output, /Frontend dependencies are missing or out of date/);
    const hashes = sourceHashes(result.root);
    assert.ok(hashes['tools/check_frontend_dependencies.mjs']);
    writeFileSync(join(result.root, 'tools/check_frontend_dependencies.mjs'), '// edited fixture\n');
    assert.notEqual(sourceHashes(result.root)['tools/check_frontend_dependencies.mjs'],
      hashes['tools/check_frontend_dependencies.mjs']);
    t.diagnostic(`compiler --noEmit reached; Vite build ${compilerExit ? 'blocked' : 'reached'}; exit ${expectedExit}`);
  });
}
